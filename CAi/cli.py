"""CAi CLI — Codex-style interactive REPL.

Output layer: minimal rich for structure, raw buffered stdout for streaming.
Input layer: prompt_toolkit for history, completions, shortcuts, and multiline input.

Launch with:  python -m CAi.main --cli
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from rich import box
from rich.align import Align
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme
from rich.text import Text

from CAi.config import WORKSPACE_DIR


# ---------------------------------------------------------------------------
# Minimal Codex-like theme
# ---------------------------------------------------------------------------

custom_theme = Theme(
    {
        "cai.fg": "#c9d1d9",
        "cai.dim": "#6e7681",
        "cai.muted": "#8b949e",
        "cai.accent": "#58a6ff",
        "cai.ok": "#3fb950",
        "cai.warn": "#d29922",
        "cai.err": "#f85149",
        "cai.exec": "#a371f7",
        "cai.border": "#30363d",
    }
)

console = Console(theme=custom_theme, highlight=False, soft_wrap=True)


# ---------------------------------------------------------------------------
# Optional logo rendering
# ---------------------------------------------------------------------------

def _supports_truecolor() -> bool:
    ct = os.environ.get("COLORTERM", "").lower()
    term = os.environ.get("TERM", "").lower()
    return ("truecolor" in ct) or ("24bit" in ct) or ("xterm-kitty" in term)


def _find_logo_path() -> Path | None:
    env_path = os.environ.get("CAI_CLI_LOGO")
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())

    here = Path(__file__).resolve().parent
    project_root = here.parent
    candidates.extend(
        [
            # User/project layout: <project-root>/assets/CAiCopilot.png
            project_root / "assets" / "CAiCopilot.png",
            project_root / "assets" / "cai_copilot.png",
            project_root / "assets" / "cai_logo.png",
            project_root / "assets" / "logo.png",
            # Package-local fallbacks: <project-root>/CAi/assets/*.png
            here / "assets" / "CAiCopilot.png",
            here / "assets" / "cai_logo.png",
            here / "assets" / "cai_copilot.png",
            here / "assets" / "logo.png",
            # Workspace fallback
            Path(WORKSPACE_DIR) / "agent_workspace" / "cai_logo.png",
        ]
    )

    for p in candidates:
        try:
            if p.is_file():
                return p
        except Exception:
            continue
    return None


def _render_logo_ansi(path: Path, max_width: int = 56) -> Text | None:
    """Render a PNG logo as ANSI truecolor half-blocks.

    Improvements over the earlier version:
    - crop transparent padding so the logo occupies more visual space
    - use nearest-neighbor scaling for crisp pixel-art edges
    - avoid painting transparent halves black, which caused muddy halos
    """
    try:
        from PIL import Image
    except Exception:
        return None

    try:
        img = Image.open(path).convert("RGBA")
    except Exception:
        return None

    # Crop transparent borders so the visible mark is larger and cleaner.
    alpha = img.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        img = img.crop(bbox)

    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        return None

    out_w = min(max_width, src_w)
    out_h_chars = max(1, int(round((src_h / src_w) * out_w / 2)))

    # Pixel-art assets look much better with nearest-neighbor scaling.
    try:
        resample = Image.Resampling.NEAREST
    except AttributeError:
        resample = Image.NEAREST
    img = img.resize((out_w, out_h_chars * 2), resample=resample)
    px = img.load()

    def rgba_at(x: int, y: int):
        r, g, b, a = px[x, y]
        if a <= 24:
            return None
        return (r, g, b)

    lines: list[str] = []
    for y in range(0, out_h_chars * 2, 2):
        parts: list[str] = []
        for x in range(out_w):
            top = rgba_at(x, y)
            bottom = rgba_at(x, y + 1)

            if top is None and bottom is None:
                parts.append(" ")
            elif top is not None and bottom is not None:
                parts.append(
                    f"\x1b[38;2;{top[0]};{top[1]};{top[2]}m"
                    f"\x1b[48;2;{bottom[0]};{bottom[1]};{bottom[2]}m▀"
                )
            elif top is not None:
                parts.append(f"\x1b[38;2;{top[0]};{top[1]};{top[2]}m▀")
            else:
                parts.append(f"\x1b[38;2;{bottom[0]};{bottom[1]};{bottom[2]}m▄")

        parts.append("\x1b[0m")
        lines.append("".join(parts))

    return Text.from_ansi("\n".join(lines))


def _print_startup_logo() -> bool:
    if not _supports_truecolor():
        return False

    logo_path = _find_logo_path()
    if not logo_path:
        return False

    try:
        default_width = max(72, int(console.width * 0.82))
        width = int(os.environ.get("CAI_CLI_LOGO_WIDTH", str(default_width)))
    except ValueError:
        width = max(72, int(console.width * 0.82))
    width = max(32, min(width, max(32, console.width - 2)))

    rendered = _render_logo_ansi(logo_path, max_width=width)
    if rendered is None:
        return False

    console.print(Align.center(rendered), crop=False, overflow="ignore")
    return True


# ---------------------------------------------------------------------------
# Exceptions and result objects
# ---------------------------------------------------------------------------

class StreamingInterrupted(Exception):
    """Ctrl+C interrupted streaming. Carries partial output for recovery."""

    def __init__(self, partial: str, raw_log: list[dict]):
        self.partial = partial
        self.raw_log = raw_log
        super().__init__(partial)


@dataclass
class StreamResult:
    full_message: str
    raw_log: list[dict]
    has_executions: bool
    interrupted: bool = False


# ---------------------------------------------------------------------------
# Fast streaming renderer
# ---------------------------------------------------------------------------

class _BufferedTokenWriter:
    """Low-overhead token writer.

    Calling rich Console.print for every token is noticeably slow. This writer
    batches tiny chunks and writes directly to Console.file, keeping streaming
    smooth while still allowing rich output for structural lines.
    """

    def __init__(self, *, min_interval: float = 0.025, min_chars: int = 48):
        self._buf: list[str] = []
        self._last_flush = time.monotonic()
        self._min_interval = min_interval
        self._min_chars = min_chars
        self._chars = 0

    def write(self, text: str) -> None:
        if not text:
            return
        self._buf.append(text)
        self._chars += len(text)
        now = time.monotonic()
        if "\n" in text or self._chars >= self._min_chars or (now - self._last_flush) >= self._min_interval:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        console.file.write("".join(self._buf))
        console.file.flush()
        self._buf.clear()
        self._chars = 0
        self._last_flush = time.monotonic()


class _CodexStreamRenderer:
    """Stream tokens while hiding XML-ish execution tags.

    The agent may emit <execute>...</execute> or <observation>...</observation>
    in the assistant stream. Showing those tags raw makes the UI look broken.
    This renderer suppresses the tags and renders compact Codex-style execution
    rows inside the flow instead.
    """

    _open_tags = {
        "<execute>": "execute",
        "<observation>": "observation",
    }
    _max_open_len = max(len(t) for t in _open_tags)

    def __init__(self) -> None:
        self.writer = _BufferedTokenWriter()
        self.pending = ""
        self.hidden_mode: str | None = None
        self.hidden_chunks: list[str] = []
        self._last_visible_char = ""

    def feed(self, text: str) -> None:
        self.pending += text
        self._process(final=False)

    def finish(self) -> None:
        self._process(final=True)
        self.writer.flush()

    def _write_visible(self, text: str) -> None:
        if not text:
            return
        self.writer.write(text)
        self._last_visible_char = text[-1]

    def _print_exec_line(self, kind: str, content: str) -> None:
        clean = _clean_agent_tag_content(content)
        if not clean:
            return
        first = _first_nonempty_line(clean)
        summary = _shorten(first, 112)
        n_lines = len(clean.splitlines()) or 1

        self.writer.flush()
        if self._last_visible_char and self._last_visible_char != "\n":
            console.file.write("\n")
            console.file.flush()

        if kind == "execute":
            console.print(
                f"[cai.dim]  •[/cai.dim] [cai.exec]execute[/cai.exec] "
                f"[cai.dim]{n_lines} lines[/cai.dim] [cai.muted]{summary}[/cai.muted]"
            )
        # observation text is rendered by the observation event. If the backend
        # only streams tags and sends no event, this still avoids leaking tags.

        self._last_visible_char = "\n"

    def _process(self, *, final: bool) -> None:
        while self.pending:
            if self.hidden_mode:
                close_tag = f"</{self.hidden_mode}>"
                idx = self.pending.find(close_tag)
                if idx == -1:
                    if final:
                        self.hidden_chunks.append(self.pending)
                        self.pending = ""
                        self._print_exec_line(self.hidden_mode, "".join(self.hidden_chunks))
                        self.hidden_mode = None
                        self.hidden_chunks.clear()
                    else:
                        keep = min(len(self.pending), len(close_tag) - 1)
                        if keep:
                            self.hidden_chunks.append(self.pending[:-keep])
                            self.pending = self.pending[-keep:]
                        else:
                            self.hidden_chunks.append(self.pending)
                            self.pending = ""
                        break
                else:
                    self.hidden_chunks.append(self.pending[:idx])
                    self.pending = self.pending[idx + len(close_tag) :]
                    self._print_exec_line(self.hidden_mode, "".join(self.hidden_chunks))
                    self.hidden_mode = None
                    self.hidden_chunks.clear()
                continue

            candidates: list[tuple[int, str, str]] = []
            for tag, mode in self._open_tags.items():
                idx = self.pending.find(tag)
                if idx != -1:
                    candidates.append((idx, tag, mode))

            if not candidates:
                if final:
                    self._write_visible(self.pending)
                    self.pending = ""
                else:
                    keep = min(len(self.pending), self._max_open_len - 1)
                    if len(self.pending) > keep:
                        self._write_visible(self.pending[:-keep])
                        self.pending = self.pending[-keep:]
                    break
                continue

            idx, tag, mode = min(candidates, key=lambda item: item[0])
            self._write_visible(self.pending[:idx])
            self.pending = self.pending[idx + len(tag) :]
            self.hidden_mode = mode
            self.hidden_chunks.clear()


# ---------------------------------------------------------------------------
# Prompt toolkit session
# ---------------------------------------------------------------------------

def _create_prompt_session(status: Callable[[], str] | None = None) -> Any:
    """Create a PromptSession with history, completion, and key bindings."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style

    history_dir = str((WORKSPACE_DIR / "agent_workspace").resolve())
    os.makedirs(history_dir, exist_ok=True)
    history_path = os.path.join(history_dir, ".cli_history")

    commands = WordCompleter(
        [
            ":help",
            ":quit",
            ":exit",
            ":convs",
            ":load",
            ":new",
            ":ml",
            ":retry",
            ":rename",
            ":reset-kernel",
            ":clear",
            ":forget",
            ":delete",
            ":last_obs",
            ":history",
            ":tools",
        ],
        ignore_case=True,
        sentence=True,
    )

    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _submit_multiline(event):
        """Esc+Enter: submit the current buffer."""
        event.app.exit(result=event.app.current_buffer.text)

    @kb.add("c-j")
    def _insert_newline(event):
        """Ctrl+J: insert a newline in multiline buffers."""
        event.current_buffer.insert_text("\n")

    @kb.add("c-c")
    def _cancel_input(event):
        """Ctrl+C during input: cancel current line and return to prompt."""
        raise KeyboardInterrupt

    toolbar = None
    if status is not None:
        toolbar = lambda: status()

    return PromptSession(
        history=FileHistory(history_path),
        auto_suggest=AutoSuggestFromHistory(),
        completer=commands,
        complete_while_typing=True,
        key_bindings=kb,
        bottom_toolbar=toolbar,
        style=Style.from_dict(
            {
                "prompt": "fg:#58a6ff bold",
                "bottom-toolbar": "fg:#6e7681",
                "completion-menu.completion": "bg:#161b22 fg:#c9d1d9",
                "completion-menu.completion.current": "bg:#30363d fg:#ffffff",
                "auto-suggestion": "fg:#6e7681",
            }
        ),
    )


