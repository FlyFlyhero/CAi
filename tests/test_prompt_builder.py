"""Unit tests for PromptBuilder and the three concrete sections."""

from __future__ import annotations

from CAi.CAi_agent.prompt import (
    CORE_INSTRUCTIONS,
    CoreSection,
    PromptBuilder,
    PromptSection,
    SkillsSection,
    ToolsSection,
)
from CAi.CAi_agent.tools import ToolRegistry, ToolSpec


# ---------------------------------------------------------------------------
# PromptBuilder mechanics
# ---------------------------------------------------------------------------


class _S(PromptSection):
    def __init__(self, text: str):
        self.text = text

    def render(self) -> str:
        return self.text


def test_builder_joins_with_double_newline():
    out = PromptBuilder().add(_S("a")).add(_S("b")).build()
    assert out == "a\n\nb"


def test_builder_drops_empty_sections():
    out = PromptBuilder().add(_S("a")).add(_S("")).add(_S("b")).build()
    assert out == "a\n\nb"


def test_builder_custom_separator():
    out = PromptBuilder(separator="\n---\n").add(_S("a")).add(_S("b")).build()
    assert out == "a\n---\nb"


def test_builder_is_fluent():
    b = PromptBuilder()
    assert b.add(_S("x")) is b


def test_builder_len_reflects_sections_added():
    b = PromptBuilder().add(_S("a")).add(_S("b"))
    assert len(b) == 2


def test_builder_sections_property_returns_copy():
    b = PromptBuilder().add(_S("a"))
    sections = b.sections
    sections.append(_S("b"))  # should not mutate b
    assert len(b) == 1


# ---------------------------------------------------------------------------
# CoreSection
# ---------------------------------------------------------------------------


def test_core_section_uses_default_persona():
    out = CoreSection().render()
    assert "drug discovery" in out.lower()
    assert CORE_INSTRUCTIONS in out


def test_core_section_accepts_custom_persona():
    out = CoreSection(persona="You are a pirate.").render()
    assert "pirate" in out
    assert CORE_INSTRUCTIONS in out


# ---------------------------------------------------------------------------
# ToolsSection
# ---------------------------------------------------------------------------


def _tool(name: str, hidden: bool = False, doc: str = "summary"):
    def fn():
        pass

    fn.__name__ = name
    fn.__doc__ = doc
    return ToolSpec.from_function(fn, name=name, hidden=hidden)


def test_tools_section_empty_registry_renders_empty():
    reg = ToolRegistry()
    assert ToolsSection(reg).render() == ""


def test_tools_section_lists_visible_tools():
    reg = ToolRegistry()
    reg.register(_tool("alpha", doc="alpha docs"))
    reg.register(_tool("beta", doc="beta docs"))
    out = ToolsSection(reg).render()
    assert "AVAILABLE TOOLS" in out
    assert "alpha" in out
    assert "beta" in out
    assert "alpha docs" in out


def test_tools_section_skips_hidden():
    reg = ToolRegistry()
    reg.register(_tool("visible", doc="v"))
    reg.register(_tool("secret", hidden=True, doc="s"))
    out = ToolsSection(reg).render()
    assert "visible" in out
    assert "secret" not in out


def test_tools_section_reflects_live_changes():
    reg = ToolRegistry()
    section = ToolsSection(reg)
    assert section.render() == ""  # empty now
    reg.register(_tool("new", doc="new doc"))
    assert "new" in section.render()


# ---------------------------------------------------------------------------
# SkillsSection
# ---------------------------------------------------------------------------


class _FakeSkillLoader:
    def __init__(self, summaries):
        self._summaries = summaries

    def get_skill_summaries(self):
        return self._summaries


def test_skills_section_with_none_loader():
    assert SkillsSection(None).render() == ""


def test_skills_section_empty_summaries():
    assert SkillsSection(_FakeSkillLoader([])).render() == ""


def test_skills_section_renders_summary():
    loader = _FakeSkillLoader(
        [
            {
                "id": "xx",
                "name": "Xtreme Synthesis",
                "description": "Do wild stuff.",
                "metadata": {"use_cases": "explore"},
            }
        ]
    )
    out = SkillsSection(loader).render()
    assert "SKILLS" in out
    assert "xx" in out
    assert "Xtreme Synthesis" in out
    assert "Do wild stuff." in out
    assert "Use cases: explore" in out


def test_skills_section_respects_excluded():
    loader = _FakeSkillLoader(
        [
            {"id": "a", "name": "A", "description": "keep", "metadata": {}},
            {"id": "b", "name": "B", "description": "drop", "metadata": {}},
        ]
    )
    out = SkillsSection(loader, excluded={"b"}).render()
    assert "keep" in out
    assert "drop" not in out


def test_skills_section_description_truncated_to_120():
    loader = _FakeSkillLoader(
        [
            {
                "id": "long",
                "name": "LongSkill",
                "description": "x" * 500,
                "metadata": {},
            }
        ]
    )
    out = SkillsSection(loader).render()
    # Original would have 500 x's — rendered should have at most ~120
    assert "x" * 121 not in out
