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
"""

import builtins
import re
from collections.abc import Generator
from datetime import datetime
from typing import Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from base_CAi.llm import SourceType, get_llm
from base_CAi.tool.support_tools import run_python_repl
from base_CAi.utils import run_bash_script, run_with_timeout


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

        next_step: "execute" | "end" | "continue"
        - "execute": response contains <execute> block → run code
        - "end": response contains <done/> → conversation complete
        - "continue": pure text response → return to user, wait for input
                      (in autonomous mode, treated as "generate" again)
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

        # Fix unclosed tags
        if "<execute>" in content and "</execute>" not in content:
            content += "</execute>"

        # Determine next step
        has_execute = bool(re.search(r"<execute>.*?</execute>", content, re.DOTALL))
        has_done = "<done/>" in content or "<done>" in content

        if has_execute:
            next_step = "execute"
        elif has_done:
            next_step = "end"
        else:
            # Pure text — in autonomous mode we check if agent seems to be
            # waiting for user input or if it should keep going
            next_step = "end"

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
        """Extract and run code from the last AI message."""
        last_content = state["messages"][-1].content

        # Find all execute blocks (there might be multiple)
        blocks = re.findall(r"<execute>(.*?)</execute>", last_content, re.DOTALL)
        if not blocks:
            return state

        results = []
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
        # Truncate very long output
        if len(combined) > 15000:
            combined = combined[:15000] + "\n\n[Output truncated — showing first 15K chars]"

        state["messages"].append(AIMessage(content=f"<observation>\n{combined}\n</observation>"))
        state["next_step"] = "generate"  # Always go back to LLM after execution
        return state

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    @staticmethod
    def _route(state: AgentState) -> Literal["execute", "generate", "end"]:
        ns = state.get("next_step", "end")
        if ns == "execute":
            return "execute"
        elif ns == "continue":
            return "generate"
        else:
            return "end"

    # ------------------------------------------------------------------
    # Workflow construction
    # ------------------------------------------------------------------

    def _build_workflow(self):
        """Build the LangGraph state machine."""
        workflow = StateGraph(AgentState)
        workflow.add_node("generate", self._node_generate)
        workflow.add_node("execute", self._node_execute)

        workflow.add_edge(START, "generate")
        workflow.add_edge("execute", "generate")
        workflow.add_conditional_edges(
            "generate",
            self._route,
            {"execute": "execute", "generate": "generate", "end": END},
        )

        self.app = workflow.compile()
        self.checkpointer = MemorySaver()
        self.app.checkpointer = self.checkpointer

    # ------------------------------------------------------------------
    # REPL injection
    # ------------------------------------------------------------------

    def _inject_functions_to_repl(self):
        """Inject registered functions into the REPL namespace."""
        custom_fns = getattr(builtins, "_base_CAi_custom_functions", None)
        if custom_fns:
            from base_CAi.utils import inject_custom_functions_to_repl
            inject_custom_functions_to_repl(custom_fns)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, prompt: str, thread_id: str = "default") -> tuple[list[str], str]:
        """Run the agent on a prompt. Returns (log, final_content)."""
        inputs = {"messages": [HumanMessage(content=prompt)], "next_step": None}
        config = {"recursion_limit": 200, "configurable": {"thread_id": thread_id}}

        log = []
        final_content = ""

        for state in self.app.stream(inputs, stream_mode="values", config=config):
            msg = state["messages"][-1]
            final_content = msg.content
            log.append(f"[{msg.__class__.__name__}] {msg.content[:200]}")

        self._last_state = state
        return log, final_content

    def run_stream(self, prompt: str, thread_id: str = "default") -> Generator[dict, None, None]:
        """Stream execution steps as dicts."""
        inputs = {"messages": [HumanMessage(content=prompt)], "next_step": None}
        config = {"recursion_limit": 200, "configurable": {"thread_id": thread_id}}

        for state in self.app.stream(inputs, stream_mode="values", config=config):
            msg = state["messages"][-1]
            yield {"type": msg.__class__.__name__, "content": msg.content}

        self._last_state = state

    def run_with_history(
        self, prompt: str, history: list[dict], thread_id: str = "default"
    ) -> Generator[dict, None, None]:
        """Run with pre-existing conversation history.

        history: list of {"role": "user"|"assistant", "content": "..."}
        """
        messages = []
        for msg in history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))

        messages.append(HumanMessage(content=prompt))
        inputs = {"messages": messages, "next_step": None}
        config = {"recursion_limit": 200, "configurable": {"thread_id": thread_id}}

        for state in self.app.stream(inputs, stream_mode="values", config=config):
            msg = state["messages"][-1]
            yield {"type": msg.__class__.__name__, "content": msg.content}

        self._last_state = state
