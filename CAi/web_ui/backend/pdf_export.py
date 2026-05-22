"""Conversation → Markdown → PDF export.

Designed to be independent of the agent implementation: it reads a
conversation dict (as returned by ConversationStore.get_conversation) and
produces a PDF file on disk.

PDF conversion is attempted in order:
    weasyprint  → best typography, requires Pango/Cairo native libs
    pandoc      → system command, if installed

If none of these are usable, export_conversation_to_pdf raises
PdfEngineUnavailable with an actionable message.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PdfEngineUnavailable(RuntimeError):
    """Raised when no PDF conversion backend is usable on this system."""


class EmptyConversation(ValueError):
    """Raised when there's nothing to export."""


# ---------------------------------------------------------------------------
# Markdown rendering (pure, easily testable)
# ---------------------------------------------------------------------------


def render_conversation_markdown(
    conv: dict[str, Any],
    workspace_dir: str | None = None,
) -> str:
    """Render a conversation dict as a Markdown document.

    Shape of `conv` (matches ConversationStore.get_conversation):
        {
            "id": str,
            "title": str,
            "created_at": str (ISO),
            "updated_at": str (ISO),
            "messages": [
                {"role": "user"|"assistant", "content": str, "timestamp": str},
                ...
            ]
        }

    Internal agent tags (<execute>, <observation>, <done/>) are reformatted:
      - <execute>...</execute>  → fenced code block
      - <observation>...</observation> → "> Output:" blockquote (with embedded plots
        when `workspace_dir` is given)
      - <done/>                → stripped
    """
    messages = conv.get("messages") or []
    if not messages:
        raise EmptyConversation("Conversation has no messages to export.")

    title = conv.get("title") or "Conversation"
    created = conv.get("created_at") or ""

    lines: list[str] = [
        f"# {title}",
        "",
    ]
    if created:
        lines.append(f"*Created: {created}*")
        lines.append("")

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        ts = msg.get("timestamp", "")

        header = "## User" if role == "user" else "## Assistant"
        if ts:
            header += f"  _({ts})_"
        lines.append(header)
        lines.append("")
        lines.append(_reformat_agent_tags(content, workspace_dir=workspace_dir))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


_EXECUTE_RE = re.compile(r"<execute>(.*?)</execute>", re.DOTALL)
_OBS_RE = re.compile(r"<observation>(.*?)</observation>", re.DOTALL)
_DONE_RE = re.compile(r"<done\s*/?>")
_IMAGE_LINE_RE = re.compile(r"\[Image saved\]:\s*(.+)", re.IGNORECASE)


def _reformat_agent_tags(text: str, workspace_dir: str | None = None) -> str:
    """Convert internal <execute>/<observation>/<done> tags into Markdown.

    If `workspace_dir` is provided, '[Image saved]: path' lines inside
    observations are replaced with markdown image references so PDF export
    can embed plots.
    """

    def _code(match):
        code = match.group(1).strip()
        # Detect language from marker
        if code.startswith("#!BASH"):
            lang = "bash"
            code = code.replace("#!BASH", "", 1).lstrip()
        elif code.startswith("#!R"):
            lang = "r"
            code = code.replace("#!R", "", 1).lstrip()
        else:
            lang = "python"
        return f"\n```{lang}\n{code}\n```\n"

    def _obs(match):
        body = match.group(1).strip()
        out_lines: list[str] = []
        text_buffer: list[str] = []

        def flush_text():
            if text_buffer:
                quoted = "\n".join(
                    f"> {line}" if line else ">" for line in text_buffer
                )
                out_lines.append(quoted)
                text_buffer.clear()

        for line in body.split("\n"):
            img_match = _IMAGE_LINE_RE.match(line.strip())
            if img_match and workspace_dir:
                flush_text()
                fname = os.path.basename(img_match.group(1).strip())
                fpath = os.path.join(workspace_dir, fname)
                if os.path.exists(fpath):
                    # weasyprint accepts file:// URLs and absolute paths
                    out_lines.append(f"\n![{fname}]({fpath.replace(os.sep, '/')})\n")
                else:
                    text_buffer.append(line)
            else:
                text_buffer.append(line)
        flush_text()

        return "\n**Output:**\n\n" + "\n".join(out_lines) + "\n"

    text = _EXECUTE_RE.sub(_code, text)
    text = _OBS_RE.sub(_obs, text)
    text = _DONE_RE.sub("", text)
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


# ---------------------------------------------------------------------------
# PDF conversion
# ---------------------------------------------------------------------------


def export_conversation_to_pdf(
    conv: dict[str, Any],
    output_path: str,
    workspace_dir: str | None = None,
) -> str:
    """Render `conv` to Markdown, then convert to PDF at `output_path`.

    Returns the output path. Raises PdfEngineUnavailable if no PDF backend
    works, or EmptyConversation if the conversation has no messages.

    `workspace_dir` enables embedded plot images in observation blocks.
    """
    markdown_text = render_conversation_markdown(conv, workspace_dir=workspace_dir)

    # Write to a temp .md file — convert_markdown_to_pdf expects a path
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
    try:
        tmp.write(markdown_text)
        tmp.close()
        _invoke_converter(tmp.name, output_path)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    return output_path


