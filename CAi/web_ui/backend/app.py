"""
FastAPI backend for CAi Web UI.
Provides streaming chat, file management, conversation persistence.

Concurrency model:
    The agent is a stateless execution engine. Conversation history lives in
    ConversationStore. Each /api/chat request loads history → runs agent →
    appends results. A global lock serialises chat requests because code
    execution uses a shared REPL namespace (see BaseAgent._exec_lock).
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

app = FastAPI(title="CAi Web UI API", version="2.0.0")

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
    if ref_files:
        prompt += f"\n\n[工作目录]: {_workspace_dir}"
        for f in ref_files:
            target = os.path.join(_workspace_dir, f)
            prompt += f"\n[引用文件]: {target}"
    else:
        prompt += f"\n\n[工作目录]: {_workspace_dir}"
    return prompt


def _extract_parts(content: str) -> dict:
    """Extract structured parts from agent response."""
    parts = {}

    # Thinking (text before any tags)
    tag_positions = [
        content.find(tag)
        for tag in ["<execute>", "<observation>", "<done"]
        if tag in content
    ]
    if tag_positions:
        thinking = content[: min(tag_positions)].strip()
        if thinking:
            parts["thinking"] = thinking

    # Code blocks
    code_blocks = re.findall(r"<execute>(.*?)</execute>", content, re.DOTALL)
    if code_blocks:
        parts["code"] = "\n\n".join(b.strip() for b in code_blocks)

    # Observations
    obs_blocks = re.findall(r"<observation>(.*?)</observation>", content, re.DOTALL)
    if obs_blocks:
        parts["observation"] = "\n\n".join(b.strip() for b in obs_blocks)

    # If no tags at all, the whole thing is text
    if not parts:
        cleaned = re.sub(r"<done\s*/?>", "", content).strip()
        if cleaned:
            parts["text"] = cleaned

    return parts


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
    """Stream chat response using SSE."""
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    # Ensure conversation
    conv_id = request.conversation_id
    if not conv_id:
        meta = _store.create_conversation()
        conv_id = meta["id"]

    async def event_stream():
        # Acquire the chat lock for the entire stream — concurrent chats
        # would otherwise interleave in the shared REPL namespace.
        async with _chat_lock:
            try:
                # Send conversation ID
                yield f"data: {json.dumps({'type': 'conversation_id', 'content': conv_id})}\n\n"

                # Load history
                conv = _store.get_conversation(conv_id)
                history = []
                if conv and conv.get("messages"):
                    for m in conv["messages"]:
                        if m.get("role") in ("user", "assistant"):
                            history.append({"role": m["role"], "content": m["content"]})

                # Build prompt
                agent_prompt = _build_prompt(request.message, request.file_refs)

                # Stream from agent (stateless — history passed explicitly)
                final_content = ""
                for step in _agent.run_with_history(agent_prompt, history):
                    content = step["content"]

                    # Parse and emit parts
                    parts = _extract_parts(content)

                    if "thinking" in parts:
                        yield f"data: {json.dumps({'type': 'thinking', 'content': parts['thinking']}, ensure_ascii=False)}\n\n"
                    if "code" in parts:
                        yield f"data: {json.dumps({'type': 'code', 'content': parts['code']}, ensure_ascii=False)}\n\n"
                    if "observation" in parts:
                        yield f"data: {json.dumps({'type': 'observation', 'content': parts['observation']}, ensure_ascii=False)}\n\n"
                    if "text" in parts:
                        yield f"data: {json.dumps({'type': 'text', 'content': parts['text']}, ensure_ascii=False)}\n\n"

                    final_content = content

                # Build the stored answer by cleaning internal tags
                stored_answer = re.sub(r"<execute>.*?</execute>", "", final_content, flags=re.DOTALL)
                stored_answer = re.sub(r"<observation>.*?</observation>", "", stored_answer, flags=re.DOTALL)
                stored_answer = re.sub(r"<done\s*/?>", "", stored_answer)
                stored_answer = re.sub(r"\n{3,}", "\n\n", stored_answer).strip()

                if not stored_answer:
                    stored_answer = final_content  # Fallback

                # Send final solution
                yield f"data: {json.dumps({'type': 'solution', 'content': stored_answer}, ensure_ascii=False)}\n\n"

                # Persist
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

    import mimetypes
    media_type = mimetypes.guess_type(fp)[0] or "application/octet-stream"

    if inline:
        from starlette.responses import Response
        with open(fp, "rb") as f:
            content = f.read()
        return Response(content=content, media_type=media_type,
                       headers={"Content-Disposition": f'inline; filename="{filename}"'})

    return FileResponse(fp, filename=filename, media_type=media_type)


@app.delete("/api/files/{filename}")
async def delete_file(filename: str):
    fp = os.path.join(_workspace_dir, filename)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(fp)
    return {"deleted": filename}


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
