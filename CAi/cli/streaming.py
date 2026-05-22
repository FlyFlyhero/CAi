"""Streaming event handler — consumes agent events and renders output.

Tag handling is delegated to ``CAi.CAi_agent.agent_tags`` so the CLI
stays in lock-step with the rest of the system. We accumulate tokens
into a buffer, periodically scan for complete ``<execute>`` blocks,
and only emit prose to the user — code is rendered separately in a
panel after each block closes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from rich.panel import Panel
from rich import box

from CAi.CAi_agent.agent_tags import (
    EXECUTE_RE,
    detect_lang,
    parse_attrs,
)
from CAi.cli.theme import console
from CAi.cli.display import (
    display_ai_response_end,
    display_ai_response_start,
    display_observation,
)


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


# ---------------------------------------------------------------------------
# Code-block rendering
# ---------------------------------------------------------------------------

# Friendly title per language so the user can see what's running.
_LANG_TITLES = {
    "python": "[bold cai.cyan]▶ Python[/bold cai.cyan]",
    "bash":   "[bold cai.cyan]▶ Bash[/bold cai.cyan]",
    "r":      "[bold cai.cyan]▶ R[/bold cai.cyan]",
}


def _display_code_block(code: str, lang: str = "python") -> None:
    """Render a complete code block in a bordered panel."""
    code = code.strip()
    if not code:
        return
    console.print()
    console.print(
        Panel(
            code,
            title=_LANG_TITLES.get(lang, _LANG_TITLES["python"]),
            title_align="left",
            border_style="#3e4452",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


# ---------------------------------------------------------------------------
# Buffered tag-aware printer
# ---------------------------------------------------------------------------

# Length of the longest opening token we need to keep in the holdback
# buffer when we don't yet know if a tag is forming. ``<execute`` is
# 8 chars; we hold back a bit more to be safe across multibyte boundaries.
_HOLDBACK_BYTES = 16


class _TagAwarePrinter:
    """Stream prose to the console, suppressing tags and rendering code blocks.

    The printer buffers incoming tokens. When a complete
    ``<execute ...>...</execute>`` block has arrived, it:

      1. Prints any prose that came *before* the opening tag.
      2. Renders the code via :func:`_display_code_block`.
      3. Continues with whatever follows.

    Until a partial tag is unambiguously incomplete OR clearly not a
    tag at all, we hold back the tail of the buffer so we never leak
    a half-printed ``<execu`` to the user.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._printed_offset = 0  # index up to which prose has been emitted
        self._processed_offset = 0  # index up to which we've parsed for tags

    def feed(self, text: str) -> None:
        if not text:
            return
        self._buffer += text
        self._drain(final=False)

    def finish(self) -> None:
        """Flush whatever's left, even if it looks like a partial tag.

        Called after the stream ends — at that point we know there are
        no more tokens, so any held-back text is just plain content.
        """
        self._drain(final=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _drain(self, *, final: bool) -> None:
        """Process as much of the buffer as we safely can."""
        while True:
            tail = self._buffer[self._processed_offset:]
            if not tail:
                return

            # Look for the next complete <execute>...</execute> block in
            # the unprocessed region.
            match = EXECUTE_RE.search(self._buffer, self._processed_offset)
            if match:
                # Emit prose up to the opening of the matched block.
                self._emit_prose_until(match.start())
                attrs = parse_attrs(match.group("attrs") or "")
                lang, code = detect_lang(match.group("code"), attrs.get("lang"))
                _display_code_block(code, lang)
                self._processed_offset = match.end()
                self._printed_offset = match.end()
                continue

            # No complete block ahead. Decide how much prose is safe to
            # emit right now without leaking a partial open tag.
            safe_until = self._safe_emit_boundary(final=final)
            if safe_until > self._printed_offset:
                self._emit_prose_until(safe_until)
            self._processed_offset = self._printed_offset
            return

    def _emit_prose_until(self, end: int) -> None:
        """Print buffer[self._printed_offset:end] verbatim."""
        if end <= self._printed_offset:
            return
        prose = self._buffer[self._printed_offset:end]
        if prose:
            console.print(prose, end="", markup=False)
        self._printed_offset = end

    def _safe_emit_boundary(self, *, final: bool) -> int:
        """Return the largest index up to which prose can be safely emitted.

        At end-of-stream everything is safe. Otherwise any ``<`` whose
        next character could be the start of a tag name (or ``/`` for a
        closing tag) makes the rest of the buffer unsafe — we can't tell
        from here whether ``<execute lang="bash">`` is followed by
        ``</execute>`` until it arrives. Plain ``<`` followed by digits
        or whitespace (e.g. ``1 < 2``) is recognised as non-tag prose
        and skipped over.
        """
        end = len(self._buffer)
        if final:
            return end

        pos = self._printed_offset
        while True:
            lt = self._buffer.find("<", pos)
            if lt == -1:
                break
            nxt = self._buffer[lt + 1: lt + 2]
            # A tag name must start with a letter, or '/' for a closer.
            if nxt and (nxt.isalpha() or nxt == "/"):
                return lt
            # Otherwise it's not a tag — skip past this '<' and keep looking.
            pos = lt + 1

        # No suspicious '<' found — apply the regular byte-holdback so a
        # multi-byte boundary mid-token doesn't get truncated.
        return max(self._printed_offset, end - _HOLDBACK_BYTES)


def _looks_like_partial_open_tag(s: str) -> bool:
    """Could ``s`` be a still-incomplete ``<execute...>`` opening tag?

    Used by the test-suite to verify the prefix-recognition contract.
    A fully-formed open tag (containing ``>``) returns False because at
    that point :data:`EXECUTE_RE` takes over.
    """
    if not s.startswith("<"):
        return False
    if ">" in s:
        return False
    target = "<execute"
    return target.startswith(s[: min(len(s), len(target))])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def stream_response(agent: object, prompt: str, history: list[dict]) -> StreamResult:
    """Stream agent events to the terminal.

    Code blocks are extracted and rendered in panels; prose is printed
    inline. Observation events are forwarded to :func:`display_observation`.
    Raises :class:`StreamingInterrupted` on Ctrl+C with whatever partial
    output had been collected.
    """
    raw_log: list[dict] = []
    full_message = ""
    printer = _TagAwarePrinter()

    display_ai_response_start()

    try:
        for event in agent.run_with_history_streaming(prompt, history):  # type: ignore[attr-defined]
            ev_type = event.get("type")
            content: str = event.get("content", "")

            if ev_type == "token":
                full_message += content
                printer.feed(content)

            elif ev_type == "message_end":
                # The runtime has finished this message — flush whatever
                # the printer was holding back, then sync our copy to
                # the canonical content (in case streaming dropped chars).
                printer.finish()
                full_message = content
                console.print()
                # Reset the printer for any subsequent message in the
                # same agent loop (rare but possible).
                printer = _TagAwarePrinter()

            elif ev_type == "observation":
                # Make sure no held-back prose leaks past the observation.
                printer.finish()
                printer = _TagAwarePrinter()
                raw_log.append({"type": "observation", "content": content})
                display_observation(content)

    except KeyboardInterrupt:
        printer.finish()
        raise StreamingInterrupted(partial=full_message, raw_log=raw_log) from None

    printer.finish()
    display_ai_response_end()

    return StreamResult(
        full_message=full_message,
        raw_log=raw_log,
        has_executions=bool(raw_log),
    )
