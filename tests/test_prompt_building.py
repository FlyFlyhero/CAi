"""Integration tests for A1pro system prompt.

Verifies that the composed prompt has the right sections, adapts to tool
changes, and stays compact when empty.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# System prompt sections — integration level
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
    sp = agent.system_prompt
    assert "AVAILABLE TOOLS" not in sp
    assert "SKILLS" not in sp
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
    # Args: section must be truncated out
    assert "First parameter" not in sp


def test_hidden_tools_do_not_appear_in_prompt(a1pro_agent):
    """Tools registered with hidden=True are callable but invisible in the catalog."""
    agent, _ = a1pro_agent()

    def secret_helper(x: int) -> int:
        """Secret: should be hidden."""
        return x

    def normal_tool(x: int) -> int:
        """A regular tool that should appear."""
        return x

    agent.add_tool(secret_helper, hidden=True)
    agent.add_tool(normal_tool)

    sp = agent.system_prompt
    assert "normal_tool" in sp
    assert "secret_helper" not in sp
    assert "Secret: should be hidden" not in sp


def test_hidden_tools_are_still_listable(a1pro_agent):
    agent, _ = a1pro_agent()

    def h(x: int) -> int:
        """hidden"""
        return x

    agent.add_tool(h, hidden=True)
    assert "h" not in agent.list_tools()
    assert "h" in agent.list_tools(include_hidden=True)


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


def test_remove_nonexistent_tool_returns_false(a1pro_agent):
    agent, _ = a1pro_agent()
    assert agent.remove_tool("does_not_exist") is False


# ---------------------------------------------------------------------------
# Real skills (loaded from disk)
# ---------------------------------------------------------------------------


def test_prompt_includes_skills_when_enabled(a1pro_agent):
    agent, _ = a1pro_agent(auto_load_skills=True)
    sp = agent.system_prompt
    assert "SKILLS" in sp
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
