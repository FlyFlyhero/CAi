# Utility Library — Design Blueprint

## 定位

工具（Tools）是原子操作，由开发者提供。工具函数（Utilities）是组合模块，由 Agent 从执行经验中积累并自行维护。

```
Tools      = 积木（开发者维护，不变）
Utilities  = 搭好的组件（Agent 维护，动态增减）
Skills     = 工作流 SOP（开发者维护，指导"怎么用"）
```

核心原则：

> A1pro 只消费快照，不参与维护。维护由独立的 UtilityManager 在会话间隙完成。

## 架构总览

```
                    会话间隙
                    ╔══════════════════════════════╗
                    ║  UtilityManager              ║
                    ║  - 接收执行日志                ║
                    ║  - 审视代码中可复用的模式       ║
                    ║  - 决定 save / update / delete ║
                    ║  - 写入 _utilities/ 目录       ║
                    ╚══════╤═══════════════════════╝
                           │ 读写                   ▲
                    ┌──────▼──────────────┐         │
                    │  _utilities/  (磁盘)  │         │
                    │  ├── func_a.py       │         │
                    │  ├── func_b.py       │         │
                    │  └── _meta.json      │─────────┘
                    └──────┬──────────────┘   apply_usage()
                           │ 读取快照           (会话结束写回)
    会话开始               │                  会话内
    ╔══════════════════════▼══════════════════════╗
    ║  A1pro                                     ║
    ║  __init__: UtilityRegistry.load_snapshot()  ║
    ║           → 包裹 _monitor_utility           ║
    ║           → 注入 REPL（附带监控装饰器）       ║──── 每次 execute 后
    ║           → 拼进提示词（不再变化）            ║     读取 _utility_usage
    ║                                            ║
    ║  会话中: 只管调用，不想维护                   ║
    ╚════════════════════════════════════════════╝
```

---

## 存储格式

每个工具函数一个 `.py` 文件。元数据用注释行携带（人可读、机器可解析）。

```
agent_workspace/_utilities/
├── parse_docking_table.py
├── filter_by_score.py
├── merge_result_sets.py
└── _meta.json
```

### 单个 utility 文件格式

```python
# @name: parse_docking_table
# @description: Load Vina docking result and sort by affinity score
# @call_count: 12
# @success_count: 11
# @created: 2026-05-10T14:22:00
# @last_used: 2026-05-15T09:10:00

import pandas as pd


def parse_docking_table(path: str) -> "pd.DataFrame":
    """
    Parse a Vina docking output file into a sorted DataFrame.

    The input is tab-separated with columns: mode, affinity, dist_from_best_mode,
    rmsd_lower_bound, rmsd_upper_bound.

    Returns a DataFrame sorted by affinity score (most negative first).
    """
    df = pd.read_csv(path, sep="\t")
    return df.sort_values("affinity")
```

注释头是元数据的唯一来源。`_meta.json` 是索引缓存，可随时从头文件重建。

---

## 运行时监控——在执行空间内埋点

监控不是外部"轮询"，而是直接植入 REPL 内核。对于 LLM 和 utility 函数本身完全透明——它们不知道自己被监控了。

### 原理

```
Parent process                          Kernel subprocess
─────────────                          ─────────────────
                                       ┌─────────────────────┐
load_snapshot()                        │ _utility_usage = {} │  ← 初始化时注入
  │                                    │                     │
  ├── exec() 得到 func_a, func_b       │ def _monitor(func,   │
  │                                    │         name):      │
  ├── 包裹成 monitored_a, monitored_b  │   @wraps(func)      │
  │   （装饰器在 kernel 侧执行）         │   def wrapper(...):  │
  │                                    │     _utility_usage  │
  └── cloudpickle → 注入 kernel ──────▶│       [name] += 1   │
                                       │     try:            │
每次 run_python_repl() 之后:            │       return func() │
  │                                    │     except:          │
  ├── _execute_in_kernel(              │       _utility_usage │
  │     "print(json.dumps(             │         [name+       │
  │       _utility_usage))")  ────────▶│         '_err'] += 1│
  │                                    │       raise         │
  └── 解析返回 → 累积到 parent 侧       │   return wrapper    │
                                       └─────────────────────┘
会话结束:
  └── flush → 写入 .py 文件头注释
```

