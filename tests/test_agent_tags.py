"""Tests for the central tag-parsing module.

Covers the public surface of CAi.CAi_agent.agent_tags so every consumer
(BaseAgent, web backend, CLI, PDF export) gets the same parsing
guarantees.
"""

from __future__ import annotations

import pytest

from CAi.CAi_agent.agent_tags import (
    DONE_RE,
    EXECUTE_RE,
    OBSERVATION_RE,
    SUPPORTED_LANGS,
    ExecuteBlock,
    detect_lang,
    has_execute_block,
    iter_execute_blocks,
    parse_attrs,
    strip_all_tags,
    strip_done,
    wrap_observation,
)


# ---------------------------------------------------------------------------
# parse_attrs
# ---------------------------------------------------------------------------


def test_parse_attrs_empty():
    assert parse_attrs("") == {}
    assert parse_attrs(None or "") == {}


def test_parse_attrs_single():
    # The leading space is what EXECUTE_RE captures in the `attrs` group.
    assert parse_attrs(' lang="bash"') == {"lang": "bash"}


def test_parse_attrs_multiple():
    out = parse_attrs(' lang="python" timeout="60" id="abc"')
    assert out == {"lang": "python", "timeout": "60", "id": "abc"}


def test_parse_attrs_hyphenated_key():
    # Future-proofing: keys with hyphens should round-trip.
    assert parse_attrs(' data-purpose="docking"') == {"data-purpose": "docking"}


# ---------------------------------------------------------------------------
# detect_lang
# ---------------------------------------------------------------------------


def test_detect_lang_attribute_wins():
    lang, code = detect_lang("ls -la", "bash")
    assert lang == "bash"
    assert code == "ls -la"


def test_detect_lang_unknown_attribute_falls_back_to_python():
    lang, code = detect_lang("print(1)", "ruby")
    assert lang == "python"
    assert code == "print(1)"


def test_detect_lang_legacy_bash_shebang():
    lang, code = detect_lang("#!BASH\nls -la")
    assert lang == "bash"
    assert code == "ls -la"
    assert "#!BASH" not in code


def test_detect_lang_legacy_r_shebang():
    lang, code = detect_lang("#!R\nlibrary(dplyr)")
    assert lang == "r"
    assert code == "library(dplyr)"


def test_detect_lang_legacy_shebang_case_insensitive():
    lang, _ = detect_lang("#!bash\nls")
    assert lang == "bash"


def test_detect_lang_no_marker_defaults_python():
    lang, code = detect_lang("print(1)")
    assert lang == "python"
    assert code == "print(1)"


def test_detect_lang_attribute_overrides_legacy_shebang():
    # If the LLM redundantly emits both, the attribute is authoritative.
    lang, _ = detect_lang("#!BASH\nls", "python")
    assert lang == "python"


def test_supported_langs_constant():
    assert "python" in SUPPORTED_LANGS
    assert "bash" in SUPPORTED_LANGS
    assert "r" in SUPPORTED_LANGS


# ---------------------------------------------------------------------------
# iter_execute_blocks
# ---------------------------------------------------------------------------


def test_iter_no_blocks():
    assert list(iter_execute_blocks("hello world")) == []


def test_iter_single_default_block():
    blocks = list(iter_execute_blocks("<execute>print(1)</execute>"))
    assert len(blocks) == 1
    assert isinstance(blocks[0], ExecuteBlock)
    assert blocks[0].lang == "python"
    assert blocks[0].code == "print(1)"
    assert blocks[0].attrs == {}


def test_iter_block_with_lang_attribute():
    text = '<execute lang="bash">ls -la</execute>'
    blocks = list(iter_execute_blocks(text))
    assert len(blocks) == 1
    assert blocks[0].lang == "bash"
    assert blocks[0].code == "ls -la"
    assert blocks[0].attrs == {"lang": "bash"}


def test_iter_block_with_legacy_shebang():
    text = "<execute>#!BASH\nls -la</execute>"
    blocks = list(iter_execute_blocks(text))
    assert len(blocks) == 1
    assert blocks[0].lang == "bash"
    assert blocks[0].code == "ls -la"


