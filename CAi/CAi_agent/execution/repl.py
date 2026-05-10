"""
Persistent Python REPL for agent code execution.

A thin `exec()` wrapper that:
  - keeps a persistent namespace across calls (variables survive between
    <execute> blocks, matching the original behaviour)
  - captures stdout so print() output is returned to the caller
  - reports exceptions as 'Error: ...' strings (never raises)
  - lets ReplBridge inject agent-registered tools via
    inject_custom_functions()

Deliberately omitted (present in the old base_CAi helper, not needed here):
  - matplotlib monkey-patching and plot capture — we don't surface plots
    through the agent right now. If that's needed later we can add an
    opt-in hook; keeping this module minimal keeps the contract simple.
"""

from __future__ import annotations

import builtins
import sys
from collections.abc import Callable
from io import StringIO

# The namespace shared across all run_python_repl() calls.
# Variables defined in one execution are visible in the next.
_PERSISTENT_NS: dict[str, object] = {}

# Name of the builtins attribute used as a cross-module registry of
# agent tools. Must match ReplBridge / tools.repl_bridge.
_CUSTOM_FNS_ATTR = "_base_CAi_custom_functions"


def run_python_repl(code: str) -> str:
    """Execute `code` in the persistent namespace; return captured stdout
    (or an 'Error: ...' message)."""
    code = (code or "").strip("`").strip()

    # Ensure any custom tools registered since the last call are visible.
    _sync_custom_fns_into_namespace()

    old_stdout = sys.stdout
    sys.stdout = buf = StringIO()
    try:
        exec(code, _PERSISTENT_NS)  # noqa: S102 — intentional; REPL-like env
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001 — surfaced to caller as string
        # Keep partial stdout even when the exec fails.
        return buf.getvalue() + f"Error: {e}"
    finally:
        sys.stdout = old_stdout


def inject_custom_functions(custom_functions: dict[str, Callable]) -> None:
    """Make the given functions callable from within the REPL.

    Also records them on `builtins._base_CAi_custom_functions` so other
    modules (e.g. ReplBridge, or code running inside the REPL that needs
    to introspect available tools) can find them.
    """
    if not custom_functions:
        return

    for name, func in custom_functions.items():
        _PERSISTENT_NS[name] = func

    registry = getattr(builtins, _CUSTOM_FNS_ATTR, None)
    if registry is None:
        registry = {}
        setattr(builtins, _CUSTOM_FNS_ATTR, registry)
    registry.update(custom_functions)


def reset_namespace() -> None:
    """Clear the persistent REPL namespace. Primarily for tests."""
    _PERSISTENT_NS.clear()


def _sync_custom_fns_into_namespace() -> None:
    """Pull anything registered on builtins into the REPL namespace.

    This lets ReplBridge update builtins outside of the REPL and still
    have those tools appear in `exec()` on the next call.
    """
    registry = getattr(builtins, _CUSTOM_FNS_ATTR, None)
    if registry:
        for name, func in registry.items():
            _PERSISTENT_NS.setdefault(name, func)
