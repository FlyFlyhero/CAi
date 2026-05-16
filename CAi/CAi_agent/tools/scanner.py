"""Tool scanners — discover ToolSpecs from various sources."""

from __future__ import annotations

import importlib
import inspect
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator

from .spec import ToolSpec

logger = logging.getLogger("CAi.tools.scanner")


class ToolScanner(ABC):
    """Strategy base class for anything that produces ToolSpecs."""

    @abstractmethod
    def scan(self) -> Iterator[ToolSpec]:
        """Yield ToolSpec instances."""


class ModuleScanner(ToolScanner):
    """Discover public functions inside a Python module.

    Rules:
      - Iterates module top-level functions (no classes, no lambdas).
      - Skips names starting with '_'.
      - Skips any name in `exclude`.
      - Marks names in `hidden` as hidden in the resulting ToolSpec
        (callable from REPL but not shown in prompt catalogs).
      - Applies `tags` to every produced ToolSpec.
    """

    def __init__(
        self,
        module_name: str,
        *,
        exclude: Iterable[str] = (),
        hidden: Iterable[str] = (),
        tags: Iterable[str] = (),
    ) -> None:
        self.module_name = module_name
        self.exclude = set(exclude)
        self.hidden = set(hidden)
        self.tags = frozenset(tags)

    def scan(self) -> Iterator[ToolSpec]:
        try:
            module = importlib.import_module(self.module_name)
        except ModuleNotFoundError:
            logger.error("Tools module not found: %s", self.module_name)
            return

        source_label = f"module:{self.module_name}"
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_") or name in self.exclude:
                continue
            # Respect the module's __all__ if present — keeps private helpers out
            module_all = getattr(module, "__all__", None)
            if module_all is not None and name not in module_all:
                continue
            yield ToolSpec.from_function(
                func,
                source=source_label,
                hidden=(name in self.hidden),
                tags=self.tags,
            )
