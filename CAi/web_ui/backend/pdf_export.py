"""Conversation → Markdown → PDF export.

Designed to be independent of the agent implementation: it reads a
conversation dict (as returned by ConversationStore.get_conversation) and
produces a PDF file on disk.

The actual PDF conversion is delegated to base_CAi.utils.convert_markdown_to_pdf,
which tries weasyprint → markdown2pdf → pandoc in that order. If none of
these are usable on the current system, export_conversation_to_pdf raises
a PdfEngineUnavailable with an actionable message.
"""

from __future__ import annotations

import os
import re
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


def render_conversation_markdown(conv: dict[str, Any]) -> str:
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
      - <observation>...</observation> → "> Output:" blockquote
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

        header = "## 🧑 User" if role == "user" else "## 🤖 Assistant"
        if ts:
            header += f"  _({ts})_"
        lines.append(header)
        lines.append("")
        lines.append(_reformat_agent_tags(content))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


_EXECUTE_RE = re.compile(r"<execute>(.*?)</execute>", re.DOTALL)
_OBS_RE = re.compile(r"<observation>(.*?)</observation>", re.DOTALL)
_DONE_RE = re.compile(r"<done\s*/?>")


def _reformat_agent_tags(text: str) -> str:
    """Convert internal <execute>/<observation>/<done> tags into Markdown."""

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
        # Quote each line so it renders as an output block
        quoted = "\n".join(f"> {line}" if line else ">" for line in body.split("\n"))
        return f"\n**Output:**\n\n{quoted}\n"

    text = _EXECUTE_RE.sub(_code, text)
    text = _OBS_RE.sub(_obs, text)
    text = _DONE_RE.sub("", text)
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


# ---------------------------------------------------------------------------
# PDF conversion
# ---------------------------------------------------------------------------


def export_conversation_to_pdf(conv: dict[str, Any], output_path: str) -> str:
    """Render `conv` to Markdown, then convert to PDF at `output_path`.

    Returns the output path. Raises PdfEngineUnavailable if no PDF backend
    works, or EmptyConversation if the conversation has no messages.
    """
    markdown_text = render_conversation_markdown(conv)

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
    """Call base_CAi.utils.convert_markdown_to_pdf and translate failures
    into a single PdfEngineUnavailable with guidance.
    """
    try:
        from base_CAi.utils import convert_markdown_to_pdf
    except ImportError as e:
        raise PdfEngineUnavailable(f"convert_markdown_to_pdf unavailable: {e}") from e

    try:
        convert_markdown_to_pdf(md_path, pdf_path)
    except ImportError as e:
        # This is the explicit signal from the upstream helper that no
        # backend (weasyprint, markdown2pdf, pandoc) could be used.
        raise PdfEngineUnavailable(
            "No PDF backend available on this system. Install one of:\n"
            "  - weasyprint (recommended)\n"
            "  - markdown2pdf\n"
            "  - pandoc (system binary)"
        ) from e
    except Exception as e:
        # weasyprint on Windows commonly fails at import time due to missing
        # native DLLs (libpango, libcairo). Surface that clearly.
        msg = str(e).lower()
        if "dll" in msg or "pango" in msg or "cairo" in msg or "gobject" in msg:
            raise PdfEngineUnavailable(
                "PDF backend (weasyprint) is installed but its native "
                "dependencies (Pango / Cairo) are missing. On Windows see: "
                "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html"
                f"\nUnderlying error: {e}"
            ) from e
        raise
