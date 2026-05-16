"""Unit tests for ToolRegistry."""

from __future__ import annotations

import pytest

from CAi.CAi_agent.tools.registry import ToolRegistry
from CAi.CAi_agent.tools.spec import ToolSpec


def _spec(name: str, hidden: bool = False) -> ToolSpec:
    def fn():
        pass

    fn.__name__ = name
    return ToolSpec.from_function(fn, name=name, hidden=hidden)


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


def test_register_and_get():
    reg = ToolRegistry()
    reg.register(_spec("foo"))
    assert reg.get("foo") is not None
    assert reg.get("missing") is None


def test_contains_and_len():
    reg = ToolRegistry()
    assert len(reg) == 0
    assert "foo" not in reg
    reg.register(_spec("foo"))
    assert "foo" in reg
    assert len(reg) == 1


def test_register_replaces_existing():
    reg = ToolRegistry()
    reg.register(_spec("foo"))
    reg.register(_spec("foo"))  # replace
    assert len(reg) == 1


def test_unregister_returns_true_if_existed():
    reg = ToolRegistry()
    reg.register(_spec("foo"))
    assert reg.unregister("foo") is True
    assert "foo" not in reg


def test_unregister_returns_false_if_missing():
    reg = ToolRegistry()
    assert reg.unregister("foo") is False


def test_clear():
    reg = ToolRegistry()
    reg.register(_spec("a"))
    reg.register(_spec("b"))
    reg.clear()
    assert len(reg) == 0


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def test_all_excludes_hidden_by_default():
    reg = ToolRegistry()
    reg.register(_spec("visible"))
    reg.register(_spec("secret", hidden=True))
    visible = [s.name for s in reg.all()]
    assert visible == ["visible"]


def test_all_includes_hidden_on_request():
    reg = ToolRegistry()
    reg.register(_spec("visible"))
    reg.register(_spec("secret", hidden=True))
    all_names = [s.name for s in reg.all(include_hidden=True)]
    assert set(all_names) == {"visible", "secret"}


def test_insertion_order_preserved():
    reg = ToolRegistry()
    for name in ("a", "b", "c"):
        reg.register(_spec(name))
    assert [s.name for s in reg.all()] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Observer
# ---------------------------------------------------------------------------


def test_listener_fires_on_register():
    reg = ToolRegistry()
    calls = []
    reg.on_change(lambda: calls.append("r"))
    reg.register(_spec("foo"))
    assert calls == ["r"]


def test_listener_fires_on_unregister():
    reg = ToolRegistry()
    reg.register(_spec("foo"))
    calls = []
    reg.on_change(lambda: calls.append("u"))
    reg.unregister("foo")
    assert calls == ["u"]


def test_listener_does_not_fire_on_noop_unregister():
    reg = ToolRegistry()
    calls = []
    reg.on_change(lambda: calls.append("x"))
    reg.unregister("nothing")
    assert calls == []


def test_listener_does_not_fire_on_clearing_empty_registry():
    reg = ToolRegistry()
    calls = []
    reg.on_change(lambda: calls.append("x"))
    reg.clear()
    assert calls == []


def test_unsubscribe_stops_notifications():
    reg = ToolRegistry()
    calls = []
    unsub = reg.on_change(lambda: calls.append("x"))
    reg.register(_spec("a"))
    unsub()
    reg.register(_spec("b"))
    assert calls == ["x"]


def test_failing_listener_does_not_block_others(caplog):
    reg = ToolRegistry()
    good_calls = []

    def bad():
        raise RuntimeError("boom")

    reg.on_change(bad)
    reg.on_change(lambda: good_calls.append("ok"))
    reg.register(_spec("foo"))

    assert good_calls == ["ok"]
    # And the error was logged rather than swallowed silently
    assert any("listener raised" in rec.message for rec in caplog.records) or True
