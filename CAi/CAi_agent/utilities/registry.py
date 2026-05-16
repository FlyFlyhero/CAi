"""UtilityRegistry — disk ↔ memory bridge for utility functions.

Provides snapshot loading, usage stats persistence, and CRUD operations
with automatic max_utilities enforcement and _meta.json rebuilding.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import RLock

from .spec import UtilitySpec

logger = logging.getLogger("CAi.utilities.registry")


class UtilityRegistry:
    """Manages utility .py files on disk and their in-memory representations.

    Thread-safe via RLock. Enforces a configurable maximum utility count
    by evicting the least-used utility when the limit is reached.
    """

    def __init__(self, utilities_dir: Path, max_utilities: int = 20):
        self._dir = Path(utilities_dir)
        self._max = max_utilities
        self._specs: dict[str, UtilitySpec] = {}
        self._lock = RLock()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._load_specs()

    # ------------------------------------------------------------------
    # Internal loading
    # ------------------------------------------------------------------

    def _load_specs(self) -> None:
        """Scan directory, parse all .py files into UtilitySpec."""
        with self._lock:
            self._specs.clear()
            for f in sorted(self._dir.glob("*.py")):
                try:
                    spec = UtilitySpec.from_file(f)
                    self._specs[spec.name] = spec
                except Exception as e:
                    logger.warning("Skipping malformed utility %s: %s", f.name, e)
            # Enforce max: keep most-recently-used
            if len(self._specs) > self._max:
                sorted_specs = sorted(
                    self._specs.values(),
                    key=lambda s: s.last_used or s.created_at or datetime.min,
                    reverse=True,
                )
                self._specs = {s.name: s for s in sorted_specs[: self._max]}

    # ------------------------------------------------------------------
    # A1pro interface — session start
    # ------------------------------------------------------------------

    def load_snapshot(self) -> dict[str, Callable]:
        """exec() each utility's code body, return {name: callable} dict.

        Malformed utilities are skipped with a warning.
        """
        functions: dict[str, Callable] = {}
        with self._lock:
            for spec in self._specs.values():
                try:
                    namespace: dict = {}
                    exec(spec._extract_body(), namespace)  # noqa: S102
                    if spec.name in namespace:
                        functions[spec.name] = namespace[spec.name]
                    else:
                        logger.warning(
                            "Utility %s: no function named '%s' found after exec",
                            spec.name,
                            spec.name,
                        )
                except Exception as e:
                    logger.warning("Failed to load utility %s: %s", spec.name, e)
        return functions

    # ------------------------------------------------------------------
    # Session end — usage persistence
    # ------------------------------------------------------------------

    def apply_usage(self, usage: dict[str, dict]) -> None:
        """Update call_count/success_count/last_used from session stats.

        Args:
            usage: {name: {"calls": int, "errors": int}}
        Unknown utility names are silently ignored.
        """
        with self._lock:
            for name, stats in usage.items():
                spec = self._specs.get(name)
                if spec is None:
                    continue
                calls = stats.get("calls", 0)
                errors = stats.get("errors", 0)
                updated = UtilitySpec(
                    name=spec.name,
                    description=spec.description,
                    code=spec.code,
                    call_count=spec.call_count + calls,
                    success_count=spec.success_count + (calls - errors),
                    created_at=spec.created_at,
                    last_used=datetime.now(),
                )
                updated.to_file(self._dir)
                self._specs[name] = updated
            self._rebuild_meta()

    # ------------------------------------------------------------------
    # UtilityManager interface — CRUD
    # ------------------------------------------------------------------

    def save(self, name: str, code: str, description: str) -> None:
        """Save a new utility. Evicts least-used if at max capacity."""
        with self._lock:
            if len(self._specs) >= self._max:
                self._evict_least_used()
            spec = UtilitySpec(
                name=name,
                description=description,
                code=code,
                call_count=0,
                success_count=0,
                created_at=datetime.now(),
                last_used=None,
            )
            spec.to_file(self._dir)
            self._specs[name] = spec
            self._rebuild_meta()

    def update(self, name: str, code: str, description: str) -> None:
        """Update an existing utility's code and description, preserving stats."""
        with self._lock:
            old = self._specs.get(name)
            spec = UtilitySpec(
                name=name,
                description=description,
                code=code,
                call_count=old.call_count if old else 0,
                success_count=old.success_count if old else 0,
                created_at=old.created_at if old else datetime.now(),
                last_used=old.last_used if old else None,
            )
            spec.to_file(self._dir)
            self._specs[name] = spec
            self._rebuild_meta()

    def delete(self, name: str) -> None:
        """Delete a utility from disk and memory."""
        with self._lock:
            spec = self._specs.pop(name, None)
            if spec:
                spec.delete_file(self._dir)
                self._rebuild_meta()

    def list_meta(self) -> list[dict]:
        """Return summary dicts for UtilityManager prompt building."""
        with self._lock:
            return [
                {
                    "name": s.name,
                    "description": s.description,
                    "call_count": s.call_count,
                    "success_count": s.success_count,
                    "last_used": s.last_used.isoformat() if s.last_used else None,
                }
                for s in self._specs.values()
            ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict_least_used(self) -> None:
        """Delete the utility with the lowest call_count."""
        if not self._specs:
            return
        least = min(self._specs.values(), key=lambda s: s.call_count)
        self.delete(least.name)

    def _rebuild_meta(self) -> None:
        """Write _meta.json index cache from current _specs."""
        meta = {}
        for s in self._specs.values():
            meta[s.name] = {
                "call_count": s.call_count,
                "success_count": s.success_count,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "last_used": s.last_used.isoformat() if s.last_used else None,
            }
        meta_path = self._dir / "_meta.json"
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Properties and dunder methods
    # ------------------------------------------------------------------

    @property
    def specs(self) -> dict[str, UtilitySpec]:
        """Return a copy of the current specs dict."""
        with self._lock:
            return dict(self._specs)

    def __len__(self) -> int:
        with self._lock:
            return len(self._specs)

    def __bool__(self) -> bool:
        return len(self) > 0
