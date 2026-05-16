"""Chat streaming endpoint and cancel."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from CAi.logger import get_logger

from ..chat_service import async_iter_agent, build_prompt, clean_stored_answer
from ..conversation_store import ConversationStore
from ..deps import (
    get_agent,
    get_agent_optional,
    get_cancel_events,
    get_chat_lock,
    get_store,
    get_workspace_dir,
)

logger = get_logger("CAi.web_ui.chat")

router = APIRouter(prefix="/api", tags=["chat"])

# Cache the most recent session log so the utilities router can access it.
_last_session_log: dict = {"log": []}


# ---------------------------------------------------------------------------
# Utility maintenance helper
# ---------------------------------------------------------------------------


async def _trigger_maintenance(agent, raw_session_log: list[dict]) -> None:
    """Flush utility usage stats and conditionally run UtilityManager.

    Runs as a fire-and-forget background task after the SSE stream completes.
    Never raises — all errors are logged and swallowed.
    """
    try:
        registry = getattr(agent, "utility_registry", None)
        if registry is None:
            return

        # 1. Flush usage stats and persist to disk
        from CAi.CAi_agent.execution.repl import flush_utility_usage

        usage = flush_utility_usage()
        if usage:
            registry.apply_usage(usage)

        # 2. Trigger UtilityManager only if there were observations (code executions)
        has_executions = any(s.get("type") == "observation" for s in raw_session_log)
        if not has_executions:
            return

        from CAi.CAi_agent.utilities import UtilityManager

        manager = UtilityManager(registry, llm=agent.llm)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, manager.maintain, raw_session_log)
    except Exception as e:
        logger.warning("Utility maintenance failed: %s", e)


async def _flush_usage_only(agent) -> None:
    """Flush utility usage stats without triggering LLM maintenance.

    The actual maintenance is now user-initiated via the frontend popup.
    """
    try:
        registry = getattr(agent, "utility_registry", None)
        if registry is None:
            return

        from CAi.CAi_agent.execution.repl import flush_utility_usage

        usage = flush_utility_usage()
        if usage:
            registry.apply_usage(usage)
    except Exception as e:
        logger.warning("Utility usage flush failed: %s", e)


class ChatRequest(BaseModel):
    message: str
    file_refs: list[str] = []
    conversation_id: str | None = None


@router.get("/health")
async def health(agent=Depends(get_agent_optional)):
    return {"status": "ok", "agent_loaded": agent is not None}


@router.post("/chat")
async def chat(
    request: ChatRequest,
    agent=Depends(get_agent),
    store: ConversationStore = Depends(get_store),
    workspace_dir: str = Depends(get_workspace_dir),
    chat_lock: asyncio.Lock = Depends(get_chat_lock),
    cancel_events: dict = Depends(get_cancel_events),
):
    """SSE stream of agent events.

    Event types sent to the frontend:
        conversation_id  — conversation UUID (first event)
        token            — one LLM token/chunk as it arrives
        message_end      — full message complete (may contain <execute>)
        observation      — code execution output
        solution         — final cleaned answer (persisted to history)
        done             — stream complete
        error            — exception message
    """
    conv_id = request.conversation_id
    if not conv_id:
        meta = store.create_conversation()
        conv_id = meta["id"]

    async def event_stream():
        async with chat_lock:
            # Accumulate raw session log for utility maintenance.
            raw_session_log: list[dict] = []

            try:
                yield f"data: {json.dumps({'type': 'conversation_id', 'content': conv_id})}\n\n"

                conv = store.get_conversation(conv_id)
                history = []
                if conv and conv.get("messages"):
                    for m in conv["messages"]:
                        if m.get("role") in ("user", "assistant"):
                            history.append({"role": m["role"], "content": m["content"]})

                agent_prompt = build_prompt(request.message, request.file_refs, workspace_dir)
                last_full_message = ""

                cancel_ev = asyncio.Event()
                cancel_events[conv_id] = cancel_ev

                try:
                    async for step in async_iter_agent(agent, agent_prompt, history, cancel_ev):
                        ev_type = step.get("type")
                        ev_content = step.get("content", "")

                        if ev_type == "token":
                            yield f"data: {json.dumps({'type': 'token', 'content': ev_content}, ensure_ascii=False)}\n\n"
                        elif ev_type == "message_end":
                            last_full_message = ev_content
                            raw_session_log.append({"type": "message_end", "content": ev_content})
                            yield f"data: {json.dumps({'type': 'message_end', 'content': ev_content}, ensure_ascii=False)}\n\n"
                        elif ev_type == "observation":
                            raw_session_log.append({"type": "observation", "content": ev_content})
                            yield f"data: {json.dumps({'type': 'observation', 'content': ev_content}, ensure_ascii=False)}\n\n"
                finally:
                    cancel_events.pop(conv_id, None)

                stored_answer = clean_stored_answer(last_full_message) or last_full_message
                yield f"data: {json.dumps({'type': 'solution', 'content': stored_answer}, ensure_ascii=False)}\n\n"

                display_message = request.message
                if request.file_refs:
                    display_message += f"\n\n📎 引用: {', '.join(request.file_refs)}"

                stored_messages = conv.get("messages", []) if conv else []
                stored_messages.append(
                    {
                        "role": "user",
                        "content": display_message,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
                stored_messages.append(
                    {
                        "role": "assistant",
                        "content": stored_answer,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
                store.save_messages(conv_id, stored_messages)

                # Notify frontend if there were code executions (maintenance candidate).
                # Must be sent BEFORE "done" because frontend stops reading after "done".
                has_executions = any(s.get("type") == "observation" for s in raw_session_log)
                if has_executions:
                    yield f"data: {json.dumps({'type': 'maintenance_pending'})}\n\n"
                    # Cache session log for the utilities router to access.
                    _last_session_log["log"] = raw_session_log

                yield f"data: {json.dumps({'type': 'done'})}\n\n"

                # Fire-and-forget: flush utility usage stats (no LLM call here).
                asyncio.ensure_future(_flush_usage_only(agent))

            except Exception as e:
                logger.exception("Chat stream error")
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/chat/cancel")
async def cancel_chat(
    conversation_id: str | None = None,
    cancel_events: dict = Depends(get_cancel_events),
):
    """Signal a running chat stream to stop."""
    if not conversation_id:
        return {"status": "no_conversation_id"}
    ev = cancel_events.get(conversation_id)
    if ev:
        ev.set()
        return {"status": "cancelled", "conversation_id": conversation_id}
    return {"status": "no_active_stream", "conversation_id": conversation_id}
