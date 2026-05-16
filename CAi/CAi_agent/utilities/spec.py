"""UtilitySpec — immutable dataclass describing a single utility function."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_HEADER_PATTERN = re.compile(r"^# @(\w+):[ \t]*(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class UtilitySpec:
    """Metadata + source code for one utility function.

    Stored on disk as a `.py` file with `# @key: value` comment headers
    followed by the function source code.
    """

    name: str
    description: str
    code: str  # Full .py file content (headers + body)
    call_count: int = 0
    success_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    last_used: datetime | None = None

    @classmethod
    def from_file(cls, path: Path) -> "UtilitySpec":
        """Parse a .py file with @-prefixed comment headers.

        Missing optional fields are filled with sensible defaults.
        Malformed files will raise — callers should catch exceptions.
        """
        text = path.read_text(encoding="utf-8")
        meta = dict(_HEADER_PATTERN.findall(text))
        return cls(
            name=meta.get("name", path.stem),
            description=meta.get("description", ""),
            code=text,
            call_count=int(meta.get("call_count", 0)),
            success_count=int(meta.get("success_count", 0)),
            created_at=(
                datetime.fromisoformat(meta["created"])
                if "created" in meta
                else datetime.now()
            ),
            last_used=(
                datetime.fromisoformat(meta["last_used"])
                if meta.get("last_used")
                else None
            ),
        )

    def to_file(self, dir_: Path) -> None:
        """Write/overwrite the .py file with updated headers + preserved body."""
        body = self._extract_body()
        headers = [
            f"# @name: {self.name}",
            f"# @description: {self.description}",
            f"# @call_count: {self.call_count}",
            f"# @success_count: {self.success_count}",
            f"# @created: {self.created_at.isoformat() if self.created_at else datetime.now().isoformat()}",
            f"# @last_used: {self.last_used.isoformat() if self.last_used else ''}",
        ]
        content = "\n".join(headers) + "\n\n" + body + "\n"
        dir_.mkdir(parents=True, exist_ok=True)
        (dir_ / f"{self.name}.py").write_text(content, encoding="utf-8")

    def delete_file(self, dir_: Path) -> None:
        """Remove the .py file if it exists. No error if missing."""
        path = dir_ / f"{self.name}.py"
        if path.exists():
            path.unlink()

    def _extract_body(self) -> str:
        """Strip header comment lines, return the function source code."""
        lines = self.code.split("\n")
        body_start = 0
        for i, line in enumerate(lines):
            if not line.startswith("# @") and line.strip():
                body_start = i
                break
        return "\n".join(lines[body_start:]).strip()
