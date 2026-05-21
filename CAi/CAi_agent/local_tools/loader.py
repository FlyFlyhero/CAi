"""LocalToolsLoader — scans a directory for .yaml tool definitions."""

from __future__ import annotations

import logging
from pathlib import Path

from .spec import LocalToolSpec

logger = logging.getLogger("CAi.local_tools.loader")


class LocalToolsLoader:
    """Scan a directory for .yaml local tool configs.

    Follows the SkillLoader pattern: directory-based scanning,
    metadata extraction, summary generation.
    """

    def __init__(self, tools_dir: str | Path | None = None) -> None:
        if tools_dir is None:
            tools_dir = Path("agent_workspace/_local_tools")
        self.tools_dir = Path(tools_dir)
        self._tools: dict[str, LocalToolSpec] = {}
        self._load()

    def _load(self) -> None:
        if not self.tools_dir.is_dir():
            return
        for yaml_file in sorted(self.tools_dir.glob("*.yaml")):
            try:
                spec = LocalToolSpec.from_file(yaml_file)
                self._tools[spec.name] = spec
            except Exception as e:
                logger.warning("Skipping malformed local tool %s: %s", yaml_file.name, e)

    @property
    def tools(self) -> dict[str, LocalToolSpec]:
        return dict(self._tools)

    def reload(self) -> None:
        self._tools.clear()
        self._load()

    def get_summaries(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "has_init": bool(t.init_command),
                "command_count": len(t.common_commands),
            }
            for t in self._tools.values()
        ]

    def __len__(self) -> int:
        return len(self._tools)
