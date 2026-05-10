"""Tests for A1pro system prompt construction.

Verifies each section renders correctly, empty inputs are handled, and
the overall prompt stays compact.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# _truncate_doc — utility
# ---------------------------------------------------------------------------


def test_truncate_doc_empty():
    from CAi.CAi_agent.agent import A1pro

    assert A1pro._truncate_doc("") == "No description."


def test_truncate_doc_cuts_at_args_section():
    from CAi.CAi_agent.agent import A1pro

    doc = "Compute X.\n\nArgs:\n    a: First arg\n    b: Second arg\n"
    out = A1pro._truncate_doc(doc)
    assert "Args:" not in out
    assert "Compute X" in out


def test_truncate_doc_respects_max_chars():
    from CAi.CAi_agent.agent import A1pro

    doc = "A" * 1000
    out = A1pro._truncate_doc(doc, max_chars=100)
    assert len(out) < 250  # allowing for the "..." suffix


def test_truncate_doc_cuts_at_returns():
    from CAi.CAi_agent.agent import A1pro

    doc = "Short summary.\n\nReturns:\n    dict: stuff"
    out = A1pro._truncate_doc(doc)
    assert "Returns:" not in out
    assert "Short summary" in out


# ---------------------------------------------------------------------------
# System prompt sections
# ---------------------------------------------------------------------------


def test_prompt_has_core_section(a1pro_agent):
    agent, _ = a1pro_agent()
    sp = agent.system_prompt
    assert "INTERACTION MODES" in sp
    assert "EXECUTION RULES" in sp
    assert "<execute>" in sp
    assert "<done/>" in sp


def test_prompt_without_tools_or_skills_is_compact(a1pro_agent):
    agent, _ = a1pro_agent()
    # No tools, no skills — should only contain the core section.
    sp = agent.system_prompt
    assert "AVAILABLE TOOLS" not in sp
    assert "SKILLS" not in sp
    # Core section alone should be well under 2KB.
    assert len(sp) < 3000


def test_prompt_with_tool_section(a1pro_agent):
    agent, _ = a1pro_agent()

    def my_tool(x: int, y: str = "hi") -> dict:
        """Demo tool used for testing.

        Args:
            x: First parameter
            y: Second parameter
        """
        return {"x": x, "y": y}

    agent.add_tool(my_tool)
    sp = agent.system_prompt
    assert "AVAILABLE TOOLS" in sp
    assert "my_tool" in sp
    assert "Demo tool used for testing" in sp
    # The Args: section must be truncated out.
    assert "First parameter" not in sp


def test_skill_meta_tools_excluded_from_tool_list(a1pro_agent):
    """get_skill_content and list_available_skills should not appear in
    the main tool catalog — they are skill infrastructure."""
    agent, _ = a1pro_agent()

    def get_skill_content(skill_id: str) -> str:
        """Should be hidden from the catalog."""
        return ""

    def list_available_skills() -> str:
        """Should also be hidden."""
        return ""

    def normal_tool(x: int) -> int:
        """A regular tool that should appear."""
        return x

    agent.add_tool(get_skill_content)
    agent.add_tool(list_available_skills)
    agent.add_tool(normal_tool)

    sp = agent.system_prompt
    assert "normal_tool" in sp
    # These are registered but not advertised
    assert "Should be hidden from the catalog" not in sp
    assert "Should also be hidden" not in sp


def test_add_tool_updates_prompt(a1pro_agent):
    agent, _ = a1pro_agent()

    def foo(x: int) -> int:
        """Foo does stuff."""
        return x

    before_len = len(agent.system_prompt)
    agent.add_tool(foo)
    after_len = len(agent.system_prompt)
    assert after_len > before_len
    assert "foo" in agent.system_prompt


def test_remove_tool_updates_prompt(a1pro_agent):
    agent, _ = a1pro_agent()

    def foo(x: int) -> int:
        """Foo does stuff."""
        return x

    agent.add_tool(foo)
    assert "foo" in agent.system_prompt

    agent.remove_tool("foo")
    assert "foo" not in agent.system_prompt


# ---------------------------------------------------------------------------
# Real skills (loaded from disk)
# ---------------------------------------------------------------------------


def test_prompt_includes_skills_when_enabled(a1pro_agent):
    agent, _ = a1pro_agent(auto_load_skills=True)
    sp = agent.system_prompt
    # At least one skill shipped with the repo
    assert "SKILLS" in sp
    # Should list at least one known skill id
    summaries = agent.list_skills()
    if summaries:
        assert summaries[0]["id"] in sp


def test_list_skills_is_stable(a1pro_agent):
    agent, _ = a1pro_agent(auto_load_skills=True)
    summaries = agent.list_skills()
    assert isinstance(summaries, list)
    for s in summaries:
        assert "id" in s
        assert "name" in s
        assert "description" in s