关键：装饰器代码和 `_utility_usage` 字典都活在 kernel 进程里。函数调用发生的那一刻，计数就自动更新了。不需要 LLM 或任何人手动调 `record_usage()`。

### 管道：注入 → 监控 → 收集 → 落盘

完整实现分四步，全部收敛在 `UtilityRegistry` + `repl.py` 的配合中。

#### Step 1 — 初始化：在 kernel 中植入监控基础设施

```python
# 在 _init_kernel_env() 或首次 load_snapshot() 时注入

_MONITOR_BOOTSTRAP = """
import functools as _functools

_utility_usage = {}

def _monitor_utility(func, name):
    '''包裹一个 utility 函数，自动记录调用次数和成败。'''
    @_functools.wraps(func)
    def wrapper(*args, **kwargs):
        entry = _utility_usage.setdefault(name, {"calls": 0, "errors": 0})
        entry["calls"] += 1
        try:
            return func(*args, **kwargs)
        except Exception:
            entry["errors"] += 1
            raise
    return wrapper
"""
# 通过 _execute_in_kernel(kc, _MONITOR_BOOTSTRAP, timeout=10) 注入
```

#### Step 2 — 注入：加载 utilities 时自动包裹

```python
# UtilityRegistry.load_snapshot()

def load_snapshot(self) -> dict[str, Callable]:
    functions = {}
    for file in self._dir.glob("*.py"):
        spec = UtilitySpec.from_file(file)
        namespace = {}
        exec(spec.code, namespace)
        raw_func = namespace[spec.name]
        functions[spec.name] = raw_func
    return functions
```

然后 A1pro 在注入时多走一步——把监控包裹后的版本送到 kernel：

```python
# A1pro.__init__ 或独立的 _inject_utilities()

utilities = self.utility_registry.load_snapshot()

# 对每个 utility，在 kernel 中执行：
#   func_a = _monitor_utility(func_a, "func_a")
for name in utilities:
    _execute_in_kernel(kc,
        f"{name} = _monitor_utility({name}, {name!r})",
        timeout=10)
```

#### Step 3 — 收集：每次代码执行后拉取使用数据

在 `run_python_repl()` 末尾追加一小段查询：

```python
# repl.py — run_python_repl() 的末尾

def run_python_repl(code: str, timeout: float = 600) -> str:
    # ... 现有逻辑：执行用户代码 + 捕获图像 ...

    # ---- 收集 utility 使用统计 ---------------------------------------
    try:
        collect_code = (
            "import json as _json\n"
            "print(_json.dumps(dict(_utility_usage)) if "
            "'_utility_usage' in dir() else '{}')"
        )
        usage_out, _ = _execute_in_kernel(kc, collect_code, timeout=5)
        if usage_out:
            _accumulate_utility_usage(json.loads(usage_out))
    except Exception:
        pass  # 监控失败不应影响正常执行

    return result
```

`_accumulate_utility_usage()` 是模块级函数，把 kernel 返回的数据累加到 parent 侧的 `_session_usage` dict：

```python
# repl.py — 模块级

_session_usage: dict[str, dict] = {}  # {name: {calls: N, errors: N}}

def _accumulate_utility_usage(kernel_usage: dict) -> None:
    for name, stats in kernel_usage.items():
        entry = _session_usage.setdefault(name, {"calls": 0, "errors": 0})
        entry["calls"] += stats.get("calls", 0)
        entry["errors"] += stats.get("errors", 0)

def flush_utility_usage() -> dict[str, dict]:
    """会话结束时调用，返回本会话的完整使用统计，并重置。"""
    global _session_usage
    result = _session_usage
    _session_usage = {}
    return result
```

#### Step 4 — 落盘：会话结束时写回文件

```python
# 在会话结束处（Web UI 层或 chat_service）

usage = flush_utility_usage()
registry.apply_usage(usage)

# UtilityRegistry.apply_usage:
def apply_usage(self, usage: dict) -> None:
    for name, stats in usage.items():
        spec = self._specs.get(name)
        if spec is None:
            continue
        # 更新内存中的 spec，然后写回文件
        updated = UtilitySpec(
            name=spec.name,
            description=spec.description,
            code=spec.code,
            call_count=spec.call_count + stats["calls"],
            success_count=spec.success_count + (stats["calls"] - stats["errors"]),
            created_at=spec.created_at,
            last_used=datetime.now(),
        )
        updated.to_file(self._dir)
```

