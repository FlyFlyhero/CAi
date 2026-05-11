"""
FastAPI backend for CAi Web UI.
Provides streaming chat, file management, conversation persistence.

Concurrency model:
    The agent is a stateless execution engine. Conversation history lives in
    ConversationStore. Each /api/chat request loads history → runs agent →
    appends results. A global lock serialises chat requests because code
    execution uses a shared REPL namespace (see BaseAgent._exec_lock).

Streaming model:
    The agent's run_with_history_streaming() yields token-level events
    from the LLM plus observation events after each code execution.
    We forward each event as an SSE message so the browser can render
    incrementally instead of waiting for the whole turn to finish.
"""

import asyncio
import json
import os
import re
import shutil
from datetime import datetime

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from CAi.config import WORKSPACE_DIR
from CAi.logger import get_logger

from .conversation_store import ConversationStore

logger = get_logger("CAi.web_ui")

app = FastAPI(title="CAi Web UI API", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== Globals ==========

_agent = None
_workspace_dir = str((WORKSPACE_DIR / "agent_workspace").resolve())
_conversations_dir = str((WORKSPACE_DIR / "agent_workspace" / "_conversations").resolve())
os.makedirs(_workspace_dir, exist_ok=True)

_store = ConversationStore(_conversations_dir)

# Serialise chat requests — the agent's REPL is process-global.
_chat_lock = asyncio.Lock()

# Per-conversation cancellation signals. When the user hits "stop" on a
# running chat, we set the event; the streaming loop checks it between
# chunks and exits cleanly.
_cancel_events: dict[str, asyncio.Event] = {}


def set_agent(agent):
    """Set the agent instance from the launcher."""
    global _agent
    _agent = agent


# ========== Models ==========


class ChatRequest(BaseModel):
    message: str
    file_refs: list[str] = []
    conversation_id: str | None = None


class CreateConversationRequest(BaseModel):
    title: str | None = None


class UpdateTitleRequest(BaseModel):
    title: str


# ========== Helpers ==========


def _build_prompt(text: str, ref_files: list[str]) -> str:
    """Build the prompt sent to the agent."""
    prompt = text
    prompt += f"\n\n[工作目录]: {_workspace_dir}"
    for f in ref_files:
        target = os.path.join(_workspace_dir, f)
        prompt += f"\n[引用文件]: {target}"
    return prompt


def _extract_parts(content: str) -> dict:
    """Split a complete AI message into thinking / code / observation / text.

    Used when re-hydrating a conversation from storage and for the
    non-streaming fallback in tests. The streaming path does its own
    incremental parsing in the frontend.
    """
    parts = {}

    has_execute = "<execute>" in content
    has_observation = "<observation>" in content

    if has_execute or has_observation:
        tag_positions = [
            content.find(tag)
            for tag in ("<execute>", "<observation>")
            if tag in content
        ]
        if tag_positions:
            thinking = content[: min(tag_positions)].strip()
            if thinking:
                parts["thinking"] = thinking

    code_blocks = re.findall(r"<execute>(.*?)</execute>", content, re.DOTALL)
    if code_blocks:
        parts["code"] = "\n\n".join(b.strip() for b in code_blocks)

    obs_blocks = re.findall(r"<observation>(.*?)</observation>", content, re.DOTALL)
    if obs_blocks:
        parts["observation"] = "\n\n".join(b.strip() for b in obs_blocks)

    if "code" not in parts and "observation" not in parts:
        cleaned = re.sub(r"<done\s*/?>", "", content).strip()
        if cleaned:
            parts["text"] = cleaned

    return parts


async def _async_iter_agent(prompt: str, history: list[dict], cancel_event: asyncio.Event | None = None):
    """Adapt the synchronous streaming generator into an async generator.

    Each call to next(gen) blocks on LLM token fetch / code execution.
    We run it in a thread so the event loop stays free to flush SSE.

    StopIteration cannot cross an asyncio Future boundary — if we let
    `next()` raise it inside run_in_executor, asyncio raises the cryptic
    "TypeError: StopIteration interacts badly with generators" and the
    stream hangs. So we catch StopIteration on the worker side and
    return a sentinel instead.

    If `cancel_event` is set between chunks, the iteration stops and the
    generator is closed. Any still-running step finishes silently in the
    background (Python threads can't be forcibly killed — but the next
    yielded chunk is simply discarded).
    """
    _DONE = object()
    gen = _agent.run_with_history_streaming(prompt, history)
    loop = asyncio.get_event_loop()

    def _next_or_sentinel():
        try:
            return next(gen)
        except StopIteration:
            return _DONE

    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                break
            step = await loop.run_in_executor(None, _next_or_sentinel)
            if step is _DONE:
                break
            yield step
    finally:
        gen.close()


def _clean_stored_answer(full_content: str) -> str:
    """Strip internal tags so the stored conversation reads as plain text."""
    text = re.sub(r"<execute>.*?</execute>", "", full_content, flags=re.DOTALL)
    text = re.sub(r"<observation>.*?</observation>", "", text, flags=re.DOTALL)
    text = re.sub(r"<done\s*/?>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ========== Conversation Endpoints ==========


@app.get("/api/conversations")
async def list_conversations():
    return {"conversations": _store.list_conversations()}


@app.post("/api/conversations")
async def create_conversation(request: CreateConversationRequest):
    return _store.create_conversation(title=request.title)


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    conv = _store.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    if not _store.delete_conversation(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"deleted": conv_id}


@app.patch("/api/conversations/{conv_id}/title")
async def update_title(conv_id: str, request: UpdateTitleRequest):
    if not _store.update_title(conv_id, request.title):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "ok"}


# ========== Chat Endpoint ==========


@app.get("/api/health")
async def health():
    return {"status": "ok", "agent_loaded": _agent is not None}


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """SSE stream of agent events.

    Event types sent to the frontend:
        conversation_id  — conversation UUID (first event)
        token            — one LLM token/chunk as it arrives
        message_end      — full message complete (may contain <execute>)
        observation      — code execution output, wrapped in <observation>
        solution         — final cleaned answer (persisted to history)
        done             — stream complete
        error            — exception message
    """
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    conv_id = request.conversation_id
    if not conv_id:
        meta = _store.create_conversation()
        conv_id = meta["id"]

    async def event_stream():
        async with _chat_lock:
            try:
                yield f"data: {json.dumps({'type': 'conversation_id', 'content': conv_id})}\n\n"

                # Load history
                conv = _store.get_conversation(conv_id)
                history = []
                if conv and conv.get("messages"):
                    for m in conv["messages"]:
                        if m.get("role") in ("user", "assistant"):
                            history.append({"role": m["role"], "content": m["content"]})

                agent_prompt = _build_prompt(request.message, request.file_refs)

                # Accumulate the full final response (the last "message_end"
                # before the loop exits) — this goes into conversation storage.
                last_full_message = ""

                # Register cancel event for this conversation
                cancel_ev = asyncio.Event()
                _cancel_events[conv_id] = cancel_ev

                async for step in _async_iter_agent(agent_prompt, history, cancel_ev):
                    ev_type = step.get("type")
                    ev_content = step.get("content", "")

                    if ev_type == "token":
                        # Forward raw token; the frontend assembles and parses.
                        yield f"data: {json.dumps({'type': 'token', 'content': ev_content}, ensure_ascii=False)}\n\n"
                    elif ev_type == "message_end":
                        last_full_message = ev_content
                        yield f"data: {json.dumps({'type': 'message_end', 'content': ev_content}, ensure_ascii=False)}\n\n"
                    elif ev_type == "observation":
                        yield f"data: {json.dumps({'type': 'observation', 'content': ev_content}, ensure_ascii=False)}\n\n"

                # Clean up cancel event
                _cancel_events.pop(conv_id, None)

                # Final cleaned answer for storage + a "solution" event.
                stored_answer = _clean_stored_answer(last_full_message) or last_full_message
                yield f"data: {json.dumps({'type': 'solution', 'content': stored_answer}, ensure_ascii=False)}\n\n"

                # Persist conversation
                display_message = request.message
                if request.file_refs:
                    display_message += f"\n\n📎 引用: {', '.join(request.file_refs)}"

                stored_messages = conv.get("messages", []) if conv else []
                stored_messages.append({
                    "role": "user",
                    "content": display_message,
                    "timestamp": datetime.now().isoformat(),
                })
                stored_messages.append({
                    "role": "assistant",
                    "content": stored_answer,
                    "timestamp": datetime.now().isoformat(),
                })
                _store.save_messages(conv_id, stored_messages)

                yield f"data: {json.dumps({'type': 'done'})}\n\n"

            except Exception as e:
                logger.exception("Chat stream error")
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ========== File Endpoints ==========


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    uploaded = []
    for file in files:
        target = os.path.join(_workspace_dir, file.filename)
        with open(target, "wb") as f:
            content = await file.read()
            f.write(content)
        uploaded.append(file.filename)
    return {"uploaded": uploaded}


@app.get("/api/files")
async def list_files():
    if not os.path.exists(_workspace_dir):
        return {"files": []}
    files = []
    for f in os.listdir(_workspace_dir):
        fp = os.path.join(_workspace_dir, f)
        if os.path.isfile(fp):
            stat = os.stat(fp)
            files.append({
                "name": f,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return {"files": files}


@app.get("/api/files/{filename}")
async def download_file(filename: str, inline: int = 0):
    fp = os.path.join(_workspace_dir, filename)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="File not found")

    media_type = _guess_media_type(filename)

    if inline:
        from starlette.responses import Response
        with open(fp, "rb") as f:
            content = f.read()
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    return FileResponse(fp, filename=filename, media_type=media_type)


# Fallback MIME types for common file extensions that mimetypes.guess_type
# may not know on Windows (registry-dependent).
_MIME_FALLBACKS = {
    ".pdf": "application/pdf",
    ".json": "application/json",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".smi": "chemical/x-daylight-smiles",
    ".sdf": "chemical/x-mdl-sdfile",
    ".mol": "chemical/x-mdl-molfile",
    ".mol2": "chemical/x-mol2",
    ".pdb": "chemical/x-pdb",
    ".pdbqt": "chemical/x-pdbqt",
}


def _guess_media_type(filename: str) -> str:
    import mimetypes

    mt = mimetypes.guess_type(filename)[0]
    if mt:
        return mt
    ext = os.path.splitext(filename)[1].lower()
    return _MIME_FALLBACKS.get(ext, "application/octet-stream")


@app.delete("/api/files/{filename}")
async def delete_file(filename: str):
    fp = os.path.join(_workspace_dir, filename)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(fp)
    return {"deleted": filename}


# ========== PDF export ==========


@app.post("/api/export-pdf")
async def export_pdf(conversation_id: str | None = None):
    from .pdf_export import (
        EmptyConversation,
        PdfEngineUnavailable,
        export_conversation_to_pdf,
    )

    if conversation_id:
        conv = _store.get_conversation(conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        recent = _store.list_conversations()
        if not recent:
            raise HTTPException(status_code=404, detail="No conversations to export")
        conv = _store.get_conversation(recent[0]["id"])
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = re.sub(r"[^\w\-]+", "_", conv.get("title") or "conversation").strip("_")
    filename = f"{safe_title}_{ts}.pdf" if safe_title else f"conversation_{ts}.pdf"
    out_path = os.path.join(_workspace_dir, filename)

    try:
        export_conversation_to_pdf(conv, out_path)
    except EmptyConversation as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PdfEngineUnavailable as e:
        logger.error("PDF engine unavailable: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("PDF export failed")
        raise HTTPException(status_code=500, detail=f"Export failed: {e}") from e

    return FileResponse(out_path, filename=filename, media_type="application/pdf")


# ========== Workspace ==========


@app.delete("/api/workspace")
async def clear_workspace():
    if os.path.exists(_workspace_dir):
        for item in os.listdir(_workspace_dir):
            if item == "_conversations":
                continue
            p = os.path.join(_workspace_dir, item)
            if os.path.isfile(p):
                os.remove(p)
            elif os.path.isdir(p):
                shutil.rmtree(p)
    os.makedirs(_workspace_dir, exist_ok=True)
    return {"status": "cleared"}


@app.post("/api/reset")
async def reset_all():
    return {"status": "reset"}

@app.post("/api/chat/cancel")
async def cancel_chat(conversation_id: str | None = None):
    """Signal a running chat stream to stop.

    The streaming loop checks the cancel event between chunks and exits
    cleanly. The already-generated content up to that point is still
    persisted to the conversation.
    """
    cid = conversation_id or "current"
    ev = _cancel_events.get(cid)
    if ev:
        ev.set()
        return {"status": "cancelled", "conversation_id": cid}
    return {"status": "no_active_stream", "conversation_id": cid}
