"""UtilityManager — independent curator agent for the utility library.

Reviews session execution logs and uses an LLM to decide whether to
SAVE new utilities, UPDATE existing ones, or DELETE underperforming ones.
Does not inherit BaseAgent — makes a single LLM call per maintenance cycle.

Every maintain()/preview() call writes a JSON trace to
`agent_workspace/_utilities/_traces/` for debugging and prompt tuning.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from .registry import UtilityRegistry
from ..agent_tags import (
    EXECUTE_RE,
    OBSERVATION_RE,
    iter_execute_blocks,
    strip_done,
)

logger = logging.getLogger("CAi.utilities.manager")


# ---------------------------------------------------------------------------
# Curator prompt template
# ---------------------------------------------------------------------------

MAINTAIN_PROMPT_TEMPLATE = """\
You are an expert Lead Developer and Curator for a Computational Chemistry and Drug Discovery AI Agent. \
Your job is to review a recent agent session and decide whether the executed code reveals \
reusable data-processing, algorithmic, or cheminformatics patterns worth saving as utility functions.

You have FULL CONTEXT of the session:
- The user's original task (the WHY)
- The agent's reasoning before each code block
- The actual code and its output
- The agent's interpretation of results
- The agent's final summary

Use this context to judge whether code is a one-off hack or a highly generalizable pattern.

## Current Utility Library

{library}

{user_request}
## Code Executions in This Session

{executions}
{final_summary}

## Instructions

Analyze the session above and decide what actions to take:

1. **SAVE**: If you see a useful, reusable pattern that is NOT already in the \
library AND is broadly applicable (e.g., custom clustering, RDKit molecule filtering, \
complex data deduplication, MaxMin diversity selection), create a new utility function. \
Do NOT copy code verbatim — rewrite it into a generalized, robust, production-ready function with:
   - Type hints on all parameters and return value (e.g., `smiles_list: list[str]`).
   - A docstring with a one-line summary and a "Use when:" line.
   - Self-contained imports strictly inside the function body or at the top of the code string.

   **Skip SAVE if:**
   - The code is a one-off hack tied to specific specific SMILES, file paths, or molecule names.
   - The code failed, produced errors, or relies on undefined variables.
   - An existing utility already covers this pattern.
   - The pattern is too trivial (e.g., mere wrappers around existing `CAi.toolkit` tools or simple print loops).

2. **UPDATE**: If an existing utility could be improved based on observed \
usage (e.g., adding error handling for RDKit NoneType returns, making interface more general, fixing a bug), update it.

3. **DELETE**: If a utility has a poor success rate (success_count/call_count < 0.5) \
or has never been used (call_count == 0 for a long time), consider deleting it.

## Response Format

Return a JSON array of actions. Each action is an object with:
- "type": "save" | "update" | "delete"
- "name": function name (snake_case, no spaces, domain-appropriate)
- "description": one-line description (for save/update)
- "code": complete Python function code (for save/update)
- "reasoning": brief explanation of WHY you took this action (1-2 sentences)

If no actions are needed, return an empty array: []

**CRITICAL JSON REQUIREMENT**: The "code" field must be a valid JSON string. \
You MUST properly escape all newlines as `\\n` and escape all internal double quotes as `\\"`.

Example:
```json
[
  {{
    "type": "save",
    "name": "canonicalize_and_deduplicate",
    "description": "Canonicalize a list of SMILES using RDKit and remove duplicates/invalid molecules.",
    "reasoning": "The agent manually wrote an RDKit canonicalization loop to clean generated molecules. This is universally useful.",
    "code": "from rdkit import Chem\\n\\ndef canonicalize_and_deduplicate(smiles_list: list[str]) -> list[str]:\\n    \\"\\"\\"Clean and deduplicate SMILES.\\n\\n    Use when: processing raw output from generation models.\\n    \\"\\"\\"\\n    unique = set()\\n    for s in smiles_list:\\n        mol = Chem.MolFromSmiles(s)\\n        if mol:\\n            unique.add(Chem.MolToSmiles(mol, isomericSmiles=True))\\n    return list(unique)"
  }},
  {{
    "type": "delete",
    "name": "old_unused_helper",
    "reasoning": "0 calls in last 20 sessions, superseded by parse_sdf_file."
  }}
]
```

