"""Skill-lookup helpers exposed to the agent's REPL.

These two functions let the agent browse and load skill (SOP) markdown
files on demand. They're registered as hidden tools in A1pro — callable
from code, but not advertised in the prompt's tool catalog (the skill
catalog in the prompt explains when to use them).
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _get_skill_loader():
    """Return a cached SkillLoader instance (lazy init)."""
    from CAi.CAi_agent.skills import SkillLoader

    return SkillLoader()


def get_skill_content(skill_id: str) -> str:
    """
    Get the complete workflow of a skill by its ID.

    Use this to load step-by-step instructions before executing a complex
    task. First call list_available_skills() to discover valid IDs.

    Args:
        skill_id: Skill identifier (e.g. 'molecule_analysis', 'virtual_screening').

    Returns:
        The complete skill content, or an error message if the id is unknown.
    """
    loader = _get_skill_loader()
    skill = loader.get_skill_by_id(skill_id)
    if skill:
        return skill.get("content_without_metadata", skill.get("content", ""))
    return (
        f"Error: Skill '{skill_id}' not found. "
        "Call list_available_skills() to see available IDs."
    )


def list_available_skills() -> str:
    """
    List all available skill IDs and their descriptions.

    Call this first to see what skills exist, then use
    get_skill_content(skill_id) to load a full workflow.
    """
    loader = _get_skill_loader()
    summaries = loader.get_skill_summaries()
    if not summaries:
        return "No skills available."
    lines = ["Available skills:"]
    for s in summaries:
        lines.append(f"  - {s['id']}: {s['description']}")
    return "\n".join(lines)
