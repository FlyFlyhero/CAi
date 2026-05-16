# Web UI Backend

`CAi/web_ui/backend/`

This document covers the design of the FastAPI backend after the
modularisation refactor. For the earlier monolithic layout see git
history (`app.py` before the split).

---

## Module map

```
backend/
├── app.py                # FastAPI app factory — thin, no business logic
├── deps.py               # Shared singletons + FastAPI Depends() providers
├── chat_service.py       # Pure business logic — no FastAPI dependency
├── conversation_store.py # File-based conversation persistence (JSON)
├── pdf_export.py         # Markdown → PDF via WeasyPrint / Pandoc
└── routers/
    ├── chat.py           # POST /api/chat, POST /api/chat/cancel, GET /api/health
    ├── conversations.py  # GET/POST/DELETE/PATCH /api/conversations[/{id}]
    ├── files.py          # /api/upload, /api/files/*, /api/export-pdf
    └── workspace.py      # DELETE /api/workspace, POST /api/reset
```

---

## app.py

Intentionally thin. Creates the `FastAPI` instance, adds CORS
middleware, registers the four routers, and re-exports `set_agent` so
`launch.py` keeps its existing import path:

```python
from CAi.web_ui.backend.app import app, set_agent
```

No route handlers, no globals, no business logic live here.

---

## deps.py

The single source of truth for shared mutable state.

### Singletons

| Name | Type | Purpose |
|------|------|---------|
| `_agent` | `BaseAgent \| None` | The running agent instance |
| `_store` | `ConversationStore` | Conversation persistence |
| `_workspace_dir` | `str` | Absolute path served by `/api/files/*` |
| `_chat_lock` | `asyncio.Lock` | Serialises concurrent chat requests |
| `_cancel_events` | `dict[str, asyncio.Event]` | Per-conversation stop signals |

### Initialisation

`set_agent(agent)` is called once by `launch.py`. It stores the agent
reference and calls `repl.set_workspace_dir()` so the Jupyter kernel
saves plots to the same directory that `/api/files/*` serves.

### Dependency providers

Each provider is a plain callable returned to FastAPI's `Depends()`:

```python
# Router declares what it needs:
@router.post("/chat")
async def chat(
    agent   = Depends(get_agent),        # raises 503 if not initialised
    store   = Depends(get_store),
    lock    = Depends(get_chat_lock),
    cancels = Depends(get_cancel_events),
):
    ...
```

`get_agent_optional()` is a variant that returns `None` rather than
raising — used by `/api/health` so the health check works before the
agent is registered.

---

## chat_service.py

Pure functions with no FastAPI imports. Can be unit-tested directly.

### `build_prompt(text, ref_files, workspace_dir) -> str`

Appends workspace path and referenced file paths to the user message
so the agent knows where to find uploaded data.

### `clean_stored_answer(full_content) -> str`

Strips `<execute>…</execute>`, `<observation>…</observation>`, and
`<done/>` tags before saving the assistant turn to conversation history.
The stored content reads as clean prose.

### `extract_parts(content) -> dict`

Splits a raw assistant message into `thinking`, `code`, `observation`,
and `text` keys. Used by `pdf_export.py` when reformatting messages for
the PDF renderer.

### `async_iter_agent(agent, prompt, history, cancel_event) -> AsyncGenerator`

Adapts the synchronous `agent.run_with_history_streaming()` generator
into an async generator safe for FastAPI's `StreamingResponse`.

**Why the sentinel pattern?**
`StopIteration` cannot cross an `asyncio.Future` boundary — if `next()`
raises inside `run_in_executor`, asyncio converts it to a
`RuntimeError`. We catch it on the worker side and return a `_DONE`
sentinel object instead, keeping the async loop clean.

**Cancellation:**
Between each chunk we check `cancel_event.is_set()`. If set, the loop
exits. The currently-executing `run_in_executor` step (LLM fetch or
code execution) runs to completion in the background thread but its
result is discarded.

---

## routers/chat.py

Handles the SSE streaming chat endpoint, the cancel endpoint, and the
health check.

### Chat flow

```
POST /api/chat
    │
    ├── Resolve or create conversation ID
    ├── Acquire _chat_lock  ← serialises all requests (REPL is process-global)
    ├── Load conversation history from ConversationStore
    ├── build_prompt()  ← injects workspace path + file refs
    ├── Register cancel_event in _cancel_events[conv_id]
    │
    ├── for step in async_iter_agent(...):
    │       emit SSE: token | message_end | observation
    │
    ├── clean_stored_answer()
    ├── emit SSE: solution
    ├── ConversationStore.save_messages()
    └── emit SSE: done
```

### SSE event types

| Type | When | Content |
|------|------|---------|
| `conversation_id` | First event | UUID of the conversation |
| `token` | Each LLM chunk | Raw token text |
| `message_end` | Full LLM turn complete | Complete message (may contain `<execute>`) |
| `observation` | After code execution | Stdout + stderr from the kernel |
| `solution` | After all turns | Cleaned final answer (stored in history) |
| `done` | Stream complete | _(no content)_ |
| `error` | Exception | Error message string |

### Cancellation

`POST /api/chat/cancel?conversation_id=<id>` sets the
`asyncio.Event` registered for that conversation. The SSE loop checks
it between chunks and exits. The `finally` block in `async_iter_agent`
calls `gen.close()` to allow the sync generator to clean up.

---

## routers/conversations.py

Standard CRUD over `ConversationStore`. No business logic — all
persistence is delegated to the store.

---

## routers/files.py

### Path traversal guard

`_safe_path(filename, workspace_dir)` resolves the real path and
verifies it starts with `os.path.realpath(workspace_dir)`. Filename is
always `os.path.basename`-stripped first so directory components in the
upload name can't escape the workspace.

### Inline vs attachment download

`GET /api/files/{filename}?inline=1` serves with
`Content-Disposition: inline` so images and PDFs open in the browser's
built-in viewer. Without `?inline=1` the browser prompts a download.

### MIME type resolution

`mimetypes.guess_type` is tried first; `_MIME_FALLBACKS` covers
chemistry formats (`.sdf`, `.mol2`, `.pdb`, `.pdbqt`, `.smi`) and
common office formats that may be missing from the OS MIME database.

---

## routers/workspace.py

`DELETE /api/workspace` removes all files and subdirectories except
`_conversations/` (which holds the conversation JSON files).

---

## Concurrency model

```
Request A (chat) ──────────────── acquires _chat_lock ──── runs agent ──── releases lock
Request B (chat) ────────────────────────────────────────── waits ─────── acquires lock ──...
Request C (files) ──── no lock needed, instant response
```

The `asyncio.Lock` serialises chat requests at the SSE layer. The
agent's internal `threading.Lock` (`_exec_lock` in `BaseAgent`)
provides a second layer of protection inside the Python process against
the same REPL being entered twice.

---

## Testing

Tests that exercise the backend live in `tests/test_web_concurrency.py`
and `tests/test_pdf_export.py`.

The refactor moved `_extract_parts` and `_async_iter_agent` out of
`app.py` into `chat_service.py`, and `_chat_lock` into `deps.py`.
Tests import from their new locations:

```python
from CAi.web_ui.backend.chat_service import extract_parts, async_iter_agent
from CAi.web_ui.backend.deps import get_chat_lock
```