# ---------------------------------------------------------------------------
# Text cleaning and observation display
# ---------------------------------------------------------------------------

def _clean_agent_tag_content(content: str) -> str:
    return (
        content.replace("<observation>", "")
        .replace("</observation>", "")
        .replace("<execute>", "")
        .replace("</execute>", "")
        .strip()
    )


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "(empty)"


def _shorten(text: str, width: int = 100) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)].rstrip() + "…"


def _looks_failed(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "traceback",
            "error:",
            "exception",
            "failed",
            "exit code 1",
            "command not found",
        )
    )


def _display_observation(content: str, event: dict | None = None) -> None:
    """Compact inline observation display."""
    clean = _clean_agent_tag_content(content)
    lines = clean.splitlines() if clean else []
    n_lines = len(lines)
    summary = _shorten(_first_nonempty_line(clean), 118)
    failed = _looks_failed(clean)
    status_icon = "!" if failed else "✓"
    status_style = "cai.err" if failed else "cai.ok"

    tool = "execute"
    if event:
        tool = event.get("tool") or event.get("tool_name") or event.get("name") or tool

    console.print(
        f"[cai.dim]  {status_icon}[/cai.dim] "
        f"[{status_style}]{tool}[/{status_style}] "
        f"[cai.dim]{n_lines} lines[/cai.dim] "
        f"[cai.muted]{summary}[/cai.muted] "
        f"[cai.dim](:last_obs)[/cai.dim]"
    )


