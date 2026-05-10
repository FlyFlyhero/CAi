"""Tests for BaseAgent / A1pro execution behaviour.

Covers:
- Stateless conversation history (no duplication, no leakage)
- Code execution via <execute> tags
- Tool registration and REPL injection
"""

from __future__ import annotations

import builtins

import pytest


# ---------------------------------------------------------------------------
# Stateless history handling
# ---------------------------------------------------------------------------


def test_single_turn_no_history(a1pro_agent):
    agent, fake = a1pro_agent(responses=["Hi there!"])
    steps = list(agent.run_with_history("Hello!", history=[]))

    # One AI message produced
    assert len(steps) == 1
    assert steps[0]["content"] == "Hi there!"
    # LLM saw system + user
    assert len(fake.last_messages) == 2


def test_second_turn_with_history_has_no_duplication(a1pro_agent):
    """Regression test for the checkpointer-collision bug that was fixed."""
    agent, fake = a1pro_agent(responses=["Yes I remember."])
    history = [
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    steps = list(agent.run_with_history("Do you remember me?", history=history))

    # Only the new AI message is yielded — not the replayed history
    assert len(steps) == 1

    # LLM saw: system + user1 + assistant1 + user2 = 4 messages
    msgs = fake.last_messages
    assert len(msgs) == 4

    # Verify the order and content of history replay
    contents = [m.content for m in msgs]
    assert "Hello!" in contents[1]
    assert "Hi there!" in contents[2]
    assert "Do you remember me?" in contents[3]


def test_no_leakage_between_calls(a1pro_agent):
    """Two independent calls with empty history should each see only their
    own user message — no state persists across calls."""
    agent, fake = a1pro_agent(responses=["first reply", "second reply"])

    list(agent.run_with_history("first question", history=[]))
    len_after_1 = len(fake.last_messages)

    list(agent.run_with_history("second question", history=[]))
    len_after_2 = len(fake.last_messages)

    # Both calls: 1 system + 1 user = 2 messages
    assert len_after_1 == 2
    assert len_after_2 == 2


def test_last_state_attribute_is_not_set(a1pro_agent):
    """The agent must not expose _last_state — this was a concurrency hazard."""
    agent, _ = a1pro_agent(responses=["ok"])
    list(agent.run_with_history("hi", history=[]))
    assert not hasattr(agent, "_last_state")


def test_malformed_history_entries_are_skipped(a1pro_agent):
    """History with missing/invalid entries shouldn't crash the agent."""
    agent, fake = a1pro_agent(responses=["ok"])
    bad_history = [
        {"role": "user", "content": "valid"},
        {"role": "unknown", "content": "skipped"},
        {"role": "user", "content": 123},  # non-string
        {"role": "assistant"},  # no content field → defaults to empty string
    ]
    list(agent.run_with_history("test", history=bad_history))

    # Should have: system + valid_user + assistant(empty) + new_user = 4
    # The 'unknown' and non-string entries are silently dropped.
    contents = [m.content for m in fake.last_messages]
    assert any("valid" in c for c in contents)
    assert "skipped" not in " ".join(str(c) for c in contents)


# ---------------------------------------------------------------------------
# Code execution
# ---------------------------------------------------------------------------


def test_execute_block_runs_python_code(a1pro_agent):
    agent, fake = a1pro_agent(
        responses=[
            "<execute>print(6*7)</execute>",
            "The answer is 42. <done/>",
        ]
    )
    steps = list(agent.run_with_history("compute", history=[]))

    # We expect at minimum: ai_reply, observation, final_answer
    # (the generator yields each new message as it arrives)
    all_content = "\n".join(s["content"] for s in steps)
    assert "42" in all_content  # output captured in observation
    assert "<observation>" in all_content


def test_registered_tool_is_callable_in_repl(a1pro_agent):
    agent, fake = a1pro_agent(
        responses=[
            "<execute>print(my_cool_tool(5))</execute>",
            "done <done/>",
        ]
    )

    def my_cool_tool(x: int) -> int:
        """Doubles its input."""
        return x * 2

    agent.add_tool(my_cool_tool)
    steps = list(agent.run_with_history("use it", history=[]))
    all_content = "\n".join(s["content"] for s in steps)
    # Output from print(my_cool_tool(5)) should be 10
    assert "10" in all_content


# ---------------------------------------------------------------------------
# Tool registration / removal
# ---------------------------------------------------------------------------


class TestToolRegistration:
    def test_add_tool_registers_in_builtins(self, a1pro_agent):
        agent, _ = a1pro_agent()

        def unique_fn_xyz(n: int) -> int:
            return n

        agent.add_tool(unique_fn_xyz)
        custom = getattr(builtins, "_base_CAi_custom_functions", {})
        assert "unique_fn_xyz" in custom

    def test_remove_tool_cleans_builtins(self, a1pro_agent):
        agent, _ = a1pro_agent()

        def tmp_fn(n: int) -> int:
            return n

        agent.add_tool(tmp_fn)
        agent.remove_tool("tmp_fn")
        custom = getattr(builtins, "_base_CAi_custom_functions", {})
        assert "tmp_fn" not in custom

    def test_remove_nonexistent_tool_is_noop(self, a1pro_agent):
        agent, _ = a1pro_agent()
        # Should not raise
        agent.remove_tool("does_not_exist")


# ---------------------------------------------------------------------------
# Autouse cleanup: reset the global REPL namespace between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_custom_functions():
    """Prevent test leakage through the process-global REPL namespace."""
    yield
    if hasattr(builtins, "_base_CAi_custom_functions"):
        builtins._base_CAi_custom_functions.clear()
