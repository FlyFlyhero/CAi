"""Tests for the CLI tag-aware printer used by streaming.

The printer accumulates LLM tokens as they arrive and decides what's
safe to emit to the terminal: prose goes through verbatim, complete
``<execute>`` blocks get rendered separately, and partial tags are
held back until enough of the buffer has arrived to disambiguate.
"""

from __future__ import annotations

from CAi.cli.streaming import _TagAwarePrinter, _looks_like_partial_open_tag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CapturingConsole:
    """Stand-in for rich.Console — records strings written to it.

    Calls to ``console.print(...)`` are recorded as ``(text, kwargs)``.
    Rich renderables (Panel, Text, etc.) are unwrapped to a string by
    looking at their ``.renderable`` / ``.title`` / ``.plain`` attributes
    so assertions can search for the content the user would actually see.
    """

    def __init__(self) -> None:
        self.printed: list[tuple[str, dict]] = []

    def print(self, *args, **kwargs) -> None:
        text = " ".join(_to_text(a) for a in args) if args else ""
        self.printed.append((text, dict(kwargs)))


def _to_text(obj) -> str:
    """Best-effort flatten of a rich renderable into searchable text."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    parts: list[str] = []
    # rich Panel exposes the wrapped renderable on .renderable; the
    # title is a separate attribute. We look for both.
    title = getattr(obj, "title", None)
    if title is not None:
        parts.append(_to_text(title))
    inner = getattr(obj, "renderable", None)
    if inner is not None and inner is not obj:
        parts.append(_to_text(inner))
    plain = getattr(obj, "plain", None)
    if isinstance(plain, str):
        parts.append(plain)
    if not parts:
        return str(obj)
    return " ".join(p for p in parts if p)


def _patch_console(monkeypatch, capture: _CapturingConsole) -> None:
    """Redirect both display and streaming console writes into ``capture``."""
    from CAi.cli import display, streaming

    monkeypatch.setattr(display, "console", capture)
    monkeypatch.setattr(streaming, "console", capture)


# ---------------------------------------------------------------------------
# _looks_like_partial_open_tag
# ---------------------------------------------------------------------------


def test_partial_tag_detection_recognises_prefix():
    assert _looks_like_partial_open_tag("<")
    assert _looks_like_partial_open_tag("<e")
    assert _looks_like_partial_open_tag("<exec")
    assert _looks_like_partial_open_tag("<execute")


def test_partial_tag_detection_rejects_full_tag():
    # A complete tag has '>'; the printer should fall through to EXECUTE_RE.
    assert not _looks_like_partial_open_tag("<execute>")
    assert not _looks_like_partial_open_tag('<execute lang="bash">')


def test_partial_tag_detection_rejects_unrelated():
    assert not _looks_like_partial_open_tag("<div")
    assert not _looks_like_partial_open_tag("hello<")  # not a leading tag


# ---------------------------------------------------------------------------
# Pure-prose flow
# ---------------------------------------------------------------------------


def test_pure_prose_emits_after_finish(monkeypatch):
    cap = _CapturingConsole()
    _patch_console(monkeypatch, cap)

    p = _TagAwarePrinter()
    p.feed("Hello, world!")
    p.finish()

    full = "".join(text for text, _ in cap.printed)
    assert "Hello, world!" in full


def test_prose_holdback_keeps_potential_tag_buffered(monkeypatch):
    """While streaming, an unfinished '<exe' must NOT leak to the user."""
    cap = _CapturingConsole()
    _patch_console(monkeypatch, cap)

    p = _TagAwarePrinter()
    p.feed("Thinking now <exe")  # partial tag at the end

    so_far = "".join(text for text, _ in cap.printed)
    assert "<exe" not in so_far


# ---------------------------------------------------------------------------
# Code blocks
# ---------------------------------------------------------------------------


def test_complete_python_block_renders(monkeypatch):
    cap = _CapturingConsole()
    _patch_console(monkeypatch, cap)

    p = _TagAwarePrinter()
    p.feed("Let me compute. <execute>print(1+1)</execute> Result: 2.")
    p.finish()

    rendered = "".join(text for text, _ in cap.printed)
    # Prose is preserved
    assert "Let me compute." in rendered
    assert "Result: 2." in rendered
    # Code body shows up via the panel render
    assert "print(1+1)" in rendered
    # Tags themselves are stripped from the prose stream
    assert "<execute>" not in rendered
    assert "</execute>" not in rendered


def test_bash_block_with_lang_attribute(monkeypatch):
    cap = _CapturingConsole()
    _patch_console(monkeypatch, cap)

    p = _TagAwarePrinter()
    p.feed('Run shell: <execute lang="bash">ls -la</execute> Done.')
    p.finish()

    rendered = "".join(text for text, _ in cap.printed)
    assert "ls -la" in rendered
    assert "<execute" not in rendered
    # Panel title should reflect the bash language.
    assert any("Bash" in text for text, _ in cap.printed)


def test_legacy_bash_shebang_still_works(monkeypatch):
    """Existing conversations stored before the lang attribute keep working."""
    cap = _CapturingConsole()
    _patch_console(monkeypatch, cap)

    p = _TagAwarePrinter()
    p.feed("<execute>#!BASH\necho hi</execute>")
    p.finish()

    rendered = "".join(text for text, _ in cap.printed)
    assert "echo hi" in rendered
    # The shebang itself is consumed by detect_lang, not displayed in
    # the panel body.
    assert "#!BASH" not in rendered


def test_multiple_blocks_in_one_message(monkeypatch):
    cap = _CapturingConsole()
    _patch_console(monkeypatch, cap)

    p = _TagAwarePrinter()
    p.feed(
        "Step 1: <execute>x = 1</execute>"
        "Step 2: <execute lang=\"bash\">echo hi</execute>"
        "Done."
    )
    p.finish()

    rendered = "".join(text for text, _ in cap.printed)
    assert "x = 1" in rendered
    assert "echo hi" in rendered
    assert "Done." in rendered


# ---------------------------------------------------------------------------
# Streaming arrival order
# ---------------------------------------------------------------------------


def test_block_split_across_many_token_arrivals(monkeypatch):
    """Tokens arrive in arbitrarily small chunks — no leak, correct render."""
    cap = _CapturingConsole()
    _patch_console(monkeypatch, cap)

    p = _TagAwarePrinter()
    full = 'Hi <execute lang="bash">ls -la</execute> bye'
    # Feed one character at a time to stress the holdback logic.
    for ch in full:
        p.feed(ch)
    p.finish()

    rendered = "".join(text for text, _ in cap.printed)
    assert "Hi " in rendered
    assert " bye" in rendered
    assert "ls -la" in rendered
    assert "<execute" not in rendered
    assert "</execute>" not in rendered


def test_finish_emits_unterminated_partial_tag_as_prose(monkeypatch):
    """If the stream truly ended mid-tag, we surface what we have rather
    than swallowing it forever."""
    cap = _CapturingConsole()
    _patch_console(monkeypatch, cap)

    p = _TagAwarePrinter()
    p.feed("Hello <exec")
    p.finish()

    rendered = "".join(text for text, _ in cap.printed)
    # The partial fragment is now safe to print since stream ended.
    assert "Hello " in rendered
    assert "<exec" in rendered
