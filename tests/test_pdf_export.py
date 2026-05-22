"""Tests for the PDF export module.

The Markdown rendering is pure and fully tested here. The actual PDF
conversion is mocked at the `_invoke_converter` level so the tests
don't depend on weasyprint / pandoc being installed in CI.
"""

from __future__ import annotations

import os

import pytest

from CAi.web_ui.backend import pdf_export
from CAi.web_ui.backend.pdf_export import (
    EmptyConversation,
    PdfEngineUnavailable,
    _reformat_agent_tags,
    export_conversation_to_pdf,
    render_conversation_markdown,
)


# ---------------------------------------------------------------------------
# _reformat_agent_tags
# ---------------------------------------------------------------------------


def test_reformat_strips_done_tag():
    assert _reformat_agent_tags("Final answer. <done/>") == "Final answer."


def test_reformat_execute_becomes_python_fence():
    out = _reformat_agent_tags("<execute>print(1)</execute>")
    assert "```python" in out
    assert "print(1)" in out


def test_reformat_execute_detects_bash():
    out = _reformat_agent_tags("<execute>#!BASH\nls -la</execute>")
    assert "```bash" in out
    assert "ls -la" in out
    assert "#!BASH" not in out


def test_reformat_execute_detects_r():
    out = _reformat_agent_tags("<execute>#!R\nlibrary(dplyr)</execute>")
    assert "```r" in out
    assert "library(dplyr)" in out


def test_reformat_observation_becomes_quoted_block():
    out = _reformat_agent_tags("<observation>\nresult=42\n</observation>")
    assert "**Output:**" in out
    assert "> result=42" in out


def test_reformat_multiple_tags_in_one_message():
    content = (
        "Let me compute.\n"
        "<execute>x = 1</execute>\n"
        "<observation>1</observation>\n"
        "All done. <done/>"
    )
    out = _reformat_agent_tags(content)
    assert "Let me compute" in out
    assert "```python" in out
    assert "**Output:**" in out
    assert "All done." in out
    assert "<done/>" not in out


def test_reformat_collapses_blank_lines():
    out = _reformat_agent_tags("line\n\n\n\n\n\nnext")
    assert "\n\n\n" not in out


# ---------------------------------------------------------------------------
# render_conversation_markdown
# ---------------------------------------------------------------------------


def test_render_empty_conversation_raises():
    with pytest.raises(EmptyConversation):
        render_conversation_markdown({"messages": []})


def test_render_missing_messages_key_raises():
    with pytest.raises(EmptyConversation):
        render_conversation_markdown({})


def test_render_basic_conversation():
    conv = {
        "id": "abc",
        "title": "My chat",
        "created_at": "2025-01-01T12:00:00",
        "messages": [
            {"role": "user", "content": "Hello", "timestamp": "2025-01-01T12:00:00"},
            {"role": "assistant", "content": "Hi there!", "timestamp": "2025-01-01T12:00:05"},
        ],
    }
    out = render_conversation_markdown(conv)
    assert out.startswith("# My chat")
    assert "2025-01-01T12:00:00" in out
    assert "## User" in out
    assert "## Assistant" in out
    assert "Hello" in out
    assert "Hi there!" in out


def test_render_reformats_agent_tags_inline():
    conv = {
        "messages": [
            {"role": "user", "content": "compute 6*7"},
            {"role": "assistant", "content": "<execute>print(6*7)</execute>"},
            {"role": "assistant", "content": "<observation>42</observation>"},
        ]
    }
    out = render_conversation_markdown(conv)
    assert "```python" in out
    assert "print(6*7)" in out
    assert "> 42" in out


def test_render_skips_malformed_messages():
    conv = {
        "messages": [
            {"role": "user", "content": "keep me"},
            "not a dict",
            {"role": "user", "content": 123},
            None,
            {"role": "assistant", "content": "also keep"},
        ]
    }
    out = render_conversation_markdown(conv)
    assert "keep me" in out
    assert "also keep" in out
    assert "123" not in out


def test_render_falls_back_to_default_title():
    conv = {"messages": [{"role": "user", "content": "hi"}]}
    out = render_conversation_markdown(conv)
    assert out.startswith("# Conversation")


# ---------------------------------------------------------------------------
# export_conversation_to_pdf — converter mocked
# ---------------------------------------------------------------------------


