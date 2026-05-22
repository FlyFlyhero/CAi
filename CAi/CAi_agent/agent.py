"""
A1pro — CAi 的主力 Agent

A1pro 本身只做编排工作：
- 装配 ToolRegistry + ReplBridge（工具子系统）
- 装配 SkillLoader（可选）
- 装配 PromptBuilder（提示词子系统）
- 继承 BaseAgent 获得 LangGraph workflow

所有脏活（扫描、拼提示词、REPL 注入）都委托给专职模块。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from CAi.CAi_agent.base import BaseAgent
from CAi.CAi_agent.prompt import (
    CoreSection,
    PromptBuilder,
    SkillsSection,
    ToolsSection,
)
from CAi.CAi_agent.skills import SkillLoader
from CAi.CAi_agent.tools import ModuleScanner, ReplBridge, ToolRegistry, ToolSpec
from CAi.logger import get_logger

logger = get_logger("CAi.A1pro")

# These helper tools are used by skills themselves. They should be callable
# from the REPL but NOT advertised in the tool catalog — skills document
# when to use them in the SKILLS section instead.
_SKILL_HELPER_TOOLS = frozenset({"get_skill_content", "list_available_skills"})


class A1pro(BaseAgent):
    """CAi 主力 Agent。"""

    def __init__(
        self,
        *,
        llm: str | None = None,
        source=None,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        timeout_seconds: int = 600,
        # Tools
        auto_load_tools: bool = True,
        tools_module: str = "CAi.toolkit",
        exclude_tools: Iterable[str] | None = None,
        # Skills
        auto_load_skills: bool = True,
        skills_dir: str | None = None,
        exclude_skills: Iterable[str] | None = None,
        # Utilities
        auto_load_utilities: bool = True,
        utilities_dir: str | None = None,
    ):
        # -------- Tool subsystem ---------------------------------------
        self.tool_registry = ToolRegistry()
        self.repl_bridge = ReplBridge(self.tool_registry)
        self.tools_module = tools_module

        if auto_load_tools:
            scanner = ModuleScanner(
                tools_module,
                exclude=set(exclude_tools or []),
                hidden=_SKILL_HELPER_TOOLS,
            )
            for spec in scanner.scan():
                self.tool_registry.register(spec)

        # -------- Skills -----------------------------------------------
        self.exclude_skills = set(exclude_skills or [])
        self.skill_loader: SkillLoader | None = (
            SkillLoader(skills_dir) if auto_load_skills else None
        )

        # -------- Utility subsystem ------------------------------------
        self.utility_registry = None
        if auto_load_utilities:
            from CAi.CAi_agent.utilities import UtilityRegistry

            util_dir = Path(utilities_dir) if utilities_dir else Path("agent_workspace/_utilities")
            self.utility_registry = UtilityRegistry(util_dir)

            # Inject into REPL with monitoring
            self._reinject_utilities()
            # Re-inject + rebuild prompt whenever the library mutates
            # (UtilityManager save/update/delete, Web UI delete, etc.).
            self.utility_registry.on_change(self._on_utilities_changed)

        # -------- Base agent (builds LLM + workflow) -------------------
        super().__init__(
            llm=llm,
            source=source,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            system_prompt="",  # overwritten right after by _rebuild_prompt
        )

        # -------- Prompt subsystem -------------------------------------
        # Section order matters: Utilities go BEFORE Tools so the agent
        # is steered to prefer high-level helpers over raw tool calls.
        builder = PromptBuilder().add(CoreSection())
        if self.utility_registry:
            from CAi.CAi_agent.utilities import UtilitiesSection

            builder.add(UtilitiesSection(self.utility_registry))
        builder.add(ToolsSection(self.tool_registry))
        builder.add(SkillsSection(self.skill_loader, self.exclude_skills))
        self.prompt_builder = builder

        # Registry → prompt: auto-rebuild whenever tools change.
        self.tool_registry.on_change(self._rebuild_prompt)
        self._rebuild_prompt()

        logger.info(
            "A1pro ready — %d tools, %d skills, %d utilities",
            len(self.tool_registry),
            len(self.list_skills()),
            len(self.utility_registry) if self.utility_registry else 0,
        )

    # ------------------------------------------------------------------
    # Prompt refresh
    # ------------------------------------------------------------------

    def _rebuild_prompt(self) -> None:
        self.system_prompt = self.prompt_builder.build()

    # ------------------------------------------------------------------
    # Utility lifecycle
    # ------------------------------------------------------------------

    def _reinject_utilities(self) -> None:
        """(Re-)load utilities from the registry into the live kernel.

        Called once at startup and again whenever the library mutates so
        newly-saved utilities are immediately callable from the REPL —
        no agent restart required.
        """
        if self.utility_registry is None:
            return
        utilities = self.utility_registry.load_snapshot()
        if not utilities:
            return
        from CAi.CAi_agent.execution.repl import inject_utilities_with_monitoring

        inject_utilities_with_monitoring(utilities)

    def _on_utilities_changed(self) -> None:
        """Registry observer: refresh kernel namespace and prompt catalog."""
        try:
            self._reinject_utilities()
        except Exception:
            logger.exception("Failed to re-inject utilities after registry change")
        # The UtilitiesSection reads from registry.specs, so prompt content
        # stays current automatically — but we still need to rebuild the
        # cached system_prompt string.
        self._rebuild_prompt()

    # ------------------------------------------------------------------
    # Tool API (backward compatible)
    # ------------------------------------------------------------------

    def add_tool(
        self,
        func: Callable,
        *,
        hidden: bool = False,
        tags: Iterable[str] = (),
    ) -> None:
        """Register a tool at runtime. Prompt and REPL update automatically."""
        spec = ToolSpec.from_function(func, hidden=hidden, tags=tags)
        self.tool_registry.register(spec)
        logger.info("Added tool: %s", spec.name)

    def remove_tool(self, name: str) -> bool:
        """Remove a tool. Returns True if it existed."""
        removed = self.tool_registry.unregister(name)
        if removed:
            logger.info("Removed tool: %s", name)
        return removed

    def list_tools(self, *, include_hidden: bool = False) -> list[str]:
        """Return registered tool names."""
        return self.tool_registry.names(include_hidden=include_hidden)

    def reload_tools(self) -> None:
        """Hot-reload tools from the configured module."""
        self.tool_registry.clear()
        scanner = ModuleScanner(self.tools_module, hidden=_SKILL_HELPER_TOOLS)
        for spec in scanner.scan():
            self.tool_registry.register(spec)
        logger.info("Tools reloaded")

    # ------------------------------------------------------------------
    # Skill API
    # ------------------------------------------------------------------

    def list_skills(self) -> list[dict]:
        """Return skill summaries (empty list if skills are disabled)."""
        if self.skill_loader is None:
            return []
        return self.skill_loader.get_skill_summaries()

    def reload_skills(self) -> None:
        """Hot-reload skills from disk (no-op if skills are disabled)."""
        if self.skill_loader is None:
            logger.warning("reload_skills called but skills are disabled")
            return
        self.skill_loader.reload()
        self._rebuild_prompt()
        logger.info("Skills reloaded")

    # ------------------------------------------------------------------
    # Web UI integration
    # ------------------------------------------------------------------

    def launch_web_ui(self, port: int = 7000, host: str = "0.0.0.0") -> None:
        """Start the Web UI."""
        from CAi.web_ui.launch import launch

        launch(self, host=host, port=port)
