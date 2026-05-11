"""
BaseAgent — 精简的 Agent 基类

职责：
- LLM 初始化
- 执行循环（generate → execute → generate）
- 代码执行（Python / Bash）
- 消息解析（支持混合模式：纯文本 + 代码执行）
- 流式输出（token 级）与非流式输出两种 API

Concurrency note:
    BaseAgent 本身是 stateless 的。每次 run*() 调用都构造新的输入；
    对话历史由调用方（例如 ConversationStore）显式管理。
    这样多个并发请求不会互相串扰。
"""

from __future__ import annotations

import builtins
import re
from collections.abc import Generator
from threading import Lock
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from .execution import (
    inject_custom_functions,
    run_bash_script,
    run_python_repl,
    run_with_timeout,
)
from .llm import SourceType, get_llm

# Maximum number of generate→execute cycles before we give up (safety net).
_MAX_ITERATIONS = 50


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
        temperature: float | None = None,
        timeout_seconds: int = 600,
        system_prompt: str | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self._system_prompt = system_prompt or self._default_system_prompt()

        # Init LLM. api_key=None lets the factory read the provider-specific
        # env var ("EMPTY" is used automatically for Custom endpoints that
        # don't require auth).
        self.llm = get_llm(
            llm,
            temperature=temperature,
            stop_sequences=["</execute>"],
            source=source,
            base_url=base_url,
            api_key=api_key,
        )

        # Serialise code execution — the REPL shares builtins across calls.
        self._exec_lock = Lock()

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

    @staticmethod
    def _normalize_content(content: Any) -> str:
        """Coerce LangChain content (str or list of blocks) into a plain string."""
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text") or block.get("content") or ""
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(block, str):
                    parts.append(block)
            return "".join(parts)
        return str(content) if content is not None else ""

    @staticmethod
    def _has_execute_block(content: str) -> bool:
        return bool(re.search(r"<execute>.*?</execute>", content, re.DOTALL))

    # ------------------------------------------------------------------
    # Code execution
    # ------------------------------------------------------------------

    def _run_code_blocks(self, content: str) -> str | None:
        """Extract <execute>...</execute> blocks from `content`, run them,
        and return the combined output wrapped in <observation> tags.

        Returns None if there were no code blocks.
        """
        blocks = re.findall(r"<execute>(.*?)</execute>", content, re.DOTALL)
        if not blocks:
            return None

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
        return f"<observation>\n{combined}\n</observation>"

    def _inject_functions_to_repl(self):
        """Make registered tools callable from inside the REPL."""
        custom_fns = getattr(builtins, "_base_CAi_custom_functions", None)
        if custom_fns:
            inject_custom_functions(custom_fns)

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def _build_messages(self, prompt: str, history: list[dict]) -> list[BaseMessage]:
        """Convert caller-supplied history + new prompt into LangChain messages."""
        messages: list[BaseMessage] = [SystemMessage(content=self._system_prompt)]
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
        return messages

    def _invoke_llm_full(self, messages: list[BaseMessage]) -> str:
        """Blocking LLM call — returns full response as a string."""
        response = self.llm.invoke(messages)
        content = self._normalize_content(response.content)
        # Repair unclosed <execute> (can happen with the </execute> stop sequence).
        if "<execute>" in content and "</execute>" not in content:
            content += "</execute>"
        return content.strip()

    def _stream_llm(
        self, messages: list[BaseMessage]
    ) -> Generator[tuple[str, str], None, None]:
        """Stream LLM tokens.

        Yields (kind, text) tuples:
            ("token", chunk)  — each token/partial chunk as it arrives
            ("final", full)   — complete, tag-repaired content once done
        """
        pieces: list[str] = []
        try:
            for chunk in self.llm.stream(messages):
                delta = self._normalize_content(getattr(chunk, "content", ""))
                if delta:
                    pieces.append(delta)
                    yield "token", delta
        except Exception as e:  # noqa: BLE001
            # Fall back to blocking invoke if streaming isn't supported
            # by this LLM backend — at least the user gets a response.
            err_msg = f"[Stream error: {e}. Falling back to blocking call.]"
            yield "token", err_msg
            full = self._invoke_llm_full(messages)
            yield "final", full
            return

        full = "".join(pieces).strip()
        if "<execute>" in full and "</execute>" not in full:
            full += "</execute>"
        yield "final", full

    # ------------------------------------------------------------------
    # Public API — non-streaming
    # ------------------------------------------------------------------

    def run(self, prompt: str) -> tuple[list[str], str]:
        """Run the agent on a single prompt (no prior history, non-streaming).

        Returns:
            (log, final_content) — log is a list of short message summaries,
            final_content is the last AI message.
        """
        log, final = [], ""
        for step in self.run_with_history(prompt, history=[]):
            log.append(f"[{step['type']}] {step['content'][:200]}")
            final = step["content"]
        return log, final

    def run_with_history(
        self, prompt: str, history: list[dict]
    ) -> Generator[dict, None, None]:
        """Non-streaming: run the agent to completion, yielding one dict
        per complete AIMessage (including observations).

        Kept for backward compatibility and tests. For real-time UI use
        `run_with_history_streaming`.

        Yields:
            {"type": "AIMessage", "content": <str>}
        """
        messages = self._build_messages(prompt, history)

        for _ in range(_MAX_ITERATIONS):
            content = self._invoke_llm_full(messages)
            messages.append(AIMessage(content=content))
            yield {"type": "AIMessage", "content": content}

            if not self._has_execute_block(content):
                return  # No code — conversation is done.

            observation = self._run_code_blocks(content)
            if observation is None:
                return
            messages.append(AIMessage(content=observation))
            yield {"type": "AIMessage", "content": observation}

    # ------------------------------------------------------------------
    # Public API — streaming
    # ------------------------------------------------------------------

    def run_with_history_streaming(
        self, prompt: str, history: list[dict]
    ) -> Generator[dict, None, None]:
        """Streaming: drive the agent loop and yield fine-grained events.

        Events:
            {"type": "token",       "content": <str>}   one LLM token/chunk
            {"type": "message_end", "content": <str>}   full AI message done
            {"type": "observation", "content": <str>}   code execution output

        The loop runs until the LLM produces a message with no <execute>
        block (i.e. a final answer / plain text / <done/>).
        """
        messages = self._build_messages(prompt, history)

        for _ in range(_MAX_ITERATIONS):
            # 1. Stream LLM tokens for the current turn.
            full_text = ""
            for kind, text in self._stream_llm(messages):
                if kind == "token":
                    yield {"type": "token", "content": text}
                elif kind == "final":
                    full_text = text

            messages.append(AIMessage(content=full_text))
            yield {"type": "message_end", "content": full_text}

            # 2. If there's no <execute> block, we're done.
            if not self._has_execute_block(full_text):
                return

            # 3. Run the code block(s) and yield the observation.
            observation = self._run_code_blocks(full_text)
            if observation is None:
                return
            messages.append(AIMessage(content=observation))
            yield {"type": "observation", "content": observation}

    # ------------------------------------------------------------------
    # Legacy helper — some callers still use run_stream without history.
    # ------------------------------------------------------------------

    def run_stream(self, prompt: str) -> Generator[dict, None, None]:
        """Non-streaming (one event per complete message); no prior history."""
        yield from self.run_with_history(prompt, history=[])
