"""Display utilities for CLI output formatting."""

from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text
from rich.style import Style
from rich.color import Color

from CAi.CAi_agent.agent_tags import OBSERVATION_RE, strip_all_tags
from CAi.cli.theme import console


def _strip_observation_tags(text: str) -> str:
    """Strip ``<observation>...</observation>`` wrappers, leaving the body.

    Goes through :data:`agent_tags.OBSERVATION_RE` so the CLI stays in
    lock-step with the rest of the system and any future attributes
    keep working.
    """
    if not text:
        return ""
    match = OBSERVATION_RE.search(text)
    if match:
        return match.group("body").strip()
    # Either plain text, or a malformed/partial tag.
    return strip_all_tags(text).strip()



def print_banner() -> None:
    """Display startup banner with 3D shadow and horizontal gradient styling."""
    # 严格修正 o 字母（去掉 E 的横杠）后的像素矩阵
    logo_raw = [
        "  ██████╗  █████╗  ██╗    ██████╗  ██████╗ ██████╗ ██╗██╗     ██████╗ ████████╗",
        "  ██╔════╝ ██╔══██╗██║    ██╔════╝ ██╔═══██╗██╔══██╗██║██║    ██╔═══██╗╚══██╔══╝",
        "  ██║      ███████║██║    ██║      ██║   ██║██████╔╝██║██║    ██║   ██║   ██║   ",
        "  ██║      ██╔══██║██║    ██║      ██║   ██║██╔═══╝ ██║██║    ██║   ██║   ██║   ",
        "  ╚██████╗ ██║  ██║██║    ╚██████╗ ╚██████╔╝██║     ██║███████╚██████╔╝   ██║   ",
        "   ╚═════╝ ╚═╝  ╚═╝╚═╝     ╚═════╝  ╚═════╝ ╚═╝     ╚═╝╚══════╝╚═════╝    ╚═╝   ",
        "   ░░░░░░░ ░░░  ░░░░░░     ░░░░░░░  ░░░░░░░ ░░░     ░░░░░░░░░░░░░░░░░░    ░░░   ",
        "                                                                               "
    ]

    c1_rgb = (92, 224, 216)   # #5ce0d8 (亮青)
    c2_rgb = (60, 200, 112)   # #3cc870 (翠绿)
    shadow_color = "#115249"  # 暗绿阴影

    console.print()
    
    max_len = max(len(line) for line in logo_raw) if logo_raw else 1

    for line in logo_raw:
        text = Text()
        for x, char in enumerate(line):
            # 1. 渲染底部颗粒阴影
            if char == "░":
                text.append(char, style=f"bold {shadow_color}")
            # 2. 渲染框架底边
            elif char in ("╚", "═", "╝"):
                text.append(char, style=f"dim {shadow_color}")
            # 3. 空白字符直接添加
            elif char == " ":
                text.append(char)
            # 4. 主体文字字符，通过元组无缝计算渐变 RGB
            else:
                blend_ratio = x / max_len
                r = int(c1_rgb[0] + (c2_rgb[0] - c1_rgb[0]) * blend_ratio)
                g = int(c1_rgb[1] + (c2_rgb[1] - c1_rgb[1]) * blend_ratio)
                b = int(c1_rgb[2] + (c2_rgb[2] - c1_rgb[2]) * blend_ratio)
                
                pixel_style = Style(color=Color.from_rgb(r, g, b), bold=True)
                text.append(char, style=pixel_style)

        console.print(text)

    console.print()
    console.print("  [cai.dim]Computational Chemistry & Drug Discovery Agent[/cai.dim]")
    console.print()

def print_session_info(conv_id: str, model_name: str, workspace_dir: str) -> None:
    """Display session initialization info."""
    console.print(f"  [cai.dim]session[/cai.dim]   [cai.primary]{conv_id[:12]}[/cai.primary]")
    console.print(f"  [cai.dim]engine[/cai.dim]    [cai.primary]{model_name}[/cai.primary]")
    console.print(f"  [cai.dim]workspace[/cai.dim] [cai.text]{workspace_dir}[/cai.text]")
    console.print()
    console.print("  [cai.dim]Type [cai.secondary]:help[/cai.secondary] for commands │ [cai.secondary]:quit[/cai.secondary] to exit[/cai.dim]")
    console.print()


def print_resumed_info(turn_count: int) -> None:
    """Display resumed session info."""
    console.print(f"  [cai.accent]✔ resumed ({turn_count} turns)[/cai.accent]")
    console.print()


