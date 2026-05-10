"""
BaseAgent — 精简的 Agent 基类

职责：
- LLM 初始化
- LangGraph workflow 构建（generate → execute → generate 循环）
- 代码执行（Python / Bash）
- 消息解析（支持混合模式：纯文本 + 代码执行）

不包含：
- 工具注册 / 检索
- 数据湖 / 软件库
- Know-how / Skills
- UI 相关逻辑

Concurrency note:
    BaseAgent 本身是 stateless 的。每次 run*() 调用都构造新的输入，LangGraph
    不使用 checkpointer —— 对话历史由调用方（例如 ConversationStore）显式管理。
    这样多个并发请求不会通过 thread_id 互相串扰。
"""

import builtins
import re
from collections.abc import Generator
from threading import Lock
from typing import Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from base_CAi.llm import SourceType, get_llm

from .execution import (
    inject_custom_functions,
    run_bash_script,
    run_python_repl,
    run_with_timeout,
)


class AgentState(TypedDict):
    messages: list[BaseMessage]
    next_step: str | None


class BaseAgent:
    """
    精简 Agent 基类。

    支持混合交互模式：
    - Agent 可以直接输出文本回复（思考、解释、讨论）
    - Agent 可以用 <execute>...</execute> 执行代码
    - Agent 用 <done/> 标记任务完成（可选）
    """

    def __init__(
        self,
        llm: str | None = None,
        source: SourceType | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: int = 600,
        system_prompt: str | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self._system_prompt = system_prompt or self._default_system_prompt()

        # Init LLM
        self.llm = get_llm(
            llm,
            stop_sequences=["</execute>"],
            source=source,
            base_url=base_url,
            api_key=api_key or "EMPTY",
        )

        # Execution lock — code execution touches builtins (REPL namespace),
        # so we serialise calls to avoid cross-request interference.
        self._exec_lock = Lock()

        # Build workflow
        self._build_workflow()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str):
        self._system_prompt = value

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _default_system_prompt(self) -> str:
        return """\
You are an AI assistant that solves tasks through reasoning and code execution.

INTERACTION MODES:
1. DIRECT RESPONSE: For questions, explanations, planning — just reply in plain text.
2. CODE EXECUTION: When you need to compute, call tools, or interact with data,
   wrap your code in <execute>...</execute> tags. You'll see the output in <observation>.
3. TASK COMPLETE: When finished, include <done/> at the end of your final message.

RULES:
- You can mix text and code in the same response.
- Always print() results in code blocks so they appear in observations.
- Python is the default. Use #!BASH prefix for shell commands.
- Keep code simple. Break complex tasks into multiple steps.
- If code fails, analyze the error before retrying."""

    # ------------------------------------------------------------------
    # Message parsing
    # ------------------------------------------------------------------

    def _parse_response(self, response) -> tuple[str, str]:
        """Parse LLM response → (content, next_step).

        next_step: "execute" | "end"
        - "execute": response contains <execute> block → run code
        - "end":     response is pure text or contains <done/> → stop
        """
        content = response.content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text") or block.get("content") or ""
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(block, str):
                    parts.append(block)
            content = "".join(parts)
        else:
            content = str(content)

        # Fix unclosed tag (can happen with the </execute> stop sequence)
        if "<execute>" in content and "</execute>" not in content:
            content += "</execute>"

        has_execute = bool(re.search(r"<execute>.*?</execute>", content, re.DOTALL))
        next_step = "execute" if has_execute else "end"

        return content.strip(), next_step

    # ------------------------------------------------------------------
    # LangGraph nodes
    # ------------------------------------------------------------------

    def _node_generate(self, state: AgentState) -> AgentState:
        """Call LLM."""
        messages = [SystemMessage(content=self.system_prompt)] + state["messages"]
        response = self.llm.invoke(messages)
        content, next_step = self._parse_response(response)

        state["messages"].append(AIMessage(content=content))
        state["next_step"] = next_step
        return state

    def _node_execute(self, state: AgentState) -> AgentState:
        """Extract and run code from the last AI message.

        Code execution is serialised via self._exec_lock because the REPL
        uses a shared builtins namespace for injected functions.
        """
        last_content = state["messages"][-1].content

        blocks = re.findall(r"<execute>(.*?)</execute>", last_content, re.DOTALL)
        if not blocks:
            return state

        results = []
        with self._exec_lock:
            for code in blocks:
                code = code.strip()
                if code.startswith("#!BASH"):
                    script = code.replace("#!BASH", "", 1).strip()
                    result = run_with_timeout(run_bash_script, [script], timeout=self.timeout_seconds)
                else:
                    self._inject_functions_to_repl()
                    result = run_with_timeout(run_python_repl, [code], timeout=self.timeout_seconds)
                results.append(result)

        combined = "\n".join(results)
        if len(combined) > 15000:
            combined = combined[:15000] + "\n\n[Output truncated — showing first 15K chars]"

        state["messages"].append(AIMessage(content=f"<observation>\n{combined}\n</observation>"))
        state["next_step"] = "generate"
        return state

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    @staticmethod
    def _route(state: AgentState) -> Literal["execute", "end"]:
        ns = state.get("next_step", "end")
        return "execute" if ns == "execute" else "end"

    # ------------------------------------------------------------------
    # Workflow construction
    # ------------------------------------------------------------------

    def _build_workflow(self):
        """Build the LangGraph state machine.

        We intentionally do NOT attach a checkpointer: conversation history
        is managed by the caller (ConversationStore), and passed into each
        run via `run_with_history`. This keeps the agent stateless and
        safe to use across concurrent requests.
        """
        workflow = StateGraph(AgentState)
        workflow.add_node("generate", self._node_generate)
        workflow.add_node("execute", self._node_execute)

        workflow.add_edge(START, "generate")
        workflow.add_edge("execute", "generate")
        workflow.add_conditional_edges(
            "generate",
            self._route,
            {"execute": "execute", "end": END},
        )

        self.app = workflow.compile()

    # ------------------------------------------------------------------
    # REPL injection
    # ------------------------------------------------------------------

    def _inject_functions_to_repl(self):
        """Inject registered functions into the REPL namespace."""
        custom_fns = getattr(builtins, "_base_CAi_custom_functions", None)
        if custom_fns:
            inject_custom_functions(custom_fns)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, prompt: str) -> tuple[list[str], str]:
        """Run the agent on a single prompt (no prior history).

        Returns:
            (log, final_content) — log is a list of short message summaries,
            final_content is the last message's full content.
        """
        inputs = {"messages": [HumanMessage(content=prompt)], "next_step": None}
        config = {"recursion_limit": 200}

        log: list[str] = []
        final_content = ""
        for state in self.app.stream(inputs, stream_mode="values", config=config):
            msg = state["messages"][-1]
            final_content = msg.content
            log.append(f"[{msg.__class__.__name__}] {msg.content[:200]}")

        return log, final_content

    def run_stream(self, prompt: str) -> Generator[dict, None, None]:
        """Stream execution steps as dicts (no prior history)."""
        yield from self.run_with_history(prompt, history=[])

    def run_with_history(
        self, prompt: str, history: list[dict]
    ) -> Generator[dict, None, None]:
        """Run with pre-existing conversation history.

        Args:
            prompt: The new user message.
            history: List of prior messages, each
                     {"role": "user"|"assistant", "content": "..."}.
                     History is provided by the caller (stateless execution);
                     the agent does not persist anything itself.

        Yields:
            {"type": <message class name>, "content": <str>} for each new
            message produced by the workflow.
        """
        messages: list[BaseMessage] = []
        for m in history:
            role = m.get("role")
            content = m.get("content", "")
            if not isinstance(content, str):
                continue
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))

        messages.append(HumanMessage(content=prompt))
        inputs = {"messages": messages, "next_step": None}
        config = {"recursion_limit": 200}

        # Track which messages are new (produced by this run), so we only
        # stream those — not the replayed history.
        seen = len(messages)
        for state in self.app.stream(inputs, stream_mode="values", config=config):
            new_msgs = state["messages"][seen:]
            seen = len(state["messages"])
            for msg in new_msgs:
                yield {"type": msg.__class__.__name__, "content": msg.content}
