"""Tests for CAi.CAi_agent.execution.repl."""

from __future__ import annotations

import builtins

import pytest

from CAi.CAi_agent.execution.repl import (
    _CUSTOM_FNS_ATTR,
    inject_custom_functions,
    reset_namespace,
    run_python_repl,
)


@pytest.fixture(autouse=True)
def _clean_repl_state():
    """Fresh namespace + clean builtins registry per test."""
    reset_namespace()
    if hasattr(builtins, _CUSTOM_FNS_ATTR):
        delattr(builtins, _CUSTOM_FNS_ATTR)
    yield
    reset_namespace()
    if hasattr(builtins, _CUSTOM_FNS_ATTR):
        delattr(builtins, _CUSTOM_FNS_ATTR)


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------


def test_captures_stdout():
    out = run_python_repl("print('hello')")
    assert out.strip() == "hello"


def test_evaluates_expression_without_print():
    """IPython kernel surfaces the repr of the last expression as output
    (execute_result), matching notebook semantics — more useful for an AI
    agent than the old exec() 'no output' behaviour."""
    out = run_python_repl("1 + 1")
    assert "2" in out


def test_persistent_namespace_across_calls():
    run_python_repl("x = 42")
    out = run_python_repl("print(x * 2)")
    assert out.strip() == "84"


def test_strips_backticks():
    out = run_python_repl("```\nprint('ok')\n```")
    assert out.strip() == "ok"


def test_empty_code_returns_empty():
    assert run_python_repl("") == ""
    assert run_python_repl("   ").strip() == ""


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_exception_returns_error_string():
    out = run_python_repl("raise ValueError('boom')")
    assert "Error:" in out
    assert "boom" in out


def test_syntax_error_returns_error_string():
    out = run_python_repl("this is not valid python :::::")
    assert "Error:" in out


def test_partial_stdout_survives_exception():
    """stdout printed before the exception should be returned alongside the error."""
    out = run_python_repl("print('before'); raise RuntimeError('x')")
    assert "before" in out
    assert "Error:" in out


# ---------------------------------------------------------------------------
# inject_custom_functions
# ---------------------------------------------------------------------------


def test_inject_makes_function_callable_in_repl():
    def my_fn(x):
        return x * 10

    inject_custom_functions({"my_fn": my_fn})
    out = run_python_repl("print(my_fn(5))")
    assert out.strip() == "50"


def test_inject_writes_to_builtins_registry():
    def fn():
        pass

    inject_custom_functions({"fn": fn})
    reg = getattr(builtins, _CUSTOM_FNS_ATTR, None)
    assert reg is not None
    assert "fn" in reg


def test_empty_injection_is_noop():
    inject_custom_functions({})
    inject_custom_functions(None)  # type: ignore[arg-type]
    # Shouldn't create the registry just because we called inject
    assert getattr(builtins, _CUSTOM_FNS_ATTR, None) in (None, {})


def test_tools_registered_on_builtins_visible_inside_repl():
    """Regression: if ReplBridge updates builtins directly, the next REPL
    call must pick those up via _sync_custom_fns_into_namespace."""

    def secret():
        return "hi"

    # Simulate what ReplBridge.sync does
    setattr(builtins, _CUSTOM_FNS_ATTR, {"secret": secret})
    out = run_python_repl("print(secret())")
    assert out.strip() == "hi"


def test_reset_clears_namespace():
    run_python_repl("x = 1")
    reset_namespace()
    out = run_python_repl("print('x' in dir())")
    # After reset, 'x' should no longer be bound
    assert "False" in out