### 为什么监控在 kernel 侧而不是 parent 侧？

如果监控 wrapper 在 parent 进程中包裹好再 cloudpickle 过去，那 wrapper 本身是一个 Python 对象，它的闭包变量（计数器）在序列化时就固化了。每次 `run_python_repl` 后计数器变化，但 parent 侧的闭包变量不会同步。

正确做法是：
1. cloudpickle 只传**原始函数**（或直接在 kernel 中 `exec` 源码）
2. 监控装饰器 `_monitor_utility` 的代码在 kernel 中执行
3. 计数器 `_utility_usage` 是 kernel 进程中的一个普通 dict
4. parent 每次执行后拉取它

### 数据流总结

```
Kernel                         Parent                         Disk
──────                         ──────                         ────
_monitor_utility 更新
  _utility_usage dict
        │
        ▼ (每次 execution 后)
   json.dumps() ──────────▶ _accumulate_usage()
                                │
                                │ (累积整个 session)
                                ▼
                           _session_usage dict
                                │
                                │ (session 结束)
                                ▼
                           flush → apply_usage() ─────────▶ .py 文件头注释
                                                              _meta.json
```

---

## 新增组件

### 1. `UtilitySpec` — 不可变描述符

```python
# CAi/CAi_agent/utilities/spec.py

@dataclass(frozen=True)
class UtilitySpec:
    name: str
    description: str       # 单行摘要，出现在提示词中
    code: str              # 完整源码
    call_count: int
    success_count: int
    created_at: datetime
    last_used: datetime | None

    @classmethod
    def from_file(cls, path: Path) -> "UtilitySpec": ...
    def to_file(self, dir_: Path) -> None: ...
    def delete_file(self, dir_: Path) -> None: ...
```

### 2. `UtilityRegistry` — 磁盘 ↔ 内存

```python
# CAi/CAi_agent/utilities/registry.py

class UtilityRegistry:
    """读取 _utilities/ 目录，产出快照给 A1pro。"""

    def __init__(self, utilities_dir: Path, max_utilities: int = 20):
        self._dir = utilities_dir
        self._max = max_utilities
        self._specs: dict[str, UtilitySpec] = {}

    # ---- A1pro 调用 ---------------------------------------------------
    def load_snapshot(self) -> dict[str, Callable]:
        """加载全部 .py 文件，exec 得到原始函数对象。
        调用方负责在注入 kernel 时包裹 _monitor_utility。"""
        ...

    def render_prompt_section(self) -> str:
        """生成提示词片段：函数名 + 签名 + 一句话描述。
        如果没有任何 utility，返回空字符串（PromptBuilder 自动丢弃）。"""
        ...

    # ---- 会话结束时调用 -------------------------------------------------
    def apply_usage(self, usage: dict[str, dict]) -> None:
        """将本会话的使用统计写回 .py 文件头注释和 _meta.json。
        usage: {"func_name": {"calls": N, "errors": N}, ...}"""
        ...

    # ---- UtilityManager 调用 ------------------------------------------
    def save(self, name: str, code: str, description: str) -> None: ...
    def update(self, name: str, code: str, description: str) -> None: ...
    def delete(self, name: str) -> None: ...
    def list_meta(self) -> list[dict]: ...   # 给 UtilityManager 看的摘要
```

### 3. `UtilitiesSection` — 提示词章节