def _display_full_observation(raw_log: list[dict]) -> None:
    """Show the most recent complete observation content."""
    observations = [e for e in raw_log if e.get("type") == "observation"]
    if not observations:
        console.print("[cai.dim]No observations recorded.[/cai.dim]")
        return

    last = observations[-1]["content"]
    clean = _clean_agent_tag_content(last)
    console.print()
    console.print("[cai.dim]─ last observation ─[/cai.dim]")
    console.print(clean or "(empty)", markup=False, soft_wrap=True)
    console.print("[cai.dim]─ end ─[/cai.dim]")
    console.print()


# ---------------------------------------------------------------------------
# Streaming display
# ---------------------------------------------------------------------------

def _stream_and_display(agent: Any, prompt: str, history: list[dict]) -> StreamResult:
    raw_log: list[dict] = []
    full_message = ""
    renderer = _CodexStreamRenderer()

    console.print()
    try:
        for event in agent.run_with_history_streaming(prompt, history):
            ev_type = event.get("type")
            content = event.get("content", "")

            if ev_type == "token":
                full_message += content
                renderer.feed(content)

            elif ev_type == "message_end":
                full_message = content or full_message
                renderer.finish()
                console.print()

            elif ev_type == "observation":
                renderer.finish()
                raw_log.append({"type": "observation", "content": content})
                _display_observation(content, event)

            elif ev_type in ("tool_start", "execution_start", "command_start"):
                renderer.finish()
                tool = event.get("tool") or event.get("tool_name") or event.get("name") or "execute"
                detail = _shorten(str(event.get("command") or event.get("content") or ""), 112)
                suffix = f" [cai.muted]{detail}[/cai.muted]" if detail else ""
                console.print(f"[cai.dim]  •[/cai.dim] [cai.exec]{tool}[/cai.exec]{suffix}")

            elif ev_type in ("error", "tool_error", "execution_error"):
                renderer.finish()
                summary = _shorten(str(content), 120)
                console.print(f"[cai.dim]  ![/cai.dim] [cai.err]error[/cai.err] [cai.muted]{summary}[/cai.muted]")

    except KeyboardInterrupt:
        renderer.finish()
        raise StreamingInterrupted(partial=full_message, raw_log=raw_log)

    renderer.finish()
    console.print()
    return StreamResult(
        full_message=full_message,
        raw_log=raw_log,
        has_executions=bool(raw_log),
    )


