"""ReplBridge — keeps the code-execution REPL's namespace in sync with a ToolRegistry."""

from __future__ import annotations

import builtins

from .registry import ToolRegistry

# The REPL reads this attribute off `builtins` as its cross-module
# registry of agent-registered tools. CAi.CAi_agent.execution.repl
# also looks it up via the same name, so mirroring here means tools
# registered through ToolRegistry become callable inside <execute>
# blocks automatically.
_NAMESPACE_ATTR = "_base_CAi_custom_functions"


class ReplBridge:
    """Mirrors a ToolRegistry into `builtins._base_CAi_custom_functions`
    so injected functions are reachable from the REPL.

    Hidden tools are included — they're callable in code even though they
    don't appear in the prompt catalog.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self._unsubscribe = registry.on_change(self.sync)
        # Prime the namespace immediately so any tools already in the
        # registry are callable right after wiring.
        self.sync()

    def sync(self) -> None:
        ns = _ensure_namespace()
        ns.clear()
        for spec in self.registry.all(include_hidden=True):
            ns[spec.name] = spec.func

    def detach(self) -> None:
        """Stop mirroring. Callers should invoke this when disposing the bridge."""
        self._unsubscribe()


def _ensure_namespace() -> dict:
    ns = getattr(builtins, _NAMESPACE_ATTR, None)
    if ns is None:
        ns = {}
        setattr(builtins, _NAMESPACE_ATTR, ns)
    return ns