```python
# CAi/CAi_agent/utilities/section.py

class UtilitiesSection(PromptSection):
    """渲染 utility 库的完整接口文档给主 agent。

    不只列函数名，而是解析每个 .py 文件的源码，提取：
    - 函数签名（参数名 + 类型 + 默认值）
    - docstring 第一段（功能描述）
    - "Use when:" 行（使用场景）
    """

    def __init__(self, registry: UtilityRegistry):
        self._registry = registry

    def render(self) -> str:
        specs = self._registry._specs
        if not specs:
            return ""
        lines = [
            "## Utility Functions",
            "",
            "The following functions are already imported. Call them directly.",
            "Do NOT re-implement them.",
            "",
        ]
        for spec in specs.values():
            sig = self._extract_signature(spec.code)
            one_liner, use_when = self._parse_docstring(spec.code)
            lines.append("---")
            lines.append(f"### `{spec.name}({sig})`")
            lines.append(one_liner)
            if use_when:
                lines.append(f"Use when: {use_when}")
            lines.append("")
        return "\n".join(lines)

    def _extract_signature(self, code: str) -> str: ...
    def _parse_docstring(self, code: str) -> tuple[str, str | None]: ...
```

### 4. `UtilityManager` — 独立 curator

```python
# CAi/CAi_agent/utilities/manager.py

class UtilityManager:
    """独立的轻量 agent，只做代码审视和工具库维护。

    不是 BaseAgent 的子类 —— 它不需要代码执行循环，
    只需要一次 LLM 调用 + 文件读写。
    """

    def __init__(self, registry: UtilityRegistry, llm_model: str | None = None):
        self._registry = registry
        self._llm = get_llm(llm_model or "gpt-4o-mini")  # 便宜模型就够

    def maintain(
        self,
        session_log: list[dict],
    ) -> dict[str, list[str]]:
        """
        审视一次会话中的执行日志，更新工具库。

        session_log: A1pro 本次会话产生的 step dict 列表
                     包含 {"type": "AIMessage", "content": ...} 等

        Returns:
            {"saved": [...], "updated": [...], "deleted": [...]}
        """
        # 1. 从 session_log 提取所有 <execute> 块及其 <observation>
        code_blocks = self._extract_executions(session_log)

        # 2. 构建 prompt：当前工具库 + 执行日志 → 决策
        prompt = self._build_maintain_prompt(code_blocks)
        response = self._llm.invoke(prompt)

        # 3. 解析 LLM 返回的决策 → 执行
        actions = self._parse_actions(response.content)
        return self._apply_actions(actions)
```

---

## UtilityManager 的 Prompt 设计

这是整个设计最关键的部分——prompt 质量决定了 curator 会不会把烂代码存进库。

核心原则：**不是照搬执行的代码，而是理解其意图后重写一个更优的版本。**

```
You are a code librarian. Your job is to maintain a small, high-quality
library of reusable utility functions for a drug-discovery AI agent.

## Current Library

{registry.list_meta() 渲染的摘要，含每个函数的 description、call_count、success_count}

## Recent Executions

{从 session_log 提取的代码块 + 执行输出}

## Instructions

Review each code block. Decide whether to SAVE, UPDATE, or DELETE.

### When to SAVE a new utility
The executed code demonstrates a reusable pattern. Save it ONLY after
rewriting it into a well-crafted function:

- *Generalize*: replace hardcoded paths/names/IDs with parameters
- *Type hints*: annotate all parameters and return type
- *Docstring*: follow the required format below (one-line summary, usage
  guidance, parameter descriptions, return value, example)
- *Error handling*: add input validation and clear error messages
- *Self-contained*: all imports inside the function or at file top

Do NOT save:
- Code that hardcodes specific file paths, molecule names, or target IDs
  (unless the parameter IS the path/name/ID)
- Trivial print() or one-liners
- Code that failed or produced errors
- Code that duplicates what an existing utility already does

### Required docstring format for every utility

def my_utility(arg1: type1, arg2: type2 = default) -> ReturnType:
    \"\"\"
    One-line summary of what this does.

    Use when: <concrete scenario where this function is the right choice>

    Args:
        arg1: Description.
        arg2: Description. Default: ...

    Returns:
        Description of return value.

    Example:
        >>> result = my_utility(x, y)
        >>> print(result)
    \"\"\"

The "Use when" line is critical — it tells the main agent when to pick
this function over writing raw code.

### When to UPDATE an existing utility
- The docstring is misleading or the "Use when" scenario is wrong
- The function signature doesn't match how it's actually called
- A better algorithm or implementation exists
- success_count / call_count < 0.7 (the function is unreliable)

### When to DELETE an existing utility
- call_count == 0 for the last 20 sessions
- A newer utility fully supersedes it
- success_count / call_count < 0.5 over more than 10 calls

### Output format
Respond with a JSON list. Each action has {"type": "save"|"update"|"delete", ...}:

{"type": "save", "name": "filter_by_score",
 "description": "Filter molecules by docking score threshold",
 "code": "def filter_by_score(df: pd.DataFrame, threshold: float) -> pd.DataFrame:\\n    \\"\\"\\"\\n    ...\\n    \\"\\"\\"\\n    ..."}

If no actions needed, respond with: []
```

