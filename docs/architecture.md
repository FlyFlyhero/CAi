# CAi Architecture

## Overview

CAi uses a two-layer agent architecture designed for clarity and extensibility.

```
BaseAgent  (CAi/CAi_agent/base.py)
    │
    └── A1pro  (CAi/CAi_agent/agent.py)
```

**BaseAgent** handles the core execution loop — LLM calls, code execution, and the LangGraph state machine. It has no domain knowledge and no tool dependencies.

**A1pro** extends BaseAgent with drug-discovery-specific capabilities: tool loading, skills (SOPs), and a domain-aware system prompt.

---

## BaseAgent

`CAi/CAi_agent/base.py`

Responsibilities:
- Initialize the LLM via `base_CAi.llm.get_llm`
- Build and run the LangGraph workflow (`generate → execute → generate`)
- Execute Python and Bash code in a sandboxed REPL
- Parse LLM responses (mixed text + code)

### Interaction modes

BaseAgent supports three response modes in a single turn:

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
agent.run(prompt, thread_id)                    # blocking, returns (log, final_content)
agent.run_stream(prompt, thread_id)             # generator of {"type", "content"} dicts
agent.run_with_history(prompt, history, thread_id)  # with prior conversation context
```

---

## A1pro

`CAi/CAi_agent/agent.py`

Extends BaseAgent with:

### Tool loading

Tools are loaded from `CAi/additional_tools` at startup. Each function is:
1. Stored in `self._loaded_tools`
2. Injected into `builtins._base_CAi_custom_functions` so the REPL can call it

```python
agent = A1pro(
    tools_module="CAi.additional_tools",   # default
    exclude_tools=["some_tool"],           # optional
)
agent.add_tool(my_func)    # add at runtime
agent.remove_tool("name")  # remove at runtime
agent.reload_tools()       # hot-reload from module
```

### Skills (SOPs)

Skills are Markdown files in `CAi/CAi_agent/skills/`. They define pre-validated step-by-step workflows for recurring tasks (molecule analysis, virtual screening, etc.).

Only the catalog (name + description) is loaded into the system prompt. The full workflow is fetched on demand:

```python
from CAi.additional_tools.get_skills_content import get_skill_content
workflow = get_skill_content("molecule_analysis")
```

```python
agent.reload_skills()                  # hot-reload from disk
agent.list_skills()                    # list summaries
```

### System prompt structure

The system prompt has three sections, totaling ~1,700 tokens:

```
1. Core instructions     (~400 tokens)
   - Interaction modes
   - Execution rules
   - Planning protocol

2. Tool catalog          (~1,100 tokens)
   - One entry per tool: signature + short description
   - Import instruction

3. Skills catalog        (~200 tokens)
   - One entry per skill: id + description + use cases
   - How to load full workflow
```

---

## Web UI

`CAi/web_ui/`

```
web_ui/
├── backend/
│   ├── app.py              # FastAPI — chat, files, conversations
│   └── conversation_store.py  # JSON-based persistence
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── launch.py               # uvicorn launcher
```

### Chat flow

```
POST /api/chat
    │
    ├── Load conversation history from ConversationStore
    ├── Build prompt (user message + workspace path + file refs)
    ├── Call agent.run_with_history(prompt, history, thread_id=conv_id)
    │       │
    │       └── Streams {"type", "content"} dicts
    │               type: "thinking" | "code" | "observation" | "text"
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
    ├── index.json          # metadata index (id, title, timestamps, message_count)
    └── conv_<id>.json      # full message list per conversation
```

---

## Configuration

All configuration lives in `CAi/config.py`, loaded from `CAi/.env`:

```bash
# CAi/.env
LLM_MODEL=claude-sonnet-4-5-20250929
LLM_BASE_URL=http://your-endpoint/v1/
LLM_API_KEY=your_key

TOOL_SERVER_HOST=0.0.0.0
TOOL_SERVER_PORT=8001
WEB_BACKEND_PORT=8000
```

`base_CAi/config.py` (`default_config`) is used only by the LLM factory and is not the primary config source for A1pro.

---

## Adding tools

1. Add a function to `CAi/additional_tools/template_tools.py`
2. Export it from `CAi/additional_tools/__init__.py`
3. Restart the agent (or call `agent.reload_tools()`)

The function's docstring becomes its description in the system prompt. Keep the first paragraph concise — everything after `Args:` is truncated.

```python
def my_tool(smiles: str, n: int = 10) -> str:
    """
    One-line summary of what this tool does.

    Optionally a second paragraph with more detail.
    This is included in the prompt.

    Args:
        smiles: Input molecule SMILES
        n: Number of results
    """
    ...
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
