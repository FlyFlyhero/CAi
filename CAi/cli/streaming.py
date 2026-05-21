"""Streaming event handler — consumes agent events and renders output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rich.panel import Panel
from rich import box

from CAi.cli.theme import console
from CAi.cli.display import display_observation, display_ai_response_start, display_ai_response_end

# Regex to detect <execute> blocks in streamed tokens
_EXECUTE_OPEN = re.compile(r"<execute>")
_EXECUTE_CLOSE = re.compile(r"</execute>")


class StreamingInterrupted(Exception):
    """Raised when Ctrl+C interrupts an in-progress stream."""
    def __init__(self, partial: str, raw_log: list[dict]):
        self.partial = partial
        self.raw_log = raw_log
        super().__init__(partial)


@dataclass
class StreamResult:
    full_message: str
    raw_log: list[dict] = field(default_factory=list)
    has_executions: bool = False
    interrupted: bool = False


def _display_code_block(code: str) -> None:
    """Render a collected code block in a bordered panel."""
    code = code.strip()
    if not code:
        return
    console.print()
    console.print(
        Panel(
            code,
            title="[bold cai.cyan]▶ Code[/bold cai.cyan]",
            title_align="left",
            border_style="#3e4452",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def stream_response(agent: object, prompt: str, history: list[dict]) -> StreamResult:
    """
    Stream agent response events to the terminal.

    Filters out <execute>/<\/execute> tags from display and renders code
    blocks in a separate panel. Normal text is printed inline.
    Raises StreamingInterrupted on Ctrl+C with partial content preserved.
    """
    raw_log: list[dict] = []
    full_message = ""

    # State machine for tracking execute blocks
    in_execute = False
    code_buffer = ""
    text_buffer = ""  # Buffer to detect tags across token boundaries

    display_ai_response_start()

    try:
        for event in agent.run_with_history_streaming(prompt, history):  # type: ignore[attr-defined]
            ev_type = event.get("type")
            content = event.get("content", "")

            if ev_type == "token":
                full_message += content
                text_buffer += content

                # Process the buffer for execute tags
                while text_buffer:
                    if not in_execute:
                        # Look for <execute> opening tag
                        match = _EXECUTE_OPEN.search(text_buffer)
                        if match:
                            # Print everything before the tag
                            before = text_buffer[:match.start()]
                            if before:
                                console.print(before, end="", markup=False)
                            text_buffer = text_buffer[match.end():]
                            in_execute = True
                            code_buffer = ""
                        else:
                            # No tag found — but might be partial tag at end
                            # Keep last 9 chars in buffer (len("<execute>") = 9)
                            if len(text_buffer) > 9:
                                safe = text_buffer[:-9]
                                console.print(safe, end="", markup=False)
                                text_buffer = text_buffer[-9:]
                            break
                    else:
                        # Inside execute block — look for </execute>
                        match = _EXECUTE_CLOSE.search(text_buffer)
                        if match:
                            code_buffer += text_buffer[:match.start()]
                            text_buffer = text_buffer[match.end():]
                            in_execute = False
                            _display_code_block(code_buffer)
                            code_buffer = ""
                        else:
                            # Keep last 10 chars (len("</execute>") = 10)
                            if len(text_buffer) > 10:
                                code_buffer += text_buffer[:-10]
                                text_buffer = text_buffer[-10:]
                            break

            elif ev_type == "message_end":
                # Flush remaining buffer
                if text_buffer:
                    if in_execute:
                        code_buffer += text_buffer
                        _display_code_block(code_buffer)
                    else:
                        console.print(text_buffer, end="", markup=False)
                    text_buffer = ""
                    code_buffer = ""
                    in_execute = False

                full_message = content
                console.print()

            elif ev_type == "observation":
                # Flush text buffer before showing observation
                if text_buffer and not in_execute:
                    console.print(text_buffer, end="", markup=False)
                    text_buffer = ""
                raw_log.append({"type": "observation", "content": content})
                display_observation(content)

    except KeyboardInterrupt:
        # Flush what we have
        if text_buffer and not in_execute:
            console.print(text_buffer, end="", markup=False)
        raise StreamingInterrupted(partial=full_message, raw_log=raw_log)

    # Final flush if stream ended without message_end
    if text_buffer:
        if in_execute:
            code_buffer += text_buffer
            _display_code_block(code_buffer)
        else:
            console.print(text_buffer, end="", markup=False)

    display_ai_response_end()

    return StreamResult(
        full_message=full_message,
        raw_log=raw_log,
        has_executions=bool(raw_log),
    )