### 保存前 vs 执行时：对比

```
执行时的代码（照搬 = 不行）            保存的 utility（优化后）
─────────────────────────────       ─────────────────────────
df = pd.read_csv("output.txt",      def load_docking_result(
    sep="\t")                           path: str,
df = df[df["affinity"] < -8]            threshold: float = -8.0,
good = df.head(10)                       top_n: int = 10
print(good.to_string())              ) -> "pd.DataFrame":
                                         \"\"\"
                                         Load and filter docking results.
                                         Use when: you have Vina output
                                         and need top-scoring molecules.
                                         ...
                                         \"\"\"
                                         df = pd.read_csv(path, sep="\t")
                                         df = df[df["affinity"] < threshold]
                                         return df.head(top_n)
```

---

## 主 Agent 看到的接口

UtilitiesSection 渲染的不只是函数名，而是从每个 utility 的 docstring 中提取的完整接口信息：

```
## Utility Functions

The following functions are already imported. Call them directly.
Do NOT re-implement them.

---

### load_docking_result(path: str, threshold: float = -8.0, top_n: int = 10) -> pd.DataFrame

Load and filter docking results from a Vina output file.

Use when: you have a Vina output file and need to extract top-scoring
molecules for further analysis.

---

### parse_molecule_table(path: str) -> pd.DataFrame

Parse a CSV or TSV file containing SMILES and properties into a DataFrame.
Auto-detects delimiter.

Use when: you need to load molecular data from a delimited file.

---

### merge_scores(*dataframes: pd.DataFrame, on: str = "smiles") -> pd.DataFrame

Merge multiple scoring result DataFrames on a common column.

Use when: you have results from different tools (Vina, SCScore, toxicity)
and need to combine them into one table for comparison.
---
```

实现：`UtilitiesSection.render()` 不再只读 `spec.description`，而是解析 `.py` 文件源码，提取完整的函数签名和 docstring 第一段 + "Use when" 行。

---

## 单个 utility 文件的最终格式

```python
# @name: load_docking_result
# @description: Load and filter docking results from a Vina output file
# @call_count: 15
# @success_count: 14
# @created: 2026-05-10T14:22:00
# @last_used: 2026-05-15T09:10:00

import pandas as pd


def load_docking_result(
    path: str,
    threshold: float = -8.0,
    top_n: int = 10,
) -> "pd.DataFrame":
    """
    Load and filter docking results from a Vina output file.

    Use when: you have a Vina output file and need to extract top-scoring
    molecules for further analysis.

    Args:
        path: Path to the Vina output file (tab-separated).
        threshold: Affinity cutoff in kcal/mol. Molecules with affinity
            below this value are kept. Default: -8.0.
        top_n: Maximum number of results to return. Default: 10.

    Returns:
        DataFrame sorted by affinity (most negative first), with columns:
        mode, affinity, dist_from_best_mode, rmsd_lbound, rmsd_ubound.

    Example:
        >>> top = load_docking_result("vina_output.txt", threshold=-7.5)
        >>> print(top[["affinity"]].describe())
    """
    if not path.endswith((".txt", ".tsv", ".csv", ".pdbqt")):
        raise ValueError(f"Unsupported file type: {path}")
    df = pd.read_csv(path, sep="\t")
    df = df[df["affinity"] < threshold]
    return df.sort_values("affinity").head(top_n)
```

注释头（`@name`, `@description`, `@call_count` 等）是给 `UtilityRegistry` 快速索引用的。docstring 是给主 agent 看的接口文档。两者各有用途，不互相替代。

---

## A1pro 集成

改动最小化。只加一个属性 + 两句注入：

