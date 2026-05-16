"""Tool management subsystem.

Three layers:
    spec       — ToolSpec: immutable metadata for one tool.
    registry   — ToolRegistry: observable catalog.
    scanner    — ToolScanner strategies (ModuleScanner ships by default).
    repl_bridge — ReplBridge: mirrors the registry into builtins for REPL use.
"""

from .registry import ToolRegistry
from .repl_bridge import ReplBridge
from .scanner import ModuleScanner, ToolScanner
from .spec import ToolSpec

__all__ = [
    "ModuleScanner",
    "ReplBridge",
    "ToolRegistry",
    "ToolScanner",
    "ToolSpec",
]
