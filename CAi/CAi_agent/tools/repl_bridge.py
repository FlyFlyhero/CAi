"""ReplBridge — keeps the code-execution REPL's namespace in sync with a ToolRegistry."""

from __future__ import annotations

import builtins

from .registry import ToolRegistry

# base_CAi.utils.inject_custom_functions_to_repl reads this attribute off
# builtins. Keeping the name consistent with upstream avoids the need to
# monkey-patch their utility.
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
