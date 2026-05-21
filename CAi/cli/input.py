"""Input handling: prompt_toolkit session, multiline mode, bracket detection."""

from __future__ import annotations

import os
from typing import Any

from CAi.cli.theme import console
from CAi.config import WORKSPACE_DIR


def create_prompt_session() -> Any:
    """Create a PromptSession with history, key bindings, and tab completion."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style

    history_dir = str((WORKSPACE_DIR / "agent_workspace").resolve())
    os.makedirs(history_dir, exist_ok=True)
    history_path = os.path.join(history_dir, ".cli_history")

    # Command completer — triggers when input starts with ":"
    command_words = [
        ":quit", ":exit", ":q",
        ":help", ":h",
        ":new", ":convs", ":load",
        ":ml", ":retry",
        ":history", ":last_obs", ":tools",
        ":rename", ":forget", ":delete",
        ":reset-kernel", ":clear",
    ]
    completer = WordCompleter(command_words, sentence=True)

    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _submit_multiline(event):
        event.app.exit(result=event.app.current_buffer.text)

    @kb.add("c-c")
    def _cancel_input(event):
        raise KeyboardInterrupt

    return PromptSession(
        history=FileHistory(history_path),
        auto_suggest=AutoSuggestFromHistory(),
        completer=completer,
        complete_while_typing=False,
        key_bindings=kb,
        style=Style.from_dict({
            "prompt": "fg:#56b6c2 bold",
            "": "fg:#abb2bf",
        }),
    )


def has_unclosed_brackets(text: str) -> bool:
    """Return True if text has unclosed brackets or open string literals."""
    stack = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    in_string = False
    string_char = None
    i = 0
    while i < len(text):
        ch = text[i]
        triple = text[i: i + 3]
        if triple in ('"""', "'''"):
            if not in_string:
                in_string, string_char = True, triple
            elif string_char == triple:
                in_string = False
            i += 3
            continue
        if in_string:
            if ch == string_char and (i == 0 or text[i - 1] != "\\"):
                in_string = False
            i += 1
            continue
        if ch in ('"', "'"):
            in_string, string_char = True, ch
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            if stack and stack[-1] == ch:
                stack.pop()
        i += 1
    return bool(stack) or in_string


def read_multiline(session: Any) -> str | None:
    """Enter multi-line input mode. Returns text or None if cancelled."""
    console.print(
        "  [cai.dim]╭─ multi-line[/cai.dim]  "
        "[cai.text]Esc+Enter[/cai.text] [cai.dim]submit[/cai.dim]  "
        "[cai.text]Ctrl+C[/cai.text] [cai.dim]cancel[/cai.dim]"
    )
    lines: list[str] = []
    try:
        while True:
            try:
                line = session.prompt("  │ ")
            except KeyboardInterrupt:
                console.print("  [cai.dim]╰─ cancelled[/cai.dim]")
                return None

            if not line and lines and not has_unclosed_brackets("\n".join(lines)):
                break
            lines.append(line)
    except EOFError:
        return None

    console.print("  [cai.dim]╰─ submitted[/cai.dim]")
    return "\n".join(lines)
