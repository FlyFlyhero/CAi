"""Tests for CAi.CAi_agent.execution.timeout."""

from __future__ import annotations

import time

from CAi.CAi_agent.execution.timeout import run_with_timeout


def test_returns_value_on_success():
    assert run_with_timeout(lambda x: x * 2, args=[21]) == 42


def test_passes_kwargs():
    assert run_with_timeout(lambda x, y: x + y, kwargs={"x": 1, "y": 2}) == 3


def test_exception_becomes_error_string():
    def boom():
        raise ValueError("kaboom")

    out = run_with_timeout(boom)
    assert isinstance(out, str)
    assert "kaboom" in out
    assert "Error:" in out


def test_timeout_returns_timeout_message():
    def slow():
        time.sleep(1.0)
        return "done"

    out = run_with_timeout(slow, timeout=0.1)
    assert isinstance(out, str)
    assert "TIMEOUT" in out
    # And the explicit deadline is mentioned
    assert "0.1" in out


def test_default_args_are_empty():
    # Shouldn't crash when we omit args/kwargs
    assert run_with_timeout(lambda: 5) == 5


def test_concurrent_calls_dont_interfere():
    """Smoke-test the shared pool — several parallel jobs all return correctly."""
    import concurrent.futures

    def work(n):
        time.sleep(0.05)
        return n * n

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(run_with_timeout, work, [i]) for i in range(5)]
        results = [f.result() for f in futures]
    assert sorted(results) == [0, 1, 4, 9, 16]