def _install_fake_converter(monkeypatch, impl):
    """Replace _invoke_converter on the module so tests control conversion."""
    monkeypatch.setattr(pdf_export, "_invoke_converter", impl)


def test_export_calls_converter_with_correct_paths(tmp_path, monkeypatch):
    captured = {}

    def fake_convert(md_path, pdf_path):
        captured["md_path"] = md_path
        captured["pdf_path"] = pdf_path
        with open(md_path, encoding="utf-8") as f:
            captured["md_content"] = f.read()
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-fake")

    _install_fake_converter(monkeypatch, fake_convert)

    conv = {
        "title": "T",
        "messages": [{"role": "user", "content": "hi"}],
    }
    out = tmp_path / "test.pdf"
    result = export_conversation_to_pdf(conv, str(out))

    assert result == str(out)
    assert out.exists()
    assert "hi" in captured["md_content"]
    # Temp markdown should have been cleaned up
    assert not os.path.exists(captured["md_path"])


def test_export_surfaces_engine_unavailable(tmp_path, monkeypatch):
    def fake_convert(md_path, pdf_path):
        raise PdfEngineUnavailable("nothing available")

    _install_fake_converter(monkeypatch, fake_convert)

    conv = {"messages": [{"role": "user", "content": "hi"}]}
    with pytest.raises(PdfEngineUnavailable, match="nothing available"):
        export_conversation_to_pdf(conv, str(tmp_path / "x.pdf"))


def test_export_empty_conversation_raises(tmp_path, monkeypatch):
    _install_fake_converter(
        monkeypatch,
        lambda md, pdf: pytest.fail("shouldn't be called"),
    )
    with pytest.raises(EmptyConversation):
        export_conversation_to_pdf({"messages": []}, str(tmp_path / "x.pdf"))


# ---------------------------------------------------------------------------
# _invoke_converter backend selection
# ---------------------------------------------------------------------------


def test_invoke_picks_weasyprint_first(tmp_path, monkeypatch):
    calls = []

    def fake_weasy(md, pdf):
        calls.append(("weasy", md, pdf))

    def fake_pandoc(md, pdf):
        calls.append(("pandoc", md, pdf))

    monkeypatch.setattr(pdf_export, "_weasyprint_convert", fake_weasy)
    monkeypatch.setattr(pdf_export, "_pandoc_convert", fake_pandoc)

    pdf_export._invoke_converter(str(tmp_path / "x.md"), str(tmp_path / "x.pdf"))
    assert calls == [("weasy", str(tmp_path / "x.md"), str(tmp_path / "x.pdf"))]


def test_invoke_falls_back_to_pandoc_when_weasy_missing(tmp_path, monkeypatch):
    calls = []

    def fake_weasy(md, pdf):
        raise pdf_export._WeasyprintMissing()

    def fake_pandoc(md, pdf):
        calls.append("pandoc")

    monkeypatch.setattr(pdf_export, "_weasyprint_convert", fake_weasy)
    monkeypatch.setattr(pdf_export, "_pandoc_convert", fake_pandoc)

    pdf_export._invoke_converter("a.md", "a.pdf")
    assert calls == ["pandoc"]


def test_invoke_reports_native_missing_without_fallback(tmp_path, monkeypatch):
    """If weasyprint is installed but native libs are missing, we surface
    that directly instead of silently falling back."""

    def fake_weasy(md, pdf):
        raise pdf_export._WeasyprintNativeMissing("libpango not found")

    def fake_pandoc(md, pdf):
        pytest.fail("should not fall back when weasyprint is present")

    monkeypatch.setattr(pdf_export, "_weasyprint_convert", fake_weasy)
    monkeypatch.setattr(pdf_export, "_pandoc_convert", fake_pandoc)

    with pytest.raises(PdfEngineUnavailable, match="Pango"):
        pdf_export._invoke_converter("a.md", "a.pdf")


def test_invoke_fails_when_all_backends_missing(tmp_path, monkeypatch):
    def missing_weasy(md, pdf):
        raise pdf_export._WeasyprintMissing()

    def missing_pandoc(md, pdf):
        raise pdf_export._PandocMissing()

    monkeypatch.setattr(pdf_export, "_weasyprint_convert", missing_weasy)
    monkeypatch.setattr(pdf_export, "_pandoc_convert", missing_pandoc)

    with pytest.raises(PdfEngineUnavailable, match="No PDF backend"):
        pdf_export._invoke_converter("a.md", "a.pdf")
