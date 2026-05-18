"""UtilitiesSection — PromptSection that renders utility function catalog."""

from __future__ import annotations

import ast

from ..prompt.section import PromptSection
from .registry import UtilityRegistry

_HEADER = """\
UTILITY FUNCTIONS  (PREFERRED — try these FIRST)
==================================================
These are pre-built, validated helpers tailored to common workflows.
They are ALREADY LOADED in your execution environment — no import needed.

⚠ TWO KINDS OF CALLABLES — handle them differently:
  • UTILITY FUNCTIONS (this section): call DIRECTLY by name, no import.
  • TOOLKIT TOOLS (next section): require `from CAi.toolkit import <name>` first.

⚠ CORRECT vs WRONG:

  ✓ CORRECT (utility — already in namespace):
      <execute>
      result = filter_compounds_by_similarity(ref, candidates, threshold=0.7)
      print(result)
      </execute>

  ✗ WRONG (utilities are NOT in CAi.toolkit):
      from CAi.toolkit import filter_compounds_by_similarity   # ImportError!
      from CAi.utilities import filter_compounds_by_similarity # ImportError!

PRIORITY RULES:
- ALWAYS check this list first when planning a task.
- If a utility's "Use when" matches your need, USE IT — do not write
  raw code or call lower-level tools to do the same thing.
- Only fall back to the toolkit tools listed below when no utility fits,
  or when a utility call fails and you need a lower-level alternative.
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
            one_liner, use_when, args_block, returns_block = self._parse_docstring(spec)
            lines.append(f"▸ {spec.name}{sig}")
            lines.append(f"    {one_liner}")
            if use_when:
                lines.append(f"    Use when: {use_when}")
            if args_block:
                lines.append("    Args:")
                for arg_line in args_block:
                    lines.append(f"      {arg_line}")
            if returns_block:
                lines.append("    Returns:")
                for ret_line in returns_block:
                    lines.append(f"      {ret_line}")
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

    def _parse_docstring(self, spec) -> tuple[str, str | None, list[str], list[str]]:
        """Extract one-liner, 'Use when:', Args, and Returns from the docstring."""
        try:
            body = spec._extract_body()
            tree = ast.parse(body)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == spec.name:
                    doc = ast.get_docstring(node) or spec.description
                    lines = doc.strip().split("\n")
                    one_liner = lines[0].strip() if lines else spec.description

                    use_when = None
                    args_block: list[str] = []
                    returns_block: list[str] = []

                    # Simple section parser: track which section we're in
                    section = None
                    for line in lines[1:]:
                        stripped = line.strip()
                        low = stripped.lower()
                        if low.startswith("use when:"):
                            use_when = stripped[len("use when:"):].strip()
                            section = None
                        elif low in ("args:", "arguments:", "parameters:", "params:"):
                            section = "args"
                        elif low in ("returns:", "return:"):
                            section = "returns"
                        elif low in ("raises:", "example:", "examples:", "note:", "notes:"):
                            section = None
                        elif stripped and section == "args":
                            args_block.append(stripped)
                        elif stripped and section == "returns":
                            returns_block.append(stripped)

                    return one_liner, use_when, args_block, returns_block
        except Exception:
            pass
        return spec.description, None, [], []
