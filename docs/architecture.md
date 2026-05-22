# CAi Architecture

## Overview

CAi has a layered architecture designed around composition:

```
BaseAgent  (execution core: LangGraph + LLM + REPL)
    │
    └── A1pro  (orchestrator — wires everything below)
              ├── execution/   (Python REPL + bash + timeout helpers)
              ├── llm.py       (LLM factory — OpenAI / Anthropic / DeepSeek / Custom)
              ├── prompt/      (PromptBuilder + sections)
              ├── tools/       (ToolRegistry + ReplBridge + Scanners)
              ├── utilities/   (UtilityRegistry + UtilityManager — self-learning code reuse)
              ├── skills/      (SkillLoader — SOP markdown files)
              └── web_ui/      (FastAPI + static frontend)
```

Each subsystem is small, testable in isolation, and depends only on the
layer below it.

---

## BaseAgent

`CAi/CAi_agent/base.py`

Responsibilities:
- Initialize the LLM via `CAi.CAi_agent.llm.get_llm`
- Build and run the LangGraph workflow (`generate → execute → generate`)
- Execute Python and Bash code via the `execution/` subpackage
- Parse LLM responses (mixed text + code)

Deliberately excludes: tool registration, prompt composition, skill
handling, UI. Those live in dedicated subsystems that A1pro wires up.

### LLM factory

`CAi/CAi_agent/llm.py`

A small provider factory supporting four sources. Auto-detection from
the model name handles the common cases:

| Source      | Auto-detect prefix    | Env var for API key | Endpoint |
|-------------|-----------------------|---------------------|----------|
| `OpenAI`    | `gpt-*`, `o1-*`, `o3-*` | `OPENAI_API_KEY`    | api.openai.com |
| `Anthropic` | `claude-*`            | `ANTHROPIC_API_KEY` | api.anthropic.com |
| `DeepSeek`  | `deepseek-*`          | `DEEPSEEK_API_KEY`  | api.deepseek.com/v1 |
| `Custom`    | (any, when `base_url` is given) | (optional)        | user-supplied (OpenAI-compatible) |

Specialised cases handled:
- OpenAI `gpt-5` / `o1` / `o3` use the Responses API and can't accept
  `stop` or `temperature` — the factory drops both transparently.
- DeepSeek is OpenAI-compatible; we point `ChatOpenAI` at the official
  endpoint instead of introducing a separate client.
- `Custom` is the catch-all for local SGLang / vLLM servers or any
  corporate OpenAI-compatible proxy. Set `LLM_API_KEY=EMPTY` for
  unauthenticated local endpoints.

Auto-detection order: `LLM_SOURCE` env var takes precedence; if unset,
the model name prefix determines the provider.

### Code execution subsystem

`CAi/CAi_agent/execution/`

```
execution/
├── repl.py       # Jupyter kernel REPL — process isolation, ZeroMQ stdout capture
├── bash.py       # run_bash_script — subprocess wrapper
└── timeout.py    # run_with_timeout — ThreadPoolExecutor deadline (used by bash only)
```

`repl.py` runs Python in a persistent **Jupyter IPython kernel** (a
separate OS process). This gives true timeout enforcement via SIGINT /
SIGKILL, thread-safe output capture over ZeroMQ, and the ability to
restart the kernel after a hang without touching the parent process.

See `docs/execution.md` for the full design and message-loop details.

### Interaction modes

| Mode | When to use | How |
|---|---|---|
| Direct text | Questions, explanations, planning | Plain text reply |
| Code execution | Compute, call tools, process data | `<execute>...</execute>` (Python by default) |
| Bash execution | Shell commands | `<execute lang="bash">...</execute>` |
| R execution | R scripts | `<execute lang="r">...</execute>` |
| Mixed | Explain + compute in one response | Text + `<execute>` block |

The agent ends a task with `<done/>`. For simple questions it just replies directly.

The legacy `#!BASH` / `#!R` shebangs on the first line of an attribute-less
`<execute>` block are still recognised so existing conversation logs keep
working, but new code should use the `lang="..."` attribute.

### Tag parser — `agent_tags.py`

`CAi/CAi_agent/agent_tags.py` is the **single source of truth** for the
agent's tag syntax. Every consumer — `BaseAgent`, the web backend, the
CLI, PDF export, `UtilityManager` — imports from this module instead of
re-implementing the regexes locally. This avoids the historic class of
bugs where one parser drifted from another (e.g. one accepting
`#!BASH`, another not).