```python
class A1pro(BaseAgent):
    def __init__(self, ..., utilities_dir: Path | None = None):
        # ... 现有 ToolRegistry / SkillLoader 不变 ...

        # ---- UtilityLibrary ------------------------------------------
        self._utilities_dir = utilities_dir or Path("agent_workspace/_utilities")
        self.utility_registry = UtilityRegistry(self._utilities_dir)

        # ---- Base agent ----------------------------------------------
        super().__init__(...)

        # ---- Prompt --------------------------------------------------
        self.prompt_builder = (
            PromptBuilder()
            .add(CoreSection())
            .add(ToolsSection(self.tool_registry))
            .add(UtilitiesSection(self.utility_registry))   # 新增
            .add(SkillsSection(self.skill_loader, self.exclude_skills))
        )
        self.tool_registry.on_change(self._rebuild_prompt)
        self._rebuild_prompt()

        # 注入到 REPL —— 复用现有管道
        utilities = self.utility_registry.load_snapshot()
        if utilities:
            inject_custom_functions(utilities)
```

---

## 触发点

UtilityManager 的入口不在 A1pro 内部，而在更上层——Web UI 的会话结束处：

```python
# CAi/web_ui/backend/chat_service.py （伪代码）

async def run_chat_stream(agent, prompt, history):
    # ... 现有的 SSE 流式逻辑 ...
    yield {"type": "done"}

    # 会话结束，后台触发 curator
    session_log = collect_execution_steps()
    manager = UtilityManager(agent.utility_registry)
    manager.maintain(session_log)  # 同步或 asyncio.to_thread
```

触发策略：
- **默认**：每次会话结束自动触发（UtilityManager 只分析，不阻塞）
- **可选**：提供 `POST /api/utilities/maintain` 手动触发
- **频率控制**：如果触发太频繁，加冷却期（N 次会话才触发一次）

---

## 删除 `_meta.json` 的生成

`_meta.json` 是索引缓存，从 `.py` 文件头注释重建。不手动编辑。

```python
def rebuild_meta(dir_: Path) -> dict:
    meta = {}
    for f in dir_.glob("*.py"):
        spec = UtilitySpec.from_file(f)
        meta[spec.name] = {
            "call_count": spec.call_count,
            "success_count": spec.success_count,
            "created_at": spec.created_at.isoformat(),
            "last_used": spec.last_used.isoformat() if spec.last_used else None,
        }
    with open(dir_ / "_meta.json", "w") as fp:
        json.dump(meta, fp, indent=2)
    return meta
```

---

## 文件布局

```
CAi/CAi_agent/utilities/
├── __init__.py          # 导出 UtilitySpec, UtilityRegistry, UtilityManager, UtilitiesSection
├── spec.py              # UtilitySpec 数据类
├── registry.py          # UtilityRegistry — 磁盘读写 + 快照
├── section.py           # UtilitiesSection — PromptSection 实现
└── manager.py           # UtilityManager — 独立 curator agent

CAi/CAi_agent/agent.py   # A1pro: 加载 utilities → 包裹监控 → 注入 REPL → 拼提示词
CAi/CAi_agent/execution/repl.py  # 新增: _session_usage, _accumulate_utility_usage(), flush_utility_usage()
```

---

## 和现有系统的关系

| 概念 | 位置 | 由谁维护 | 注入方式 | 提示词可见 |
|------|------|---------|---------|-----------|
| Tools | `CAi/toolkit/` | 开发者 | ReplBridge → cloudpickle | ToolsSection |
| Skills | `skills/*.md` | 开发者 | SkillLoader → 文本插值 | SkillsSection |
| Utilities | `_utilities/*.py` | Agent 自己 | UtilityRegistry → cloudpickle | UtilitiesSection |

三条链路平行，互不干扰。新增模块不触及现有 Tool / Skill / Prompt 代码。

---

## 不做的

- **不让 A1pro 参与维护决策**。A1pro 的提示词只列可用函数，不教它何时保存。
- **不让 UtilityManager 执行代码**。它只生成/修改源码，不运行。代码的正确性由"下次 session 中被调用且成功"来验证。
- **不自动合并两个 utility**。合并需要理解语义，LLM 容易出错。宁愿留两个，让后续的 DELETE 逻辑自然淘汰。
- **不做版本回滚**。`_utilities/` 目录用 git 跟踪即可回滚。
