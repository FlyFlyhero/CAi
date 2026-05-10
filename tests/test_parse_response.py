"""Unit tests for BaseAgent._parse_response.

These tests don't need a real LLM or even a constructed agent — we call the
method on an unbound descriptor or on a bare instance.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


# We bypass BaseAgent.__init__ (which builds a LangGraph) by constructing an
# object via __new__ and invoking the bound method manually.
def _parser():
    from CAi.CAi_agent.base import BaseAgent

    obj = BaseAgent.__new__(BaseAgent)
    return obj._parse_response


def _fake_resp(content):
    return SimpleNamespace(content=content)


# ---------------------------------------------------------------------------
# Basic cases
# ---------------------------------------------------------------------------


def test_plain_text_ends():
    content, step = _parser()(_fake_resp("Hello there!"))
    assert content == "Hello there!"
    assert step == "end"


def test_execute_block_triggers_execute():
    text = "Let me compute this.\n<execute>print(1+1)</execute>"
    content, step = _parser()(_fake_resp(text))
    assert step == "execute"
    assert "<execute>" in content and "</execute>" in content


def test_done_tag_ends():
    content, step = _parser()(_fake_resp("All finished. <done/>"))
    assert step == "end"
    assert "<done/>" in content


def test_mixed_text_and_execute_prefers_execute():
    """If a response has both text and code, we must run the code."""
    text = "Here is my plan.\nStep 1: do X.\n<execute>print('X')</execute>"
    _, step = _parser()(_fake_resp(text))
    assert step == "execute"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_unclosed_execute_is_auto_closed():
    """The </execute> stop sequence can leave an open tag — we repair it."""
    text = "<execute>print(1)"
    content, step = _parser()(_fake_resp(text))
    assert step == "execute"
    assert content.endswith("</execute>")


def test_content_is_stripped():
    content, _ = _parser()(_fake_resp("   \n  hello  \n  "))
    assert content == "hello"


def test_list_content_is_joined():
    """Some LLMs (e.g. Anthropic) return content as a list of blocks."""
    blocks = [
        {"type": "text", "text": "Part A. "},
        {"type": "text", "text": "Part B."},
    ]
    content, step = _parser()(_fake_resp(blocks))
    assert "Part A" in content and "Part B" in content
    assert step == "end"


def test_list_content_with_execute():
    blocks = [
        {"type": "text", "text": "Let me try.\n"},
        {"type": "text", "text": "<execute>x = 1</execute>"},
    ]
    _, step = _parser()(_fake_resp(blocks))
    assert step == "execute"


def test_non_string_list_items_are_skipped_gracefully():
    """Malformed blocks shouldn't crash the parser."""
    blocks = [
        {"type": "text", "text": "ok"},
        {"type": "image", "url": "http://..."},  # no 'text' key
        None,  # weird, but shouldn't crash
    ]
    content, _ = _parser()(_fake_resp(blocks))
    assert "ok" in content


def test_empty_response_ends():
    content, step = _parser()(_fake_resp(""))
    assert content == ""
    assert step == "end"


def test_multiple_execute_blocks_still_triggers_execute():
    text = "<execute>a=1</execute>\nAnd then:\n<execute>b=2</execute>"
    _, step = _parser()(_fake_resp(text))
    assert step == "execute"