# ---------------------------------------------------------------------------
# Multi-line editor (:ml mode)
# ---------------------------------------------------------------------------

def _multiline_input(session: Any) -> str | None:
    console.print("[cai.dim]multiline: Ctrl+J newline · Esc+Enter submit · Ctrl+C cancel[/cai.dim]")
    try:
        text = session.prompt(
            "› ",
            multiline=True,
            prompt_continuation="  ",
        )
    except KeyboardInterrupt:
        console.print("[cai.dim]cancelled[/cai.dim]")
        return None
    except EOFError:
        return None

    text = text.rstrip("\n")
    if not text.strip():
        return None
    return text


# ---------------------------------------------------------------------------
# History, help, and conversation listing
# ---------------------------------------------------------------------------

def _show_history(display_messages: list[dict], n: int) -> None:
    pairs: list[tuple[dict, dict]] = []
    i = 0
    while i < len(display_messages) - 1:
        if display_messages[i].get("role") == "user" and display_messages[i + 1].get("role") == "assistant":
            pairs.append((display_messages[i], display_messages[i + 1]))
            i += 2
        else:
            i += 1

    shown = pairs[-n:]
    if not shown:
        console.print("[cai.dim]No conversation history to display.[/cai.dim]")
        return

    for idx, (user_msg, ai_msg) in enumerate(shown, start=max(1, len(pairs) - len(shown) + 1)):
        console.print()
        console.print(f"[cai.dim]user {idx}[/cai.dim]")
        console.print(user_msg.get("content", ""), markup=False, soft_wrap=True)
        console.print(f"[cai.dim]assistant {idx}[/cai.dim]")
        ai_content = ai_msg.get("content", "")
        if ai_content:
            console.print(Markdown(ai_content, code_theme="ansi_dark"))
        if ai_msg.get("interrupted"):
            console.print("[cai.warn]! response was interrupted[/cai.warn]")
    console.print()


