"""Unit tests for ReplBridge — registry ↔ builtins namespace sync."""

from __future__ import annotations

import builtins

import pytest

from CAi.CAi_agent.tools.registry import ToolRegistry
from CAi.CAi_agent.tools.repl_bridge import ReplBridge, _NAMESPACE_ATTR
from CAi.CAi_agent.tools.spec import ToolSpec


@pytest.fixture(autouse=True)
def _clean_ns():
    """Reset the global REPL namespace around every test."""
    if hasattr(builtins, _NAMESPACE_ATTR):
        delattr(builtins, _NAMESPACE_ATTR)
    yield
    if hasattr(builtins, _NAMESPACE_ATTR):
        delattr(builtins, _NAMESPACE_ATTR)


def _spec(name: str, fn=None, hidden: bool = False) -> ToolSpec:
    if fn is None:
        def fn():  # noqa: E306
            return name
    fn.__name__ = name
    return ToolSpec.from_function(fn, name=name, hidden=hidden)


def _ns() -> dict:
    return getattr(builtins, _NAMESPACE_ATTR, {})


def test_bridge_creates_namespace_on_init():
    reg = ToolRegistry()
    ReplBridge(reg)
    assert hasattr(builtins, _NAMESPACE_ATTR)
    assert _ns() == {}


def test_register_flows_to_namespace():
    reg = ToolRegistry()
    ReplBridge(reg)
    reg.register(_spec("hello"))
    assert "hello" in _ns()


def test_unregister_removes_from_namespace():
    reg = ToolRegistry()
    ReplBridge(reg)
    reg.register(_spec("hello"))
    reg.unregister("hello")
    assert "hello" not in _ns()


def test_hidden_tools_are_still_injected():
    """Hidden only means 'don't advertise', not 'don't expose'."""
    reg = ToolRegistry()
    ReplBridge(reg)
    reg.register(_spec("secret", hidden=True))
    assert "secret" in _ns()


def test_function_is_callable_from_namespace():
    def my_fn():
        return 42

    reg = ToolRegistry()
    ReplBridge(reg)
    reg.register(ToolSpec.from_function(my_fn))
    assert _ns()["my_fn"]() == 42


def test_detach_stops_syncing():
    reg = ToolRegistry()
    bridge = ReplBridge(reg)
    reg.register(_spec("a"))
    bridge.detach()
    reg.register(_spec("b"))
    assert "a" in _ns()
    assert "b" not in _ns()


def test_clear_removes_all():
    reg = ToolRegistry()
    ReplBridge(reg)
    reg.register(_spec("a"))
    reg.register(_spec("b"))
    reg.clear()
    assert _ns() == {}


def test_preexisting_namespace_is_reused():
    """If something already created the namespace, we should preserve it
    by clearing it on sync rather than creating a parallel dict."""
    ns = {"pre_existing": lambda: None}
    setattr(builtins, _NAMESPACE_ATTR, ns)
    reg = ToolRegistry()
    ReplBridge(reg)
    # After priming on init, the bridge synced an empty registry into the
    # same dict — the `ns` reference should still point at builtins ns.
    assert _ns() is ns
