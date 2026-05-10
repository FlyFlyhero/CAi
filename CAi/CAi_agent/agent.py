"""
A1pro — CAi 的主力 Agent

继承 BaseAgent（轻量 LangGraph 基类），增加：
- additional_tools 自动加载
- Skills（SOP）目录 + 按需加载
- 精简系统提示词
"""

import builtins
import importlib
import inspect

from CAi.CAi_agent.base import BaseAgent
from CAi.CAi_agent.skills import SkillLoader
from CAi.logger import get_logger

logger = get_logger("CAi.A1pro")


class A1pro(BaseAgent):
    """
    CAi 主力 Agent。

    特性：
    - 基于 BaseAgent 的混合交互模式（文本 + 代码执行）
    - 自动加载 additional_tools
    - Skills（SOP）按需加载
    - 精简系统提示词（~3k tokens）
    """

    def __init__(
        self,
        *,
        llm: str | None = None,
        source=None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: int = 600,
        # Tools
        auto_load_tools: bool = True,
        tools_module: str = "CAi.additional_tools",
        exclude_tools: list[str] | None = None,
        # Skills
        auto_load_skills: bool = True,
        skills_dir: str | None = None,
        exclude_skills: list[str] | None = None,
        # Legacy compat (ignored, kept so callers don't break)
        use_tool_retriever: bool = False,
        expected_data_lake_files: list | None = None,
        commercial_mode: bool = False,
        path: str | None = None,
        **kwargs,
    ):
        # Tool state (must exist before super().__init__ triggers anything)
        self.tools_module = tools_module
        self.exclude_tools = exclude_tools or []
        self._loaded_tools: dict[str, callable] = {}

        # Skills — only initialise the loader if auto_load_skills is on.
        # Otherwise keep it as None so the prompt builder will skip the
        # skills section entirely.
        self.exclude_skills = exclude_skills or []
        if auto_load_skills:
            self.skill_loader = SkillLoader(skills_dir)
        else:
            self.skill_loader = None

        # Init base (builds LLM + workflow, uses our system_prompt property)
        super().__init__(
            llm=llm,
            source=source,
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            system_prompt=None,  # We'll build it after tools load
        )

        # Load tools
        if auto_load_tools:
            self._load_tools()

        # Build final system prompt (needs _loaded_tools to be populated)
        self.system_prompt = self._build_system_prompt()

        # Log summary
        logger.info(
            f"A1pro ready — {len(self._loaded_tools)} tools, "
            f"{len(self.list_skills())} skills"
        )

    # ------------------------------------------------------------------
    # Tool loading
    # ------------------------------------------------------------------

    def _load_tools(self):
        """Load all functions from the tools module."""
        try:
            module = importlib.import_module(self.tools_module)
        except ModuleNotFoundError as e:
            logger.error(f"Tools module not found: {self.tools_module} — {e}")
            return

        for name, func in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_") or name in self.exclude_tools:
                continue
            self._register_tool(name, func)

        logger.info(f"Loaded {len(self._loaded_tools)} tools from {self.tools_module}")

    def _register_tool(self, name: str, func):
        """Register a single tool — make it available in REPL and catalog."""
        self._loaded_tools[name] = func
        # Make callable in code execution
        if not hasattr(builtins, "_base_CAi_custom_functions"):
            builtins._base_CAi_custom_functions = {}
        builtins._base_CAi_custom_functions[name] = func

    def add_tool(self, func):
        """Public API to add a tool at runtime."""
        name = func.__name__
        self._register_tool(name, func)
        # Rebuild prompt to include new tool
        self.system_prompt = self._build_system_prompt()
        logger.info(f"Added tool: {name}")

    def remove_tool(self, name: str):
        """Remove a tool by name."""
        if name in self._loaded_tools:
            del self._loaded_tools[name]
            if hasattr(builtins, "_base_CAi_custom_functions"):
                builtins._base_CAi_custom_functions.pop(name, None)
            self.system_prompt = self._build_system_prompt()
            logger.info(f"Removed tool: {name}")

    # ------------------------------------------------------------------
    # System prompt construction
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """Build the complete system prompt."""
        sections = [
            self._section_core(),
            self._section_tools(),
            self._section_skills(),
        ]
        return "\n\n".join(s for s in sections if s)

    def _section_core(self) -> str:
        """Core instructions — execution protocol + interaction modes."""
        return """\
You are a drug discovery and medicinal chemistry AI assistant.

INTERACTION MODES:
1. DIRECT RESPONSE — For questions, explanations, discussions, or planning,
   reply in plain text. No code needed.
2. CODE EXECUTION — When you need to compute, call tools, or process data,
   wrap code in <execute>...</execute>. Output appears in <observation>.
3. MIXED — You can combine text explanation with code in one response.

EXECUTION RULES:
- Python is default. Use `#!BASH` for shell commands.
- Always print() results so they appear in observations.
- Import tools before use: `from CAi.additional_tools import tool_name`
- Validate SMILES with RDKit before passing to tools.
- Keep code simple. Break complex tasks into multiple rounds.
- If code fails, analyze the error before retrying.

PLANNING (for multi-step tasks):
- Start with a numbered plan. Mark steps [✓] or [✗] as you go.
- Update the plan after each step.

COMPLETION:
- When the task is fully done, end your message with <done/>
- For simple questions, just answer directly (no <done/> needed)."""

    def _section_tools(self) -> str:
        """Tool catalog — one clean listing."""
        if not self._loaded_tools:
            return ""

        lines = [
            "AVAILABLE TOOLS",
            "=" * 50,
            "Import before use: `from CAi.additional_tools import <name>`",
            "",
        ]

        for name, func in self._loaded_tools.items():
            # Skip meta-tools (skill helpers) from the main listing
            if name in ("get_skill_content", "list_available_skills"):
                continue

            sig = str(inspect.signature(func))
            doc = (func.__doc__ or "").strip()

            # Extract just the first paragraph (up to Args/Returns/Example)
            short_doc = self._truncate_doc(doc, max_chars=400)

            lines.append(f"▸ {name}{sig}")
            for dline in short_doc.split("\n"):
                lines.append(f"    {dline}")
            lines.append("")

        return "\n".join(lines)

    def _section_skills(self) -> str:
        """Skills catalog — SOPs available for complex tasks."""
        if self.skill_loader is None:
            return ""

        summaries = self.skill_loader.get_skill_summaries()
        summaries = [s for s in summaries if s["id"] not in self.exclude_skills]

        if not summaries:
            return ""

        lines = [
            "SKILLS — Standard Operating Procedures",
            "=" * 50,
            "",
            "Skills are pre-validated workflows for recurring tasks.",
            "When a user's request matches a skill, load it FIRST:",
            "",
            "  from CAi.additional_tools.get_skills_content import get_skill_content",
            "  workflow = get_skill_content('<skill_id>')",
            "  print(workflow)",
            "",
            "Then follow the workflow step-by-step.",
            "",
            "Available skills:",
        ]

        for s in summaries:
            meta = s.get("metadata", {}) or {}
            lines.append(f"")
            lines.append(f"  • {s['id']} — {s['name']}")
            lines.append(f"    {s['description'][:120]}")
            if meta.get("use_cases"):
                lines.append(f"    Use cases: {meta['use_cases']}")

        return "\n".join(lines)

    @staticmethod
    def _truncate_doc(doc: str, max_chars: int = 400) -> str:
        """Truncate docstring to first meaningful section."""
        if not doc:
            return "No description."

        # Cut at Args/Returns/Example/Notes section
        for marker in ("Args:", "Parameters:", "Returns:", "Example", "Notes:", "---"):
            idx = doc.find(marker)
            if idx > 0:
                doc = doc[:idx].rstrip()
                break

        if len(doc) > max_chars:
            doc = doc[:max_chars].rsplit("\n", 1)[0] + "\n    ..."

        return doc

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def list_tools(self) -> list[str]:
        """Return names of all loaded tools."""
        return list(self._loaded_tools.keys())

    def list_skills(self) -> list[dict]:
        """Return skill summaries (empty list if skills are disabled)."""
        if self.skill_loader is None:
            return []
        return self.skill_loader.get_skill_summaries()

    def reload_tools(self):
        """Hot-reload tools from module."""
        self._loaded_tools.clear()
        if hasattr(builtins, "_base_CAi_custom_functions"):
            builtins._base_CAi_custom_functions.clear()
        self._load_tools()
        self.system_prompt = self._build_system_prompt()
        logger.info("Tools reloaded")

    def reload_skills(self):
        """Hot-reload skills from disk (no-op if skills are disabled)."""
        if self.skill_loader is None:
            logger.warning("reload_skills called but skills are disabled")
            return
        self.skill_loader.reload()
        self.system_prompt = self._build_system_prompt()
        logger.info("Skills reloaded")

    # ------------------------------------------------------------------
    # Web UI integration
    # ------------------------------------------------------------------

    def launch_web_ui(self, port=7000, host="0.0.0.0"):
        """Start the Web UI."""
        from CAi.web_ui.launch import launch
        launch(self, host=host, port=port)