def _show_help() -> None:
    rows = [
        (":ml", "multi-line input"),
        (":retry", "rerun the last user request after removing the last turn"),
        (":history [n]", "render recent conversation history"),
        (":last_obs", "show full output from the last execution"),
        (":convs", "list recent sessions"),
        (":load <id>", "switch to a session"),
        (":new", "start a new session"),
        (":rename <title>", "rename current session"),
        (":reset-kernel", "reset the execution namespace"),
        (":clear", "clear terminal screen"),
        (":forget", "clear and persist empty history for this session"),
        (":quit", "exit"),
    ]
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), border_style="cai.border")
    table.add_column("cmd", style="cai.accent", no_wrap=True)
    table.add_column("desc", style="cai.muted")
    for row in rows:
        table.add_row(*row)
    console.print(table)


def _list_conversations(store: Any) -> None:
    convs = store.list_conversations()
    if not convs:
        console.print("[cai.dim]No conversations found.[/cai.dim]")
        return

    table = Table(box=box.SIMPLE, border_style="cai.border", show_lines=False)
    table.add_column("updated", style="cai.dim", width=16)
    table.add_column("id", style="cai.accent", no_wrap=True)
    table.add_column("title", style="cai.fg")
    table.add_column("msgs", justify="right", style="cai.dim")

    for c in convs[:15]:
        ts = c.get("updated_at", "")[:16].replace("T", " ")
        count = str(c.get("message_count", 0))
        title = c.get("title", "Untitled")
        cid = c.get("id", "")
        table.add_row(ts, cid, title, count)

    console.print(table)


# ---------------------------------------------------------------------------
# Utility maintenance and kernel reset
# ---------------------------------------------------------------------------

def _flush_cli_usage(agent: Any) -> None:
    try:
        registry = getattr(agent, "utility_registry", None)
        if registry is None:
            return
        from CAi.CAi_agent.execution.repl import flush_utility_usage

        usage = flush_utility_usage()
        if usage:
            registry.apply_usage(usage)
    except Exception:
        pass


def _reset_kernel() -> None:
    try:
        from CAi.CAi_agent.execution.repl import reset_namespace

        reset_namespace()
        console.print("[cai.ok]✓ kernel reset[/cai.ok]")
    except Exception as e:
        console.print(f"[cai.err]kernel reset failed:[/cai.err] {e}")


# ---------------------------------------------------------------------------
# Conversation loading and mutation helpers
# ---------------------------------------------------------------------------

def _load_conversation(store: Any, conv_id: str) -> tuple[list[dict], list[dict]] | None:
    conv = store.get_conversation(conv_id)
    if not conv:
        return None

    model_history: list[dict] = []
    display_messages: list[dict] = list(conv.get("messages", []))

    for m in display_messages:
        role = m.get("role")
        if role in ("user", "assistant"):
            from CAi.web_ui.backend.chat_service import clean_stored_answer

            clean = clean_stored_answer(m.get("content", "")) or m.get("content", "")
            if m.get("interrupted"):
                clean = f"{clean}\n\n[上条 assistant 回复被用户中断，内容可能不完整。]"
            model_history.append({"role": role, "content": clean})

    return model_history, display_messages


def _pop_last_turn(model_history: list[dict], display_messages: list[dict]) -> str | None:
    """Remove the last user+assistant turn and return the user prompt."""
    if len(model_history) < 2 or len(display_messages) < 2:
        return None

    # Walk display history backwards to find the last assistant preceded by user.
    for i in range(len(display_messages) - 1, 0, -1):
        if display_messages[i].get("role") == "assistant" and display_messages[i - 1].get("role") == "user":
            user_text = display_messages[i - 1].get("content", "")
            del display_messages[i - 1 : i + 1]
            break
    else:
        return None

    # Remove the last model turn if it has the usual user/assistant shape.
    if len(model_history) >= 2 and model_history[-1].get("role") == "assistant" and model_history[-2].get("role") == "user":
        model_history.pop()
        model_history.pop()

    return user_text


