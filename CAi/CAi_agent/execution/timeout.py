"""
run_with_timeout — run a callable with a deadline.

The implementation uses concurrent.futures.ThreadPoolExecutor + Future.cancel,
which is safer than the old ctypes-based thread-kill trick. The underlying
callable still can't be forcibly terminated mid-blocking-call (a Python
thread limitation), but:
  - we return promptly once the deadline elapses
  - we drop our reference to the runaway thread (it's a daemon)
  - we never cause reentrancy on builtins or the interpreter's internals

For truly non-cooperative work (stuck subprocess, blocking I/O), callers
should prefer subprocess-based execution (see bash.py).
"""

from __future__ import annotations

import concurrent.futures
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("CAi.execution.timeout")

# Shared daemon executor — threads are GC'd when the interpreter exits.
# max_workers is generous because jobs may be nested (LangGraph node calls
# run_python_repl which itself may call into other tools).
_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="CAi-exec",
)


def run_with_timeout(
    func: Callable[..., Any],
    args: list | None = None,
    kwargs: dict | None = None,
    timeout: float = 600,
) -> Any:
    """Run `func(*args, **kwargs)` and enforce a wall-clock timeout.

    Returns the callable's return value on success. On timeout or
    exception, returns a human-readable string starting with 'Error:'
    or 'TIMEOUT:' — matching the convention of the previous helper so
    callers (e.g. BaseAgent._node_execute) don't need to change.
    """
    args = args or []
    kwargs = kwargs or {}

    fut = _POOL.submit(func, *args, **kwargs)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        # Best-effort cancellation; Python threads can't truly be killed,
        # but the executor will stop caring about this future.
        fut.cancel()
        msg = f"TIMEOUT: Code execution timed out after {timeout} seconds"
        logger.warning(msg)
        return msg
    except Exception as e:  # noqa: BLE001 — surface to the caller as a string
        logger.exception("run_with_timeout: callable raised")
        return f"Error: {e}"
