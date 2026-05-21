"""LocalToolsSection — renders locally installed CLI tools into the prompt."""

from __future__ import annotations

from ..prompt.section import PromptSection
from .loader import LocalToolsLoader

_HEADER = """\
LOCALLY INSTALLED CLI TOOLS
==================================================
The following CLI tools are available on this system.
Invoke them using #!BASH execution blocks.

BEFORE using any tool, run its init command to set up
environment variables (PATH, library paths, etc.)."""


class LocalToolsSection(PromptSection):
    """Render locally installed CLI tools into the agent prompt.

    Returns empty string when no tools are configured, so
    PromptBuilder silently drops this section.
    """

    def __init__(self, loader: LocalToolsLoader | None) -> None:
        self._loader = loader

    def render(self) -> str:
        if self._loader is None:
            return ""

        tools = self._loader.tools
        if not tools:
            return ""

        lines = [_HEADER]
        for spec in tools.values():
            lines.append("")
            lines.append(f"- {spec.name} — {spec.description}")
            lines.append("")

            if spec.init_command:
                lines.append("  Initialization (run BEFORE first use):")
                lines.append(f"    {spec.init_command}")
                lines.append("")

            if spec.common_commands:
                lines.append("  Common commands:")
                for cmd in spec.common_commands:
                    lines.append(f"    - {cmd.name}: {cmd.description}")
                    if cmd.example:
                        lines.append(f"      Example: {cmd.example}")
                lines.append("")

        return "\n".join(lines).rstrip()
