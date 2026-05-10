"""ToolRegistry — the single source of truth for registered tools."""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import RLock

from .spec import ToolSpec

logger = logging.getLogger("CAi.tools.registry")


class ToolRegistry:
    """In-memory catalog of ToolSpec instances.

    Thread-safe and observable: listeners registered via on_change() are
    invoked after any mutating operation (register / unregister / clear).
    This lets dependent subsystems (REPL namespace, prompt builder) stay
    in sync without the caller having to remember to refresh them.
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._listeners: list[Callable[[], None]] = []
        self._lock = RLock()

    # ------------------------------------------------------------------
    # Mutating operations
    # ------------------------------------------------------------------

    def register(self, spec: ToolSpec) -> None:
        """Add or replace a tool. Triggers on_change listeners."""
        with self._lock:
            self._specs[spec.name] = spec
        self._notify()

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if it existed."""
        with self._lock:
            existed = self._specs.pop(name, None) is not None
        if existed:
            self._notify()
        return existed

    def clear(self) -> None:
        """Remove all tools."""
        with self._lock:
            had_any = bool(self._specs)
            self._specs.clear()
        if had_any:
            self._notify()

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get(self, name: str) -> ToolSpec | None:
        with self._lock:
            return self._specs.get(name)

    def all(self, *, include_hidden: bool = False) -> list[ToolSpec]:
        """Return specs in insertion order."""
        with self._lock:
            items = list(self._specs.values())
        if include_hidden:
            return items
        return [s for s in items if not s.hidden]

    def names(self, *, include_hidden: bool = False) -> list[str]:
        return [s.name for s in self.all(include_hidden=include_hidden)]

    # ------------------------------------------------------------------
    # Observer protocol
    # ------------------------------------------------------------------

    def on_change(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Subscribe a callback to mutation events.

        Returns an unsubscribe function so callers can tear down cleanly.
        Exceptions in callbacks are logged but don't stop other callbacks
        from running (fail-isolated notification).
        """
        self._listeners.append(callback)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                logger.exception("Tool registry listener raised — continuing")

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __contains__(self, name: str) -> bool:
        with self._lock:
            return name in self._specs

    def __len__(self) -> int:
        with self._lock:
            return len(self._specs)

    def __iter__(self):
        return iter(self.all())
