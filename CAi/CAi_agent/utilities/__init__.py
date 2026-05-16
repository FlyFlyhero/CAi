"""Utility Library — self-learning code reuse system.

Components:
    spec       — UtilitySpec: immutable metadata for one utility.
    registry   — UtilityRegistry: disk ↔ memory bridge with CRUD.
    section    — UtilitiesSection: PromptSection for agent prompt.
    manager    — UtilityManager: independent curator agent.
"""

from .spec import UtilitySpec


def __getattr__(name: str):
    """Lazy imports for modules that may not exist yet during incremental development."""
    if name == "UtilityRegistry":
        from .registry import UtilityRegistry
        return UtilityRegistry
    if name == "UtilityManager":
        from .manager import UtilityManager
        return UtilityManager
    if name == "UtilitiesSection":
        from .section import UtilitiesSection
        return UtilitiesSection
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "UtilitySpec",
    "UtilityRegistry",
    "UtilityManager",
    "UtilitiesSection",
]