def _maybe_autotitle(store: Any, conv_id: str, display_messages: list[dict], user_input: str) -> None:
    # Give new CLI sessions useful names in :convs and Web UI without model calls.
    if len(display_messages) > 2:
        return
    title = _shorten(user_input.replace("\n", " "), 48)
    if not title:
        return
    try:
        store.update_title(conv_id, title)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# REPL main loop and entry point
# ---------------------------------------------------------------------------

def repl_loop(agent: Any, store: Any, workspace_dir: str, conv_id: str | None = None) -> None:
    from CAi.web_ui.backend.chat_service import clean_stored_answer

    current = {"conv_id": conv_id or ""}

    def status() -> str:
        cid = current["conv_id"][:8] if current["conv_id"] else "new"
        return f" conv {cid} · :help · :ml · :last_obs · Ctrl+D exit "

    session = _create_prompt_session(status=status)

    if conv_id:
        loaded = _load_conversation(store, conv_id)
        if loaded is None:
            console.print(f"[cai.err]conversation not found:[/cai.err] {conv_id}")
            meta = store.create_conversation()
            conv_id = meta["id"]
            model_history, display_messages = [], []
        else:
            model_history, display_messages = loaded
    else:
        meta = store.create_conversation()
        conv_id = meta["id"]
        model_history, display_messages = [], []

    current["conv_id"] = conv_id
    console.print(f"[cai.dim]session[/cai.dim] [cai.accent]{conv_id}[/cai.accent] [cai.dim]· :help[/cai.dim]")
    if model_history:
        console.print(f"[cai.dim]resumed {len(model_history) // 2} turns[/cai.dim]")
    console.print()

    last_raw_log: list[dict] = []
    last_user_input: str | None = None

    while True:
        try:
            user_input = session.prompt("› ")
        except KeyboardInterrupt:
            console.print("[cai.dim]cancelled[/cai.dim]")
            continue
        except EOFError:
            console.print("[cai.dim]bye[/cai.dim]")
            break

        if not user_input:
            continue

        stripped = user_input.strip()

        # Command routing
        if stripped in (":quit", ":exit"):
            console.print("[cai.dim]bye[/cai.dim]")
            break
        if stripped == ":help":
            _show_help()
            continue
        if stripped == ":convs":
            _list_conversations(store)
            continue
        if stripped.startswith(":load "):
            target_id = stripped[len(":load ") :].strip()
            loaded = _load_conversation(store, target_id)
            if loaded is None:
                console.print(f"[cai.err]conversation not found:[/cai.err] {target_id}")
            else:
                conv_id = target_id
                current["conv_id"] = conv_id
                model_history, display_messages = loaded
                console.print(f"[cai.ok]✓ switched[/cai.ok] [cai.accent]{target_id}[/cai.accent] [cai.dim]({len(model_history) // 2} turns)[/cai.dim]")
            continue
        if stripped == ":new":
            meta = store.create_conversation()
            conv_id = meta["id"]
            current["conv_id"] = conv_id
            model_history, display_messages = [], []
            console.print(f"[cai.ok]✓ new session[/cai.ok] [cai.accent]{conv_id}[/cai.accent]")
            continue
        if stripped == ":ml":
            user_input = _multiline_input(session)
            if user_input is None:
                continue
            stripped = user_input.strip()
        if stripped == ":retry":
            retry_input = _pop_last_turn(model_history, display_messages)
            if retry_input:
                store.save_messages(conv_id, display_messages)
                user_input = retry_input
                stripped = user_input.strip()
                console.print("[cai.dim]retrying previous request[/cai.dim]")
            elif last_user_input:
                user_input = last_user_input
                stripped = user_input.strip()
                console.print("[cai.dim]retrying previous request[/cai.dim]")
            else:
                console.print("[cai.warn]no previous message to retry[/cai.warn]")
                continue
        if stripped.startswith(":rename "):
            new_title = stripped[len(":rename ") :].strip()
            if new_title:
                store.update_title(conv_id, new_title)
                console.print(f"[cai.ok]✓ renamed[/cai.ok] {new_title}")
            continue
        if stripped == ":reset-kernel":
            _reset_kernel()
            continue
        if stripped == ":clear":
            console.clear()
            continue
        if stripped == ":forget":
            model_history, display_messages = [], []
            store.save_messages(conv_id, display_messages)
            console.print("[cai.ok]✓ conversation history cleared[/cai.ok]")
            continue
        if stripped == ":delete":
            store.delete_conversation(conv_id)
            meta = store.create_conversation()
            conv_id = meta["id"]
            current["conv_id"] = conv_id
            model_history, display_messages = [], []
            console.print(f"[cai.ok]✓ deleted; new session[/cai.ok] [cai.accent]{conv_id}[/cai.accent]")
            continue
        if stripped == ":last_obs":
            _display_full_observation(last_raw_log)
            continue
        if stripped.startswith(":history"):
            parts = stripped.split()
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 3
            _show_history(display_messages, n)
            continue
        if stripped == ":tools":
            tools = agent.list_tools() if hasattr(agent, "list_tools") else []
            if tools:
                for t in tools:
                    name = t if isinstance(t, str) else t.get("name", "?")
                    console.print(f"[cai.dim]•[/cai.dim] [cai.accent]{name}[/cai.accent]")
            else:
                console.print("[cai.dim]no tools loaded[/cai.dim]")
            continue

        # Build prompt and stream
        prompt = f"{user_input}\n\n[工作目录]: {workspace_dir}"
        last_user_input = user_input
        last_raw_log = []

        try:
            result = _stream_and_display(agent, prompt, model_history)
            last_raw_log = result.raw_log

        except StreamingInterrupted as exc:
            partial, raw_log = exc.partial, exc.raw_log
            last_raw_log = raw_log

            if partial:
                console.print("[cai.warn]! output interrupted[/cai.warn]")
                clean = clean_stored_answer(partial) or partial

                # Keep the display transcript complete, but make the model context
                # explicit that this assistant turn was interrupted.
                model_history.append({"role": "user", "content": user_input})
                model_history.append(
                    {
                        "role": "assistant",
                        "content": f"{clean}\n\n[上条 assistant 回复被用户中断，内容可能不完整。]",
                    }
                )

                display_messages.append({"role": "user", "content": user_input, "timestamp": datetime.now().isoformat()})
                display_messages.append(
                    {
                        "role": "assistant",
                        "content": partial,
                        "timestamp": datetime.now().isoformat(),
                        "interrupted": True,
                        "interrupted_at_char": len(partial),
                    }
                )
                store.save_messages(conv_id, display_messages)
            else:
                console.print("[cai.warn]! interrupted before any output[/cai.warn]")
            continue

        clean = clean_stored_answer(result.full_message) or result.full_message
        model_history.append({"role": "user", "content": user_input})
        model_history.append({"role": "assistant", "content": clean})

        display_messages.append({"role": "user", "content": user_input, "timestamp": datetime.now().isoformat()})
        display_messages.append({"role": "assistant", "content": result.full_message, "timestamp": datetime.now().isoformat()})

        store.save_messages(conv_id, display_messages)
        _maybe_autotitle(store, conv_id, display_messages, user_input)
        _flush_cli_usage(agent)


