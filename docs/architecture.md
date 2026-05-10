# CAi Architecture

## Overview

CAi has a layered architecture designed around composition:

```
BaseAgent  (execution core: LangGraph + LLM + REPL)
    │
    └── A1pro  (orchestrator — wires everything below)
              ├── execution/ (Python REPL + bash + timeout helpers)
              ├── llm.py     (LLM factory — OpenAI / Anthropic / DeepSeek / Custom)
              ├── prompt/    (PromptBuilder + sections)
              ├── tools/     (ToolRegistry + ReplBridge + Scanners)
              ├── skills/    (SkillLoader — SOP markdown files)
              └── web_ui/    (FastAPI + static frontend)
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
  corporate OpenAI-compatible proxy.

### Code execution subsystem

`CAi/CAi_agent/execution/`

Three small, self-contained helpers. No dependency on the legacy
`base_CAi.tool.*` or `base_CAi.utils` modules:

```
execution/
├── repl.py       # persistent-namespace Python REPL (exec + stdout capture)
├── bash.py       # run_bash_script — subprocess wrapper, bash-explicit
└── timeout.py    # run_with_timeout — ThreadPoolExecutor-based deadline
```

`BaseAgent._node_execute` invokes these through `run_with_timeout` so
each <execute> block has a wall-clock limit. When the agent's REPL
bridge (`tools/repl_bridge.py`) updates the tool namespace, the REPL
picks those tools up on the next call via
`builtins._base_CAi_custom_functions`.

### Interaction modes

| Mode | When to use | How |
|---|---|---|
| Direct text | Questions, explanations, planning | Plain text reply |
| Code execution | Compute, call tools, process data | `<execute>...</execute>` |
| Mixed | Explain + compute in one response | Text + `<execute>` block |

The agent ends a task with `<done/>`. For simple questions it just replies directly.

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
agent.run(prompt)                              # blocking, returns (log, final_content)
agent.run_stream(prompt)                       # generator of {"type", "content"} dicts
agent.run_with_history(prompt, history)        # with prior conversation context
```

The agent is stateless — history is passed explicitly per call.

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
    .add(ToolsSection(tool_registry))         # reads from registry
    .add(SkillsSection(skill_loader))         # reads from loader
    .build()
)
```

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
subscribers react to additions and removals.

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

## A1pro

`CAi/CAi_agent/agent.py`

Thin orchestrator (~150 lines). Its job is to:

1. Create a `ToolRegistry` and attach a `ReplBridge`
2. Run a `ModuleScanner` against `CAi.toolkit` to populate the registry
3. Create a `SkillLoader` (optional)
4. Initialize `BaseAgent` (LLM + LangGraph)
5. Build a `PromptBuilder` with the three default sections
6. Wire `registry.on_change` → auto-rebuild prompt

All the public methods (`add_tool`, `remove_tool`, `list_tools`,
`reload_tools`, `list_skills`, `reload_skills`) delegate to the
appropriate subsystem.

---

## Web UI

`CAi/web_ui/`

```
web_ui/
├── backend/
│   ├── app.py                  # FastAPI — chat, files, conversations
│   └── conversation_store.py   # JSON-based persistence
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── launch.py                   # uvicorn launcher
```

### Chat flow

```
POST /api/chat
    │
    ├── Acquire _chat_lock (asyncio.Lock) — serialises concurrent requests
    ├── Load conversation history from ConversationStore
    ├── Build prompt (user message + workspace path + file refs)
    ├── Call agent.run_with_history(prompt, history)
    │       │
    │       └── Streams {"type", "content"} dicts
    │
    ├── Emit SSE events to frontend
    └── Persist user + assistant messages to ConversationStore
```

### SSE event types

| Type | Content |
|---|---|
| `conversation_id` | Newly created or existing conversation ID |
| `thinking` | Agent reasoning text (before any tags) |
| `code` | Code block from `<execute>` |
| `observation` | Code execution output |
| `text` | Pure text response (no code) |
| `solution` | Final cleaned response (stored in history) |
| `done` | Stream complete |
| `error` | Exception message |

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

All user-facing configuration lives in `CAi/config.py`, loaded from `CAi/.env`:

```bash
LLM_MODEL=claude-sonnet-4-5-20250929
LLM_BASE_URL=http://your-endpoint/v1/
LLM_API_KEY=your_key

TOOL_SERVER_HOST=0.0.0.0
TOOL_SERVER_PORT=8001
WEB_BACKEND_PORT=8000
```

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
├── test_parse_response.py      # BaseAgent message parsing           (11 tests)
├── test_prompt_builder.py      # PromptBuilder + concrete sections   (17 tests)
├── test_prompt_building.py     # A1pro prompt integration            (10 tests)
├── test_tool_spec.py           # ToolSpec.from_function              (12 tests)
├── test_tool_registry.py       # Registry CRUD + observer            (15 tests)
├── test_tool_scanner.py        # ModuleScanner discovery             ( 8 tests)
├── test_repl_bridge.py         # Registry → builtins sync            ( 8 tests)
├── test_agent_execution.py     # Stateless history, tools, code      (10 tests)
├── test_execution_repl.py      # Persistent REPL namespace           (13 tests)
├── test_execution_bash.py      # Bash subprocess wrapper             ( 6 tests)
├── test_execution_timeout.py   # run_with_timeout / pool safety      ( 6 tests)
├── test_llm_factory.py         # LLM provider factory                (26 tests)
├── test_web_concurrency.py     # SSE parsing + chat lock             ( 6 tests)
├── test_pdf_export.py          # Conversation → Markdown → PDF       (17 tests)
├── test_toolkit_client.py      # Tool server HTTP client             (14 tests)
└── test_toolkit_validators.py  # SMILES input validators             ( 8 tests)
                                  Total: 183 tests, ~1.7s runtime (Linux)
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