def _invoke_converter(md_path: str, pdf_path: str) -> None:
    """Convert `md_path` → `pdf_path` using the first backend that works.

    Tries weasyprint first (best typography), then pandoc. Raises
    PdfEngineUnavailable if neither is usable.
    """
    # 1) weasyprint
    try:
        return _weasyprint_convert(md_path, pdf_path)
    except _WeasyprintMissing:
        pass  # weasyprint not installed — try next backend
    except _WeasyprintNativeMissing as e:
        # Installed but native DLLs (Pango / Cairo) missing. This is
        # the most common Windows failure — surface it clearly and
        # don't fall back, because the user clearly wanted weasyprint.
        raise PdfEngineUnavailable(
            "weasyprint is installed but its native dependencies "
            "(Pango / Cairo) could not be loaded. On Windows see: "
            "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html"
            f"\nUnderlying error: {e}"
        ) from e

    # 2) pandoc fallback
    try:
        return _pandoc_convert(md_path, pdf_path)
    except _PandocMissing:
        pass

    raise PdfEngineUnavailable(
        "No PDF backend available on this system. Install one of:\n"
        "  - weasyprint (pip install weasyprint, recommended)\n"
        "  - pandoc (system binary)"
    )


# ---------------------------------------------------------------------------
# weasyprint backend
# ---------------------------------------------------------------------------


class _WeasyprintMissing(Exception):
    """weasyprint isn't installed at all."""


class _WeasyprintNativeMissing(Exception):
    """weasyprint is importable but native deps (Pango / Cairo) aren't loadable."""


def _weasyprint_convert(md_path: str, pdf_path: str) -> None:
    """Render Markdown → styled HTML → PDF with weasyprint."""
    try:
        from weasyprint import HTML  # noqa: F401 — used below
    except ImportError as e:
        raise _WeasyprintMissing() from e
    except OSError as e:
        # On Windows, weasyprint raises OSError when ctypes can't find
        # libpango-1.0-0.dll etc. at import time.
        raise _WeasyprintNativeMissing(str(e)) from e
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if any(hint in msg for hint in ("dll", "pango", "cairo", "gobject")):
            raise _WeasyprintNativeMissing(str(e)) from e
        raise

    try:
        import markdown as _md
    except ImportError as e:
        raise _WeasyprintMissing(
            "weasyprint requires the 'markdown' package: pip install markdown"
        ) from e

    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration

    with open(md_path, encoding="utf-8") as f:
        md_content = f.read()

    html_body = _md.markdown(md_content, extensions=["fenced_code", "tables"])
    html_doc = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{_PDF_CSS}</style></head><body>"
        f"{html_body}"
        "</body></html>"
    )
    HTML(string=html_doc).write_pdf(
        pdf_path,
        font_config=FontConfiguration(),
        optimize_images=True,
    )


# ---------------------------------------------------------------------------
# pandoc backend
# ---------------------------------------------------------------------------


class _PandocMissing(Exception):
    """pandoc isn't installed."""


def _pandoc_convert(md_path: str, pdf_path: str) -> None:
    try:
        subprocess.run(
            ["pandoc", md_path, "-o", pdf_path],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        if isinstance(e, FileNotFoundError):
            raise _PandocMissing() from e
        # pandoc exists but failed — surface as PDF-engine error
        raise PdfEngineUnavailable(
            f"pandoc failed while converting to PDF: {e.stderr.decode(errors='replace')}"
        ) from e


# ---------------------------------------------------------------------------
# Stylesheet for the weasyprint backend
# ---------------------------------------------------------------------------

_PDF_CSS = """
body {
    font-family: sans-serif;
    font-size: 10pt;
    line-height: 1.45;
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
    color: #2c3e50;
}
h1, h2, h3, h4 {
    font-family: sans-serif;
    color: #1f2d3d;
    margin-top: 1.0em;
    margin-bottom: 0.4em;
}
h1 { font-size: 16pt; border-bottom: 2px solid #3498db; padding-bottom: 6px; }
h2 { font-size: 13pt; border-bottom: 1px solid #bdc3c7; padding-bottom: 3px; }
h3 { font-size: 11pt; }
h4 { font-size: 10pt; }
code {
    background-color: #f4f6f8;
    padding: 1px 4px;
    border-radius: 2px;
    font-family: 'Menlo', 'Consolas', monospace;
    font-size: 9pt;
}
pre {
    background-color: #f4f6f8;
    padding: 10px;
    border-radius: 3px;
    border-left: 3px solid #3498db;
    overflow-x: auto;
    white-space: pre-wrap;
    word-wrap: break-word;
    font-size: 9pt;
}
pre code { background: transparent; padding: 0; font-size: 9pt; }
blockquote {
    border-left: 3px solid #bdc3c7;
    margin: 0.5em 0;
    padding-left: 15px;
    color: #4a5968;
    font-size: 9pt;
}
table { border-collapse: collapse; width: 100%; font-size: 9pt; margin: 0.5em 0; }
th, td { border: 1px solid #bdc3c7; padding: 4px 8px; text-align: left; }
th { background-color: #ecf0f1; font-weight: bold; }
img { max-width: 100%; height: auto; }
p { margin: 0.3em 0; }
"""
