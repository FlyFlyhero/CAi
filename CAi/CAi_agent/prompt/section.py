"""PromptSection — the abstract unit of system-prompt composition."""

from __future__ import annotations

from abc import ABC, abstractmethod


class PromptSection(ABC):
    """One logical chunk of the system prompt.

    Subclasses implement `render()` to return their text. Returning "" (or
    any falsy string) tells the PromptBuilder to omit this section from
    the final output — useful when there's nothing to say (e.g. no tools
    are loaded).
    """

    @abstractmethod
    def render(self) -> str: ...

    # Nice repr for debugging
    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__}>"