def test_iter_multiple_blocks():
    text = (
        "First step:\n<execute>x = 1</execute>\n"
        "Now bash:\n<execute lang=\"bash\">echo hi</execute>"
    )
    blocks = list(iter_execute_blocks(text))
    assert len(blocks) == 2
    assert blocks[0].lang == "python"
    assert blocks[0].code == "x = 1"
    assert blocks[1].lang == "bash"
    assert blocks[1].code == "echo hi"


def test_iter_preserves_unknown_attributes():
    text = '<execute lang="python" purpose="docking" id="b1">do_it()</execute>'
    blocks = list(iter_execute_blocks(text))
    assert blocks[0].attrs["purpose"] == "docking"
    assert blocks[0].attrs["id"] == "b1"


def test_iter_handles_multiline_code():
    code = "import json\n\ndef f(x):\n    return json.dumps(x)\nprint(f({}))"
    text = f"<execute>{code}</execute>"
    blocks = list(iter_execute_blocks(text))
    assert blocks[0].code == code.strip()


# ---------------------------------------------------------------------------
# has_execute_block
# ---------------------------------------------------------------------------


def test_has_execute_block_true():
    assert has_execute_block("<execute>print(1)</execute>")
    assert has_execute_block('<execute lang="bash">ls</execute>')


def test_has_execute_block_false_for_unclosed():
    # While streaming, the closing tag may not have arrived yet — we
    # don't want has_execute_block() to return True until the block is
    # actually complete.
    assert not has_execute_block("<execute>print(1)")


def test_has_execute_block_false_for_plain_text():
    assert not has_execute_block("Just talking about <execute> conceptually.")


# ---------------------------------------------------------------------------
# strip_done / strip_all_tags / wrap_observation
# ---------------------------------------------------------------------------


def test_strip_done_removes_self_closing():
    assert strip_done("All done. <done/>") == "All done. "


def test_strip_done_removes_with_extra_whitespace():
    assert strip_done("ok<done    />continue") == "okcontinue"


def test_strip_done_leaves_other_text():
    assert strip_done("nothing to remove") == "nothing to remove"


def test_strip_all_tags_removes_everything():
    text = (
        "I'll compute it.\n"
        "<execute>x = 1+2</execute>\n"
        "<observation>3</observation>\n"
        "Answer: 3 <done/>"
    )
    cleaned = strip_all_tags(text)
    assert "<execute>" not in cleaned
    assert "<observation>" not in cleaned
    assert "<done/>" not in cleaned
    assert "I'll compute it." in cleaned
    assert "Answer: 3" in cleaned


def test_strip_all_tags_preserves_attributes_message():
    text = '<execute lang="bash">ls</execute>'
    assert strip_all_tags(text) == ""


def test_wrap_observation_round_trip():
    body = "result = 42"
    wrapped = wrap_observation(body)
    match = OBSERVATION_RE.search(wrapped)
    assert match is not None
    assert match.group("body").strip() == body


# ---------------------------------------------------------------------------
# Regex sanity
# ---------------------------------------------------------------------------


def test_execute_re_named_groups():
    match = EXECUTE_RE.search('<execute lang="r">x</execute>')
    assert match.group("attrs") == ' lang="r"'
    assert match.group("code") == "x"


def test_done_re_matches_variants():
    for variant in ("<done/>", "<done />", "<done  />", "<done>"):
        # `<done>` (no slash) intentionally matches per the regex; it's
        # a tolerated form some models produce.
        assert DONE_RE.search(variant) is not None


@pytest.mark.parametrize(
    "code,expected",
    [
        ("import os", "python"),
        ("#!BASH\necho hi", "bash"),
        ("#!R\nx <- 1", "r"),
        ("", "python"),
    ],
)
def test_detect_lang_no_attribute_table(code, expected):
    lang, _ = detect_lang(code)
    assert lang == expected
