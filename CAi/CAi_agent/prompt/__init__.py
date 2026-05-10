"""System-prompt composition subsystem."""

from .builder import PromptBuilder
from .section import PromptSection
from .sections import (
    CORE_INSTRUCTIONS,
    DEFAULT_DRUG_DISCOVERY_PERSONA,
    CoreSection,
    SkillsSection,
    ToolsSection,
)

__all__ = [
    "CORE_INSTRUCTIONS",
    "CoreSection",
    "DEFAULT_DRUG_DISCOVERY_PERSONA",
    "PromptBuilder",
    "PromptSection",
    "SkillsSection",
    "ToolsSection",
]
