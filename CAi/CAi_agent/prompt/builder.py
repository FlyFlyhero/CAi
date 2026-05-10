"""PromptBuilder — composes PromptSection instances into a single string."""

from __future__ import annotations

from .section import PromptSection


class PromptBuilder:
    """Ordered, fluent composer of prompt sections.

    Usage:
        prompt = (
            PromptBuilder()
            .add(CoreSection())
            .add(ToolsSection(registry))
            .add(SkillsSection(loader))
            .build()
        )

    Sections that render to an empty string are dropped silently so
    callers don't have to conditionally include them.
    """

    def __init__(self, *, separator: str = "\n\n") -> None:
        self._sections: list[PromptSection] = []
        self._separator = separator

    def add(self, section: PromptSection) -> "PromptBuilder":
        self._sections.append(section)
        return self

    def build(self) -> str:
        parts = [s.render() for s in self._sections]
        return self._separator.join(p for p in parts if p)

    @property
    def sections(self) -> list[PromptSection]:
        return list(self._sections)

    def __len__(self) -> int:
        return len(self._sections)