Return ONLY the JSON array, no other text.
"""


class UtilityManager:
    """Independent curator that reviews execution logs and maintains the utility library.

    Accepts a pre-configured LLM instance (reuses the agent's LLM by default).
    All failures are caught and logged without affecting the main agent.

    Trace logging:
        Every maintain() / preview() call writes a JSON trace file to
        `<utilities_dir>/_traces/` capturing the full prompt, raw LLM
        response, parsed actions, and any errors. Use `inspect_traces.py`
        or `UtilityManager.list_traces()` to review them.
    """

    def __init__(self, registry: UtilityRegistry, llm=None):
        self._registry = registry
        self._llm = llm
        # Trace dir lives next to the utilities so they're easy to find.
        self._trace_dir = registry._dir / "_traces"
        self._trace_dir.mkdir(parents=True, exist_ok=True)

    def _invoke_curator(self, prompt: str) -> str:
        """Call the LLM for curation work, defensively isolating from
        the main agent's stop sequences.

        The maintain prompt template includes example tag tokens like
        ``</execute>`` inside JSON examples. If the shared LLM was
        configured with that as a stop sequence (BaseAgent does this),
        the curator response gets truncated mid-JSON. We rebind stop
        sequences to ``None`` for this single call.
        """
        llm = self._llm
        try:
            llm = self._llm.bind(stop=None)
        except Exception:
            # Some chat models don't support .bind(stop=...). Fall back
            # to invoking as-is and hope the response survives.
            llm = self._llm
        response = llm.invoke(prompt)
        return response.content if hasattr(response, "content") else str(response)

    def maintain(
        self,
        session_log: list[dict],
        user_message: str | None = None,
    ) -> dict[str, list[str]]:
        """Review session executions and update utility library.

        Args:
            session_log: list of {"type": "message_end"|"observation", "content": str}
            user_message: the user's original task description (optional but
                          highly recommended — it tells the curator WHY code
                          was written, not just WHAT was written).

        Returns:
            {"saved": [...], "updated": [...], "deleted": [...]}
        """
        trace = _new_trace("maintain")
        trace["user_message"] = user_message
        try:
            if self._llm is None:
                trace["status"] = "skipped"
                trace["reason"] = "no LLM configured"
                logger.debug("UtilityManager: no LLM configured, skipping.")
                return {"saved": [], "updated": [], "deleted": []}

            session_summary = self._extract_session(session_log)
            trace["code_blocks_extracted"] = len(session_summary["blocks"])
            trace["session_summary"] = session_summary
            if not session_summary["blocks"]:
                trace["status"] = "skipped"
                trace["reason"] = "no code blocks"
                return {"saved": [], "updated": [], "deleted": []}

            prompt = self._build_maintain_prompt(session_summary, user_message)
            trace["prompt"] = prompt
            trace["library_before"] = self._registry.list_meta()

            content = self._invoke_curator(prompt)
            trace["raw_response"] = content

            actions = self._parse_actions(content)
            trace["parsed_actions"] = actions

            result = self._apply_actions(actions)
            trace["applied_result"] = result
            trace["library_after"] = self._registry.list_meta()
            trace["status"] = "ok"
            return result
        except Exception as e:
            trace["status"] = "error"
            trace["error"] = str(e)
            logger.error("UtilityManager maintenance failed: %s", e)
            return {"saved": [], "updated": [], "deleted": []}
        finally:
            self._save_trace(trace)

    def preview(
        self,
        session_log: list[dict],
        user_message: str | None = None,
    ) -> list[dict]:
        """Analyze session and return proposed actions WITHOUT applying them.

        Args:
            session_log: list of {"type": "message_end"|"observation", "content": str}
            user_message: the user's original task description (optional).

        Returns:
            List of action dicts: [{"type": "save"|"update"|"delete", "name": ..., ...}]
        """
        trace = _new_trace("preview")
        trace["user_message"] = user_message
        try:
            if self._llm is None:
                trace["status"] = "skipped"
                trace["reason"] = "no LLM configured"
                return []

            session_summary = self._extract_session(session_log)
            trace["code_blocks_extracted"] = len(session_summary["blocks"])
            trace["session_summary"] = session_summary
            if not session_summary["blocks"]:
                trace["status"] = "skipped"
                trace["reason"] = "no code blocks"
                return []

            prompt = self._build_maintain_prompt(session_summary, user_message)
            trace["prompt"] = prompt
            trace["library_before"] = self._registry.list_meta()

            content = self._invoke_curator(prompt)
            trace["raw_response"] = content

            actions = self._parse_actions(content)
            trace["parsed_actions"] = actions
            trace["status"] = "ok"
            return actions
        except Exception as e:
            trace["status"] = "error"
            trace["error"] = str(e)
            logger.error("UtilityManager preview failed: %s", e)
            return []
        finally:
            self._save_trace(trace)

    def _extract_session(self, session_log: list[dict]) -> dict:
        """Extract a structured summary of the session for the curator.

        Captures not just code + observations, but also the agent's
        reasoning text BEFORE each code block (the "why") and any final
        summary text AFTER all code (the "what was concluded").

        Skips bash blocks (those starting with #!BASH).

        Returns:
            {
                "blocks": [
                    {
                        "reasoning": str,  # text before <execute> in the same message
                        "code": str,
                        "output": str,
                        "outcome": str,    # text after </execute> in the same message
                    },
                    ...
                ],
                "final_summary": str,  # last text-only message after all code
            }
        """
        blocks: list[dict] = []
        final_summary = ""

        for i, step in enumerate(session_log):
            if step.get("type") != "message_end":
                continue
            content = step.get("content", "")

            # Use the central tag parser so we accept both new (lang="bash")
            # and legacy (#!BASH) syntax uniformly.
            matches = list(EXECUTE_RE.finditer(content))

            if not matches:
                # Pure-text message after code blocks → likely the final summary
                cleaned = strip_done(content).strip()
                if cleaned and blocks:
                    final_summary = cleaned
                continue

            # Locate the next observation in the log (paired with this message)
            obs = ""
            if (
                i + 1 < len(session_log)
                and session_log[i + 1].get("type") == "observation"
            ):
                obs = session_log[i + 1].get("content", "")
                # Strip <observation> tags if the runtime included them
                obs = OBSERVATION_RE.sub(
                    lambda m: m.group("body"), obs
                ).strip()
                # Also handle a plain </observation> if obs was already body-only
                obs = obs.replace("<observation>", "").replace("</observation>", "").strip()

            # Reasoning before first <execute>; outcome after last </execute>
            reasoning = content[: matches[0].start()].strip()
            outcome = strip_done(content[matches[-1].end():]).strip()

            # Re-parse via iter_execute_blocks to honour lang=... and skip non-Python.
            execute_blocks = list(iter_execute_blocks(content))
            python_blocks = [
                b for b in execute_blocks
                if b.lang == "python" and b.code.strip()
            ]
            for j, block in enumerate(python_blocks):
                blocks.append({
                    "reasoning": reasoning if j == 0 else "",
                    "code": block.code,
                    # All execute blocks in one message share the combined observation
                    "output": obs,
                    "outcome": outcome if j == len(python_blocks) - 1 else "",
                })

        return {"blocks": blocks, "final_summary": final_summary}

    # Legacy alias for backward compat with existing tests
    def _extract_executions(self, session_log: list[dict]) -> list[dict]:
        """Legacy: returns just code+output pairs (no reasoning context)."""
        summary = self._extract_session(session_log)
        return [{"code": b["code"], "output": b["output"]} for b in summary["blocks"]]

    def _build_maintain_prompt(
        self,
        session_summary: dict,
        user_message: str | None = None,
    ) -> str:
        """Build the curator prompt with full session context."""
        current_lib = self._registry.list_meta()
        lib_section = json.dumps(current_lib, indent=2) if current_lib else "[]"

        # User task — the WHY
        user_section = (
            f"\n## User's Original Request\n\n{user_message.strip()}\n"
            if user_message and user_message.strip()
            else "\n## User's Original Request\n\n(not provided)\n"
        )

        # Code blocks with reasoning + outcome
        blocks = session_summary.get("blocks", [])
        exec_section = ""
        for i, block in enumerate(blocks[:10]):  # Cap at 10 blocks
            exec_section += f"\n### Block {i + 1}\n"
            if block.get("reasoning"):
                exec_section += f"\n**Agent's reasoning before running this code:**\n{block['reasoning'][:600]}\n"
            exec_section += f"\n**Code:**\n```python\n{block['code']}\n```\n"
            if block.get("output"):
                exec_section += f"\n**Output:**\n```\n{block['output'][:500]}\n```\n"
            if block.get("outcome"):
                exec_section += f"\n**Agent's interpretation of the result:**\n{block['outcome'][:400]}\n"

        # Final summary (the agent's conclusion)
        final = session_summary.get("final_summary", "")
        final_section = (
            f"\n## Agent's Final Summary\n\n{final[:800]}\n"
            if final
            else ""
        )

        return MAINTAIN_PROMPT_TEMPLATE.format(
            library=lib_section,
            user_request=user_section,
            executions=exec_section,
            final_summary=final_section,
        )

    def _parse_actions(self, response: str) -> list[dict]:
        """Parse LLM response into action dicts.

        Tries three strategies, in order:
          1. Direct ``json.loads`` on the whole response — works when the
             LLM follows the spec and returns ONLY a JSON array.
          2. JSON inside a markdown code fence (```json ... ```) — most
             common when the LLM wraps its output for "readability".
          3. Greedy regex match for the outermost ``[ ... ]`` — last
             resort. Will fail if the array contains stray ``[`` or ``]``
             outside of strings, but we accept that.
        """
        if not response or not response.strip():
            return []

        text = response.strip()

        # Strategy 1: maybe the model returned a clean JSON array.
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 2: extract from a markdown code fence.
        fence_match = re.search(
            r"```(?:json)?\s*(\[.*?\])\s*```",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if fence_match:
            try:
                data = json.loads(fence_match.group(1))
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        # Strategy 3: greedy outermost array.
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        logger.warning("Failed to parse UtilityManager response as JSON array")
        return []

    @staticmethod
    def _is_valid_python(code: str, expected_function_name: str | None = None) -> tuple[bool, str]:
        """Sanity-check a code blob before persisting it as a utility.

        Checks (all static — no execution):
          1. ``ast.parse`` succeeds (syntax is valid).
          2. The code defines at least one top-level function.
          3. If ``expected_function_name`` is given, that exact name exists.
          4. Every top-level ``import`` / ``from ... import`` references a
             module that ``importlib.util.find_spec`` can resolve. Imports
             *inside* function bodies are skipped — those are commonly
             optional fallbacks the agent wraps in try/except.

        Returns:
            (ok, error_message). ``error_message`` is empty when ``ok`` is True.
        """
        if not code or not code.strip():
            return False, "code is empty"
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"syntax error: {e}"

        defined = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        if not defined:
            return False, "no top-level function definition"
        if expected_function_name and expected_function_name not in defined:
            return False, (
                f"expected function '{expected_function_name}' not found "
                f"(defined: {sorted(defined)})"
            )

        # Best-effort import resolution. Only check top-level imports —
        # in-function imports are often optional/lazy and would be unfair
        # to reject statically.
        for node in tree.body:
            ok, err = UtilityManager._check_import_node(node)
            if not ok:
                return False, err

        return True, ""

    @staticmethod
    def _check_import_node(node: ast.AST) -> tuple[bool, str]:
        """Validate that the modules referenced by an import node exist.

        Uses ``importlib.util.find_spec`` so we never actually execute the
        target module's top-level code (which is what ``import`` would do).
        Relative imports and unresolvable dotted paths are rejected.
        """
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _module_exists(alias.name):
                    return False, f"unknown module in import: {alias.name!r}"
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                return False, (
                    f"relative import not allowed in utilities (level={node.level})"
                )
            if node.module and not _module_exists(node.module):
                return False, f"unknown module in 'from': {node.module!r}"
        return True, ""

    def _apply_actions(self, actions: list[dict]) -> dict[str, list[str]]:
        """Dispatch save/update/delete actions to the registry.

        For ``save`` and ``update``, the generated code is validated with
        ``ast.parse`` and checked to actually define the named function
        before being persisted. Invalid actions are logged with the reason
        and recorded in the ``rejected`` bucket so callers can surface them.
        """
        result: dict[str, list[str]] = {
            "saved": [],
            "updated": [],
            "deleted": [],
            "rejected": [],
        }
        for action in actions:
            try:
                atype = action.get("type")
                name = action.get("name", "")
                if atype in ("save", "update") and name:
                    code = action.get("code", "")
                    ok, err = self._is_valid_python(code, expected_function_name=name)
                    if not ok:
                        logger.warning(
                            "Rejected %s action for %r: %s", atype, name, err
                        )
                        result["rejected"].append(f"{atype}:{name} ({err})")
                        continue
                    if atype == "save":
                        self._registry.save(name, code, action.get("description", ""))
                        result["saved"].append(name)
                    else:
                        self._registry.update(name, code, action.get("description", ""))
                        result["updated"].append(name)
                elif atype == "delete" and name:
                    self._registry.delete(name)
                    result["deleted"].append(name)
            except Exception as e:
                logger.warning("Failed to apply action %s: %s", action, e)
                result["rejected"].append(f"{action.get('type')}:{action.get('name')} ({e})")
        return result

    # ------------------------------------------------------------------
    # Trace persistence
    # ------------------------------------------------------------------

    def _save_trace(self, trace: dict) -> None:
        """Persist a trace dict as JSON. Never raises."""
        try:
            ts = trace.get("timestamp", datetime.now().isoformat())
            # File-safe timestamp
            stamp = ts.replace(":", "-").replace(".", "-")
            filename = f"{stamp}_{trace.get('mode', 'unknown')}.json"
            path = self._trace_dir / filename
            path.write_text(
                json.dumps(trace, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            # Keep only the most recent 50 traces
            self._prune_old_traces(keep=50)
        except Exception as e:
            logger.warning("Failed to save trace: %s", e)

    def _prune_old_traces(self, keep: int = 50) -> None:
        try:
            files = sorted(
                self._trace_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for f in files[keep:]:
                f.unlink()
        except Exception:
            pass

    def list_traces(self, limit: int = 20) -> list[dict]:
        """Return summaries of the most recent traces, newest first."""
        try:
            files = sorted(
                self._trace_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            summaries = []
            for f in files[:limit]:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    summaries.append({
                        "file": f.name,
                        "timestamp": data.get("timestamp"),
                        "mode": data.get("mode"),
                        "status": data.get("status"),
                        "code_blocks": data.get("code_blocks_extracted", 0),
                        "actions_count": len(data.get("parsed_actions", [])),
                        "error": data.get("error"),
                    })
                except Exception:
                    continue
            return summaries
        except Exception:
            return []

    def get_trace(self, filename: str) -> dict | None:
        """Load a single trace by filename."""
        try:
            path = self._trace_dir / filename
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------


def _new_trace(mode: str) -> dict:
    """Initialize a trace dict with timestamp and mode."""
    return {
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "status": "running",
    }


# Cache `find_spec` results — the same modules (rdkit, numpy, ...) get
# checked over and over across many utility validations.
_module_cache: dict[str, bool] = {}


def _module_exists(dotted: str) -> bool:
    """Return True if ``dotted`` is importable on this interpreter.

    Uses ``importlib.util.find_spec`` so we don't actually run the
    target module's top-level code. Sub-attributes after the first
    importable parent (``foo.bar.Baz`` where ``foo.bar`` is a module
    and ``Baz`` is a class) are accepted.
    """
    if not dotted:
        return False
    if dotted in _module_cache:
        return _module_cache[dotted]

    parts = dotted.split(".")
    found = False
    # Try progressively shorter prefixes — `find_spec("a.b.C")` raises
    # if `a.b` exists as a module and `C` is just a class attribute.
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        try:
            if importlib.util.find_spec(candidate) is not None:
                found = True
                break
        except (ImportError, ValueError, ModuleNotFoundError):
            # find_spec can raise for partially broken parents. Try a
            # shorter prefix.
            continue
    _module_cache[dotted] = found
    return found