def display_observation(content: str) -> None:
    """Display execution output — shows the full result of code execution."""
    clean = _strip_observation_tags(content)
    if not clean:
        console.print("   [cai.cyan]⚙[/cai.cyan] [cai.dim](no output)[/cai.dim]")
        return

    # Truncate very long output for readability, full version via :last_obs
    lines = clean.split("\n")
    MAX_LINES = 40
    truncated = len(lines) > MAX_LINES
    display_text = "\n".join(lines[:MAX_LINES])
    if truncated:
        display_text += f"\n\n… ({len(lines) - MAX_LINES} more lines, use :last_obs to see all)"

    console.print()
    console.print(
        Panel(
            display_text,
            title="[bold cai.cyan]⚙ Output[/bold cai.cyan]",
            title_align="left",
            border_style="#3e4452",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )
    console.print()


def display_full_observation(raw_log: list[dict]) -> None:
    """Show the most recent complete observation content."""
    observations = [e for e in raw_log if e.get("type") == "observation"]
    if not observations:
        console.print("[cai.dim]  No observations recorded.[/cai.dim]")
        return
    
    last = observations[-1]["content"]
    clean = _strip_observation_tags(last)

    console.print()
    console.print(
        Panel(
            clean,
            title="[bold cai.cyan]⚙ Observation Output[/bold cai.cyan]",
            title_align="left",
            subtitle="[cai.dim]:last_obs to view again[/cai.dim]",
            subtitle_align="right",
            border_style="#3e4452",
            box=box.HEAVY_HEAD,
            padding=(1, 2),
        )
    )
    console.print()


def display_ai_response_start() -> None:
    """Display visual separator before AI response."""
    console.print()
    console.print("  [cai.dim]┌─ [/cai.dim][bold cai.secondary]CAi[/bold cai.secondary]")
    console.print("  [cai.dim]│[/cai.dim]")


def display_ai_response_end() -> None:
    """Display visual separator after AI response."""
    console.print()
    console.print("  [cai.dim]└───────────────────────────────────────[/cai.dim]")
    console.print()


def display_history(display_messages: list[dict], n: int) -> None:
    """Display conversation history."""
    pairs: list[tuple[dict, dict]] = []
    i = 0
    while i < len(display_messages) - 1:
        if display_messages[i].get("role") == "user":
            pairs.append((display_messages[i], display_messages[i + 1]))
            i += 2
        else:
            i += 1

    shown = pairs[-n:]
    if not shown:
        console.print("[cai.dim]  No conversation history.[/cai.dim]")
        return

    console.print()
    for idx, (user_msg, ai_msg) in enumerate(shown):
        console.print(f"  [bold cai.cyan]You[/bold cai.cyan] [cai.dim](turn {idx + 1})[/cai.dim]")
        console.print(f"  [cai.text]{user_msg.get('content', '')}[/cai.text]")
        console.print()

        ai_content = ai_msg.get("content", "")
        if ai_content:
            console.print(f"  [bold cai.secondary]CAi[/bold cai.secondary]")
            console.print(Markdown(ai_content, code_theme="monokai"))
        if ai_msg.get("interrupted"):
            console.print("  [cai.warn]⚠ interrupted[/cai.warn]")
        console.print()
    console.print()


def display_conversations(convs: list[dict]) -> None:
    """Display list of saved conversations."""
    if not convs:
        console.print("[cai.dim]  No conversations found.[/cai.dim]")
        return

    console.print()
    table = Table(
        box=box.SIMPLE_HEAVY,
        border_style="#3e4452",
        title="[bold cai.secondary]Sessions[/bold cai.secondary]",
        title_justify="left",
        show_edge=False,
        pad_edge=True,
        padding=(0, 1),
    )

    table.add_column("Updated", style="#5c6370", width=12)
    table.add_column("ID", style="bold #61afef", max_width=12)
    table.add_column("Title", style="#abb2bf")
    table.add_column("Msgs", justify="right", style="#5c6370", width=5)

    for c in convs[:15]:
        ts = c.get("updated_at", "")[:10]
        count = str(c.get("message_count", 0))
        title = c.get("title", "Untitled")
        cid = c.get("id", "")[:10]
        table.add_row(ts, cid, title, count)

    console.print(table)
    console.print()


def display_tools(tools: list) -> None:
    """Display available tools."""
    if not tools:
        console.print("[cai.dim]  no tools loaded.[/cai.dim]")
        return
    
    console.print()
    console.print("  [bold cai.secondary]Available Tools[/bold cai.secondary]")
    for t in tools:
        name = t if isinstance(t, str) else t.get("name", "?")
        console.print(f"   [cai.cyan]◆[/cai.cyan] [cai.text]{name}[/cai.text]")
    console.print()


def display_help() -> None:
    """Display available commands."""
    commands = [
        (":quit, :q", "Exit the session"),
        (":help, :h", "Show this help"),
        (":new", "Start a new conversation"),
        (":convs", "List saved conversations"),
        (":load <id>", "Load a conversation"),
        (":ml", "Multi-line input mode"),
        (":retry", "Retry last message"),
        (":history [n]", "Show last n turns (default: 3)"),
        (":last_obs", "Show last execution output"),
        (":tools", "List available tools"),
        (":rename <t>", "Rename current session"),
        (":forget", "Clear memory (keep session)"),
        (":delete", "Delete session & start new"),
        (":reset-kernel", "Reset REPL kernel"),
        (":clear", "Clear terminal screen"),
    ]

    console.print()
    table = Table(
        box=box.SIMPLE,
        show_header=False,
        border_style="#3e4452",
        padding=(0, 2),
        pad_edge=True,
    )
    table.add_column("Command", style="bold #56b6c2", min_width=18)
    table.add_column("Description", style="#abb2bf")

    for cmd, desc in commands:
        table.add_row(cmd, desc)

    console.print(
        Panel(
            table,
            title="[bold cai.secondary]Commands[/bold cai.secondary]",
            title_align="left",
            border_style="#3e4452",
            box=box.ROUNDED,
            padding=(0, 1),
            expand=False,
        )
    )
    console.print()