Key entry points:
- `iter_execute_blocks(content)` — yields `ExecuteBlock(lang, code, attrs)`
- `has_execute_block(content)` — quick check during streaming loop
- `strip_all_tags(content)` — produce a clean prose version
- `wrap_observation(body)` — emit a well-formed `<observation>` tag

Future-proof: the regex captures arbitrary `key="value"` attributes
(see `parse_attrs`) so additions like `timeout="..."` or `id="..."`
don't require a new parser.

Python `<execute>` blocks call `run_python_repl(code, timeout)` directly
(timeout enforced inside the kernel loop). Bash `<execute lang="bash">`
blocks still go through `run_with_timeout(run_bash_script, ...)`.

Tool functions are injected into the kernel via **cloudpickle**
serialisation so closures and locally-defined callables work. The
`builtins._base_CAi_custom_functions` registry is kept in sync for
ReplBridge compatibility. Tools are now injected **once per
`<execute>`-bearing message**, not per code block — multi-block messages
no longer pay the IPC cost N times.

`set_workspace_dir(path)` configures where matplotlib figures are
auto-saved; new image files in the workspace are detected after each
execution and reported in the output so the web UI can display them.

See `docs/execution.md` for the full design and message-loop details.

### LangGraph state machine

```
START → generate ──► execute ──► generate
                │                    │
                └────────────────────┘
                         │
                        END  (when next_step = "end")
```

`next_step` is determined by the LLM response:
- Contains `<execute>` → `"execute"`
- Contains `<done/>` or no action tags → `"end"`

### Public API

```python
agent.run(prompt)                                    # blocking, returns (log, final_content)
agent.run_stream(prompt)                             # generator of {"type", "content"} dicts (legacy)
agent.run_with_history(prompt, history)              # non-streaming with prior conversation context
agent.run_with_history_streaming(prompt, history)    # streaming with prior conversation context
```

The agent is stateless — history is passed explicitly per call.
An optional `context_compress_hook` can be passed to `BaseAgent.__init__`
to trim history when it grows large.

---

## Prompt subsystem

`CAi/CAi_agent/prompt/`

Composition over inheritance. Every section of the system prompt is a
`PromptSection` object; `PromptBuilder` assembles them.

```
prompt/
├── section.py     # PromptSection ABC — single abstract method: render() -> str
├── builder.py     # PromptBuilder — fluent, drops empty sections
└── sections.py    # CoreSection, ToolsSection, SkillsSection
```

Example of composing a prompt:

```python
prompt = (
    PromptBuilder()
    .add(CoreSection())                       # persona + interaction rules
    .add(UtilitiesSection(utility_registry))  # PREFERRED helpers (try first)
    .add(ToolsSection(tool_registry))         # FALLBACK low-level tools
    .add(SkillsSection(skill_loader))         # SOPs for recurring workflows
    .build()
)
```

Section ordering matters — Utilities are listed BEFORE Tools so the
agent is steered to prefer high-level, pre-validated helpers over
re-implementing logic with raw tool calls. ToolsSection acts as a
fallback when no utility covers the task.

Adding a new section is a one-file change: subclass `PromptSection`,
implement `render()`, and `.add(YourSection())` in A1pro's constructor.
A1pro's own code doesn't need to grow.

Sections whose `render()` returns an empty string are silently dropped
from the output, so conditional inclusion is free.

---

## Tools subsystem

`CAi/CAi_agent/tools/`

Four narrowly-scoped modules:

```
tools/
├── spec.py         # ToolSpec — immutable tool descriptor
├── registry.py     # ToolRegistry — observable in-memory catalog
├── scanner.py      # ToolScanner + ModuleScanner — strategies for discovery
└── repl_bridge.py  # ReplBridge — mirrors registry into builtins for REPL
```

### ToolSpec

Frozen dataclass. `ToolSpec.from_function(func)` handles all the tedious
work (extract name, compute `inspect.signature`, truncate docstring).
Tools become data — you can pass them around, compare them, put them in sets.

```python
spec = ToolSpec.from_function(
    my_func,
    source="module:CAi.toolkit",
    hidden=False,          # show in prompt catalog?
    tags={"chemistry"},
)
```

### ToolRegistry

The single source of truth. Observable — `on_change(callback)` lets
subscribers react to additions and removals. Thread-safe via `RLock`.

