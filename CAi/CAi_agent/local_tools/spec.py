"""LocalToolSpec — immutable descriptor for a locally installed CLI tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LocalToolCommand:
    """A single common command for a local CLI tool."""

    name: str
    description: str
    example: str


@dataclass(frozen=True)
class LocalToolSpec:
    """Immutable descriptor for one locally installed CLI tool."""

    name: str
    description: str
    init_command: str
    common_commands: list[LocalToolCommand] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: Path) -> LocalToolSpec:
        """Parse a .yaml file into a LocalToolSpec.

        Expected YAML structure:
            name: GROMACS
            description: Molecular dynamics engine
            init_command: source /path/to/GMXRC
            common_commands:
              - name: pdb2gmx
                description: PDB -> topology
                example: gmx pdb2gmx -f protein.pdb ...
        """
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not data or "name" not in data:
            raise ValueError(f"Missing 'name' in {path}")

        commands = []
        for cmd in data.get("common_commands", []):
            commands.append(
                LocalToolCommand(
                    name=cmd.get("name", ""),
                    description=cmd.get("description", ""),
                    example=cmd.get("example", ""),
                )
            )

        return cls(
            name=data["name"],
            description=data.get("description", ""),
            init_command=data.get("init_command", ""),
            common_commands=commands,
        )