def _print_banner() -> None:
    console.print()
    showed_logo = _print_startup_logo()
    if showed_logo:
        console.print(Align.center("[cai.fg]terminal agent[/cai.fg] [cai.dim]· :help for commands[/cai.dim]"))
    else:
        console.print("[cai.accent]CAi Copilot[/cai.accent] [cai.dim]terminal agent[/cai.dim]")
        console.print("[cai.dim]type :help for commands[/cai.dim]")
    console.print()


def launch_cli(agent: Any, resume_conv_id: str | None = None) -> None:
    from CAi.CAi_agent.execution.repl import set_workspace_dir
    from CAi.web_ui.backend.conversation_store import ConversationStore

    workspace_dir = str((WORKSPACE_DIR / "agent_workspace").resolve())
    conversations_dir = str((WORKSPACE_DIR / "agent_workspace" / "_conversations").resolve())

    set_workspace_dir(workspace_dir)
    store = ConversationStore(conversations_dir)

    _print_banner()

    model_name = getattr(agent.llm, 'model_name', 'unknown')
    active_conv = resume_conv_id or "new"
    console.print(Align.center(f"[cai.fg]model[/cai.fg] {model_name} [cai.dim]·[/cai.dim] [cai.fg]session[/cai.fg] {active_conv[:10]} [cai.dim]· :help[/cai.dim]"))
    console.print()

    repl_loop(agent, store, workspace_dir, conv_id=resume_conv_id)