```python
registry = ToolRegistry()
registry.register(spec)
registry.on_change(rebuild_prompt)   # auto-refresh prompt when tools change
```

### ReplBridge

Subscribes to a registry and keeps `builtins._base_CAi_custom_functions`
in sync. Hidden tools are still injected (they're callable from code) —
"hidden" only affects the prompt catalog.

### ModuleScanner

Strategy pattern for discovery. Current implementation scans a Python
module's top-level functions. Future: `YamlConfigScanner`,
`EntryPointScanner`, etc. — without touching A1pro.

```python
scanner = ModuleScanner(
    "CAi.toolkit",
    exclude={"deprecated_fn"},
    hidden={"get_skill_content", "list_available_skills"},
)
for spec in scanner.scan():
    registry.register(spec)
```

---

## Utilities subsystem

`CAi/CAi_agent/utilities/`

Self-learning code reuse library. The agent accumulates reusable functions
from execution experience; an independent curator (UtilityManager) maintains
quality in between sessions.

```
utilities/
├── spec.py       # UtilitySpec — immutable descriptor (metadata + source code)
├── registry.py   # UtilityRegistry — disk ↔ memory, CRUD, usage tracking
├── section.py    # UtilitiesSection — PromptSection rendering for the agent
└── manager.py    # UtilityManager — independent LLM-based curator
```

### Design principles

```
Tools      = building blocks (developer-maintained, static)
Utilities  = assembled components (agent-maintained, dynamic)
Skills     = workflow SOPs (developer-maintained, guides "how to use")
```

The main agent (A1pro) only consumes a snapshot of utilities — it never
participates in maintenance decisions. Maintenance is handled by the
UtilityManager during session gaps.

### UtilitySpec

Frozen dataclass. Each utility is stored as a single `.py` file with
metadata in comment headers (`@name`, `@description`, `@call_count`,
`@success_count`, `@created`, `@last_used`). Human-readable, git-trackable,
machine-parseable.

```python
spec = UtilitySpec.from_file(Path("_utilities/load_docking_result.py"))
# spec.name, spec.description, spec.code, spec.call_count, ...
```

### UtilityRegistry

Disk ↔ memory bridge. Thread-safe (RLock). Provides:
- `load_snapshot()` — exec each utility, return `{name: callable}` dict
- `apply_usage(stats)` — update call/success counts after session
- `save()` / `update()` / `delete()` — CRUD for UtilityManager
- `on_change(callback)` — observer protocol (mirrors ToolRegistry).
  A1pro subscribes so newly-saved utilities are re-injected into the
  live kernel without restart.
- Enforces a configurable max (default: 20). When the directory contains
  more files than the limit on load, the LRU files are deleted from disk
  (not just hidden in memory) so we don't accumulate stale `.py` files.

### UtilitiesSection

`PromptSection` subclass. Renders each utility's signature, one-line
description, and "Use when:" guidance extracted from docstrings via AST
parsing. Returns empty string when no utilities exist (auto-dropped by
PromptBuilder).

### Runtime monitoring

Utilities are injected into the Jupyter kernel with transparent monitoring
wrappers. The `_monitor_utility` decorator in the kernel tracks calls and
errors in a `_utility_usage` dict. After each code execution, the parent
process collects stats via a `__UTIL_USAGE__:` prefixed JSON line, then
accumulates them in `_session_usage`. At session end, `flush_utility_usage()`
returns the data for `apply_usage()`.

### UtilityManager

Independent lightweight curator — not a BaseAgent subclass. Uses a cheap
LLM (e.g. gpt-4o-mini) to review session execution logs and decide
whether to SAVE new utilities, UPDATE existing ones, or DELETE
underperforming ones. Key rules:
- Never copies executed code verbatim — rewrites into generalized,
  type-annotated, documented functions
- DELETE candidates: success_rate < 0.5 over 10+ calls, or unused for
  20+ sessions
- Triggered asynchronously at session end (non-blocking)

The curator runs on its own LLM, distinct from the main agent. A1pro
exposes ``self.curator_llm`` (lazy-built) which all UtilityManager call
sites use. By default the curator reuses ``self.llm`` so existing setups
keep working unchanged. Set any of ``CURATOR_MODEL`` / ``CURATOR_SOURCE``
/ ``CURATOR_BASE_URL`` / ``CURATOR_API_KEY`` / ``CURATOR_TEMPERATURE`` in
``CAi/.env`` (or pass the matching ``curator_*`` constructor args) to
specialise. Typical use: keep a strong model for the main agent and run
the curator on a cheaper model.

Robustness measures:
- LLM is invoked with `bind(stop=None)` so the curator response isn't
  truncated when the prompt template contains `</execute>` examples
  (the main agent's stop sequence).
- Generated code is validated statically before persistence:
  - `ast.parse` for syntax
  - the named function actually exists at top level
  - all top-level `import` / `from ... import` references resolve via
    `importlib.util.find_spec` (in-function imports are skipped — they
    are commonly optional/lazy and wrapped in try/except).
  Rejected actions are reported in a `rejected` bucket alongside
  `saved/updated/deleted`.
- JSON parsing tries direct, fenced, and bare-array strategies in order
  so curator responses formatted slightly differently still apply.
- Every maintain/preview call writes a JSON trace to
  `<utilities_dir>/_traces/` for debugging.

### Storage layout

```
agent_workspace/_utilities/
├── load_docking_result.py    # individual utility files
├── parse_molecule_table.py
├── merge_scores.py
└── _meta.json                # index cache (rebuildable from .py headers)
```

### Relationship to other subsystems

| Concept | Location | Maintained by | Injection | Prompt visible |
|---------|----------|---------------|-----------|----------------|
| Tools | `CAi/toolkit/` | Developer | ReplBridge → cloudpickle | ToolsSection |
| Utilities | `_utilities/*.py` | Agent (UtilityManager) | inject_utilities_with_monitoring | UtilitiesSection |
| Skills | `skills/*.md` | Developer | SkillLoader → text | SkillsSection |

Three parallel pipelines, no interference. The utilities subsystem does
not touch existing Tool / Skill / Prompt code.

---

## A1pro

`CAi/CAi_agent/agent.py`

Thin orchestrator. Its job is to:

1. Create a `ToolRegistry` and attach a `ReplBridge`
2. Run a `ModuleScanner` against `CAi.toolkit` to populate the registry
3. Create a `UtilityRegistry` and inject utilities with monitoring (optional)
4. Create a `SkillLoader` (optional)
5. Initialize `BaseAgent` (LLM + LangGraph)
6. Build a `PromptBuilder` with the four default sections
7. Wire `registry.on_change` → auto-rebuild prompt

All the public methods (`add_tool`, `remove_tool`, `list_tools`,
`reload_tools`, `list_skills`, `reload_skills`) delegate to the
appropriate subsystem. `launch_web_ui(port, host)` starts the FastAPI
server directly from the agent instance.

---

## Web UI

`CAi/web_ui/`

```
web_ui/
├── backend/
│   ├── app.py                  # FastAPI instance + router registration (thin layer)
│   ├── deps.py                 # Shared singletons + FastAPI Depends() providers
│   ├── chat_service.py         # Business logic: prompt building, SSE iteration, response cleaning
│   ├── conversation_store.py   # JSON-based conversation persistence
│   ├── pdf_export.py           # Conversation → Markdown → PDF
│   └── routers/
│       ├── chat.py             # POST /api/chat, POST /api/chat/cancel, GET /api/health
│       ├── conversations.py    # CRUD for /api/conversations
│       ├── files.py            # Upload / list / download / delete + PDF export
│       └── workspace.py        # DELETE /api/workspace, POST /api/reset
├── frontend/
│   ├── index.html
│   ├── js/                     # ES modules: main, chat, conversations, files, utilities, state
│   └── styles.css
└── launch.py                   # uvicorn launcher + SPA catch-all routing
```

### Dependency graph

```
app.py
  ├── routers/chat.py        → deps.py, chat_service.py
  ├── routers/conversations.py → deps.py
  ├── routers/files.py       → deps.py
  └── routers/workspace.py   → deps.py

deps.py
  └── conversation_store.py, repl.set_workspace_dir
```

`deps.py` owns all mutable singletons (`_agent`, `_store`, `_chat_lock`,
`_cancel_events`) and exposes them as FastAPI `Depends()` callables so
each router can declare its needs without importing globals.
`chat_service.py` holds pure functions with no FastAPI dependency —
testable without spinning up the app.

See `docs/web_ui_backend.md` for the full design rationale.

### API routes

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Streaming chat (SSE) |
| `POST` | `/api/chat/cancel` | Cancel running generation |
| `GET` | `/api/conversations` | List all conversations |
| `POST` | `/api/conversations` | Create conversation |
| `GET` | `/api/conversations/{id}` | Get conversation with messages |
| `DELETE` | `/api/conversations/{id}` | Delete conversation |
| `PATCH` | `/api/conversations/{id}/title` | Rename conversation |
| `POST` | `/api/upload` | Upload file to workspace |
| `GET` | `/api/files` | List workspace files |
| `GET` | `/api/files/{filename}` | Download file |
| `DELETE` | `/api/files/{filename}` | Delete file |
| `DELETE` | `/api/workspace` | Clear workspace files |
| `POST` | `/api/reset` | Full reset |
| `POST` | `/api/export-pdf` | Export conversation to PDF |
| `POST` | `/api/utilities/maintain` | Manually trigger utility maintenance |
| `GET` | `/api/utilities/list` | List learned utilities (with status badges) |
| `GET` | `/api/utilities/detail/{name}` | Full data + source code for one utility |
| `DELETE` | `/api/utilities/detail/{name}` | Remove a utility from the library |
| `GET` | `/api/utilities/traces` | Recent UtilityManager LLM call traces |
| `GET` | `/api/utilities/traces/{filename}` | One trace (full prompt + response) |
| `GET` | `/api/health` | Health check |

### Chat flow

```
POST /api/chat
    │
    ├── Acquire _chat_lock (asyncio.Lock) — serialises concurrent requests
    ├── Load conversation history from ConversationStore
    ├── Build prompt (user message + workspace path + file refs)
    ├── Call agent.run_with_history_streaming(prompt, history)
    │       │
    │       └── Streams {"type", "content"} dicts
    │
    ├── Emit SSE events to frontend
    ├── Persist user + assistant messages to ConversationStore
    └── Async task: flush utility usage → apply_usage → UtilityManager.maintain()
```

### SSE event types

| Type | Content |
|---|---|
| `conversation_id` | Newly created or existing conversation ID |
| `token` | Single LLM token/chunk as it arrives |
| `message_end` | Full message text (complete LLM turn) |
| `observation` | Code execution output |
| `solution` | Final cleaned response (stored in history) |
| `done` | Stream complete |
| `error` | Exception message |

Generation can be interrupted mid-stream via `POST /api/chat/cancel`,
which sets a per-conversation `asyncio.Event` checked by the SSE loop.

### Conversation persistence

`ConversationStore` stores conversations as JSON files:

```
agent_workspace/
└── _conversations/
    ├── index.json          # metadata index
    └── conv_<id>.json      # full message list per conversation
```

---

## Configuration

All user-facing configuration lives in `CAi/config.py`, loaded from environment
variables (or a `CAi/.env` file):

```bash
# Main agent LLM
LLM_MODEL=claude-sonnet-4-5-20250929
LLM_SOURCE=                        # auto-detect from model name if unset
LLM_BASE_URL=http://your-endpoint/v1/
LLM_API_KEY=your_key
LLM_TEMPERATURE=0.7

# Curator LLM (UtilityManager) — optional, falls back to LLM_* when unset
CURATOR_MODEL=deepseek-v4-flash
CURATOR_SOURCE=Custom
CURATOR_BASE_URL=
CURATOR_API_KEY=
CURATOR_TEMPERATURE=0.2

# Network
TOOL_SERVER_HOST=0.0.0.0
TOOL_SERVER_PORT=8001
WEB_BACKEND_HOST=0.0.0.0
WEB_BACKEND_PORT=8000
```

The CLI entry point (`python -m CAi.main`) accepts `--port`, `--model`,
`--source`, `--base-url`, `--api-key`, and `--temperature` flags that
override the env-var defaults at startup. Curator overrides are
configured via env vars or directly via `A1pro(curator_llm=..., ...)`.

---

## Adding tools

1. Add a function to `CAi/toolkit/functions/generation.py` or `evaluation.py`
2. Re-export it from `CAi/toolkit/__init__.py` (and from `functions/__init__.py`)
3. Restart the agent (or call `agent.reload_tools()`)

The function's docstring becomes its catalog description. Text after
`Args:` is truncated, so keep the summary in the first paragraph.

```python
def my_tool(smiles: str, n: int = 10) -> str:
    """
    One-line summary of what this tool does.

    Optional second paragraph with more detail — still included in the prompt.

    Args:
        smiles: Input molecule SMILES        # truncated out of the prompt
        n: Number of results
    """
    ...
```

To register a tool without exposing it in the prompt (for skill helpers etc.):

```python
agent.add_tool(helper_fn, hidden=True)
```

The built-in toolkit currently ships **10 drug-discovery tools** plus
**2 hidden skill helpers** (`get_skill_content`, `list_available_skills`):

| Category | Functions |
|----------|-----------|
| Evaluation | `calculate_scscore`, `predict_molecule_toxicity`, `predict_antibacterial_pmic`, `perform_molecular_docking_vina` |
| Generation | `generate_scaffold_analogs`, `generate_libinvent_decorations`, `generate_molecules_for_pocket`, `generate_molecules_reinvent4_denovo`, `generate_molecules_reinvent4_libinvent`, `generate_molecules_reinvent4_mol2mol` |

## Adding skills

Create a Markdown file in `CAi/CAi_agent/skills/`:

```markdown
# Skill Name

## Description
Brief description (shown in the catalog).

## Metadata
**Category**: Drug Discovery
**Required Tools**: calculate_scscore, predict_molecule_toxicity
**Difficulty**: Medium
**Use Cases**: Lead optimization, candidate ranking

---

## Workflow
Step-by-step instructions...
```

The file name (without `.md`) becomes the skill ID.

## Adding a custom prompt section

Subclass `PromptSection`, wire it into A1pro:

```python
from CAi.CAi_agent.prompt import PromptSection

class WorkspaceSection(PromptSection):
    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
    def render(self) -> str:
        return f"Your workspace directory is: {self.workspace_dir}"

# In A1pro.__init__ (or after construction):
agent.prompt_builder.add(WorkspaceSection("/tmp/work"))
agent._rebuild_prompt()
```

---

## Testing

The project uses pytest. Install dev dependencies:

```bash
pip install -e ".[dev]"
```

Run the full suite:

```bash
pytest
```

### Test layout

```
tests/
├── conftest.py                 # FakeLLM fixtures, no credentials needed
├── test_parse_response.py      # BaseAgent message parsing
├── test_prompt_builder.py      # PromptBuilder + concrete sections
├── test_prompt_building.py     # A1pro prompt integration
├── test_tool_spec.py           # ToolSpec.from_function
├── test_tool_registry.py       # Registry CRUD + observer
├── test_tool_scanner.py        # ModuleScanner discovery
├── test_repl_bridge.py         # Registry → builtins sync
├── test_utility_spec.py        # UtilitySpec file I/O + parsing
├── test_utility_registry.py    # UtilityRegistry CRUD + usage tracking
├── test_utilities_section.py   # UtilitiesSection prompt rendering
├── test_utility_manager.py     # UtilityManager curator logic (mock LLM)
├── test_utility_monitoring.py  # Kernel-side monitoring + flush
├── test_utility_agent_integration.py   # A1pro + utilities end-to-end
├── test_utility_session_lifecycle.py   # Full session: inject → call → collect → persist
├── test_agent_execution.py     # Stateless history, tools, code
├── test_execution_repl.py      # Persistent REPL namespace
├── test_execution_bash.py      # Bash subprocess wrapper
├── test_execution_timeout.py   # run_with_timeout / pool safety
├── test_llm_factory.py         # LLM provider factory
├── test_web_concurrency.py     # SSE parsing + chat lock
├── test_pdf_export.py          # Conversation → Markdown → PDF
├── test_toolkit_client.py      # Tool server HTTP client
└── test_toolkit_validators.py  # SMILES input validators
```

All tests use a `FakeLLM` stub that returns scripted responses — no
network, no API keys, no credentials required.

### Key invariants exercised

- Conversation history is passed explicitly; nothing leaks between calls
- The `_chat_lock` in the web backend serialises concurrent requests
- `<done/>` does not bleed into "thinking" or "text" output fields
- Tool docstrings are truncated before the `Args:` section in the prompt
- Registry observers are fail-isolated (one bad listener doesn't block others)
- ReplBridge injects hidden tools (callable) but ToolsSection omits them (catalog)
- PromptBuilder drops sections that render to an empty string
- `flush_utility_usage()` resets `_session_usage` to empty after returning
- `apply_usage()` correctly increments file-header counters
- UtilityManager.maintain() never raises — all errors are caught and logged
- Kernel restart re-injects monitoring bootstrap and re-wraps utilities
