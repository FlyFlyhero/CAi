"""UtilitiesSection — PromptSection that renders utility function catalog."""

from __future__ import annotations

import ast

from ..prompt.section import PromptSection
from .registry import UtilityRegistry

_HEADER = """\
UTILITY FUNCTIONS
==================================================
The following functions are already available in your execution environment.
Call them directly — do NOT re-import or re-implement them.
"""


class UtilitiesSection(PromptSection):
    """Render available utility functions into the agent prompt.

    Shows each utility's signature, one-line description, and optional
    "Use when" guidance extracted from the docstring.
    Returns empty string when the registry has no utilities.
    """

    def __init__(self, registry: UtilityRegistry) -> None:
        self._registry = registry

    def render(self) -> str:
        specs = self._registry.specs
        if not specs:
            return ""

        lines = [_HEADER]
        for spec in specs.values():
            sig = self._extract_signature(spec)
            one_liner, use_when = self._parse_docstring(spec)
            lines.append(f"▸ {spec.name}{sig}")
            lines.append(f"    {one_liner}")
            if use_when:
                lines.append(f"    Use when: {use_when}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def _extract_signature(self, spec) -> str:
        """Parse the function def to extract its signature string via AST."""
        try:
            body = spec._extract_body()
            tree = ast.parse(body)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == spec.name:
                    return "(" + ast.unparse(node.args) + ")"
        except Exception:
            pass
        return "(...)"

    def _parse_docstring(self, spec) -> tuple[str, str | None]:
        """Extract one-liner and 'Use when:' line from the function docstring."""
        try:
            body = spec._extract_body()
            tree = ast.parse(body)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == spec.name:
                    doc = ast.get_docstring(node) or spec.description
                    lines = doc.strip().split("\n")
                    one_liner = lines[0].strip() if lines else spec.description

                    use_when = None
                    for line in lines:
                        stripped = line.strip()
                        if stripped.lower().startswith("use when:"):
                            use_when = stripped[len("use when:"):].strip()
                            break

                    return one_liner, use_when
        except Exception:
            pass
        return spec.description, None
