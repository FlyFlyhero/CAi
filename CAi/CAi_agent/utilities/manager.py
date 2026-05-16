"""UtilityManager — independent curator agent for the utility library.

Reviews session execution logs and uses an LLM to decide whether to
SAVE new utilities, UPDATE existing ones, or DELETE underperforming ones.
Does not inherit BaseAgent — makes a single LLM call per maintenance cycle.
"""

from __future__ import annotations

import json
import logging
import re

from .registry import UtilityRegistry

logger = logging.getLogger("CAi.utilities.manager")


# ---------------------------------------------------------------------------
# Curator prompt template
# ---------------------------------------------------------------------------

MAINTAIN_PROMPT_TEMPLATE = """\
You are a utility library curator for a Python coding agent. Your job is to \
review recent code executions and maintain a reusable function library.

## Current Utility Library

{library}

## Recent Code Executions

{executions}

## Instructions

Analyze the code executions above and decide what actions to take:

1. **SAVE**: If you see a useful, reusable pattern that is NOT already in the \
library, create a new utility function. Do NOT copy code verbatim — rewrite it \
into a generalized, well-documented function with:
   - Type hints on all parameters and return value
   - A docstring with a one-line summary and a "Use when:" line
   - Input validation where appropriate
   - Self-contained imports (at top of function or file level)

2. **UPDATE**: If an existing utility could be improved based on new usage \
patterns (better error handling, more general interface, bug fix), update it.

3. **DELETE**: If a utility has a poor success rate (success_count/call_count < 0.5 \
over 10+ calls) or has never been used (call_count == 0 for a long time), \
consider deleting it.

## Response Format

Return a JSON array of actions. Each action is an object with:
- "type": "save" | "update" | "delete"
- "name": function name (snake_case, no spaces)
- "description": one-line description (for save/update)
- "code": complete Python function code (for save/update)

If no actions are needed, return an empty array: []

Example:
```json
[
  {{
    "type": "save",
    "name": "parse_sdf_file",
    "description": "Parse an SDF file and return a list of molecule dicts.",
    "code": "import re\\nfrom pathlib import Path\\n\\ndef parse_sdf_file(path: str) -> list[dict]:\\n    \\"\\"\\"Parse an SDF file into molecule records.\\n\\n    Use when: you need to read molecular data from .sdf files.\\n    \\"\\"\\"\\n    ..."
  }},
  {{
    "type": "delete",
    "name": "old_unused_helper"
  }}
]
```

Return ONLY the JSON array, no other text.
"""


class UtilityManager:
    """Independent curator that reviews execution logs and maintains the utility library.

    Accepts a pre-configured LLM instance (reuses the agent's LLM by default).
    All failures are caught and logged without affecting the main agent.
    """

    def __init__(self, registry: UtilityRegistry, llm=None):
        self._registry = registry
        self._llm = llm

    def maintain(self, session_log: list[dict]) -> dict[str, list[str]]:
        """Review session executions and update utility library.

        Args:
            session_log: list of {"type": "message_end"|"observation", "content": str}

        Returns:
            {"saved": [...], "updated": [...], "deleted": [...]}
        """
        try:
            if self._llm is None:
                logger.debug("UtilityManager: no LLM configured, skipping.")
                return {"saved": [], "updated": [], "deleted": []}

            code_blocks = self._extract_executions(session_log)
            if not code_blocks:
                return {"saved": [], "updated": [], "deleted": []}

            prompt = self._build_maintain_prompt(code_blocks)
            response = self._llm.invoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            actions = self._parse_actions(content)
            return self._apply_actions(actions)
        except Exception as e:
            logger.error("UtilityManager maintenance failed: %s", e)
            return {"saved": [], "updated": [], "deleted": []}

    def preview(self, session_log: list[dict]) -> list[dict]:
        """Analyze session and return proposed actions WITHOUT applying them.

        Returns:
            List of action dicts: [{"type": "save"|"update"|"delete", "name": ..., ...}]
        """
        try:
            if self._llm is None:
                return []

            code_blocks = self._extract_executions(session_log)
            if not code_blocks:
                return []

            prompt = self._build_maintain_prompt(code_blocks)
            response = self._llm.invoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            return self._parse_actions(content)
        except Exception as e:
            logger.error("UtilityManager preview failed: %s", e)
            return []

    def _extract_executions(self, session_log: list[dict]) -> list[dict]:
        """Extract paired code + observation from session log.

        Skips bash blocks (those starting with #!BASH).
        Pairs each <execute> block with the next observation in the log.
        """
        blocks: list[dict] = []
        for i, step in enumerate(session_log):
            if step.get("type") == "message_end":
                content = step.get("content", "")
                code_matches = re.findall(
                    r"<execute>(.*?)</execute>", content, re.DOTALL
                )
                # Find the next observation
                obs = ""
                if (
                    i + 1 < len(session_log)
                    and session_log[i + 1].get("type") == "observation"
                ):
                    obs = session_log[i + 1].get("content", "")
                for code in code_matches:
                    code = code.strip()
                    if code and not code.startswith("#!BASH"):
                        blocks.append({"code": code, "output": obs})
        return blocks

    def _build_maintain_prompt(self, code_blocks: list[dict]) -> str:
        """Build the curator prompt with current library state + recent executions."""
        current_lib = self._registry.list_meta()
        lib_section = json.dumps(current_lib, indent=2) if current_lib else "[]"

        exec_section = ""
        for i, block in enumerate(code_blocks[:10]):  # Cap at 10 blocks
            exec_section += f"\n### Block {i + 1}\n```python\n{block['code']}\n```\n"
            if block["output"]:
                exec_section += (
                    f"Output:\n```\n{block['output'][:500]}\n```\n"
                )

        return MAINTAIN_PROMPT_TEMPLATE.format(
            library=lib_section,
            executions=exec_section,
        )

    def _parse_actions(self, response: str) -> list[dict]:
        """Parse LLM response into action dicts.

        Tries to find a JSON array in the response. Falls back to empty list
        if parsing fails.
        """
        try:
            # Look for [...] pattern in the response
            match = re.search(r"\[.*\]", response, re.DOTALL)
            if match:
                return json.loads(match.group())
        except (json.JSONDecodeError, AttributeError):
            pass
        logger.warning("Failed to parse UtilityManager response")
        return []

    def _apply_actions(self, actions: list[dict]) -> dict[str, list[str]]:
        """Dispatch save/update/delete actions to the registry.

        Invalid or failed actions are logged and skipped.
        """
        result: dict[str, list[str]] = {"saved": [], "updated": [], "deleted": []}
        for action in actions:
            try:
                atype = action.get("type")
                name = action.get("name", "")
                if atype == "save" and name:
                    self._registry.save(
                        name, action.get("code", ""), action.get("description", "")
                    )
                    result["saved"].append(name)
                elif atype == "update" and name:
                    self._registry.update(
                        name, action.get("code", ""), action.get("description", "")
                    )
                    result["updated"].append(name)
                elif atype == "delete" and name:
                    self._registry.delete(name)
                    result["deleted"].append(name)
            except Exception as e:
                logger.warning("Failed to apply action %s: %s", action, e)
        return result
