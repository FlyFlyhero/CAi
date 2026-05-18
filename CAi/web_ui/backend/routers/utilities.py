"""Utility library maintenance endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from CAi.logger import get_logger

from ..deps import get_agent

logger = get_logger("CAi.web_ui.utilities")

router = APIRouter(prefix="/api/utilities", tags=["utilities"])


class MaintainRequest(BaseModel):
    skip_cooldown: bool = False
    mode: str = "execute"  # "execute" or "preview"


@router.post("/maintain")
async def maintain(
    request: MaintainRequest,
    agent=Depends(get_agent),
):
    """Trigger utility library maintenance.

    Modes:
        - "preview": analyze session log and return proposed actions without applying
        - "execute": analyze and apply actions to the utility library
    """
    registry = getattr(agent, "utility_registry", None)
    if registry is None:
        return {"status": "disabled", "message": "Utility system not enabled"}

    # Get the most recent session log from the chat router's cache
    from .chat import _last_session_log

    session_log = _last_session_log.get("log", [])
    user_message = _last_session_log.get("user_message", "")
    if not session_log:
        return {"status": "no_data", "message": "No recent session data"}

    from CAi.CAi_agent.utilities import UtilityManager

    manager = UtilityManager(registry, llm=agent.llm)
    loop = asyncio.get_event_loop()

    if request.mode == "preview":
        actions = await loop.run_in_executor(None, manager.preview, session_log, user_message)
        return {"status": "ok", "preview": actions}
    else:
        result = await loop.run_in_executor(None, manager.maintain, session_log, user_message)
        return {"status": "ok", **result}


@router.get("/list")
async def list_utilities(agent=Depends(get_agent)):
    """List all utilities in the library."""
    registry = getattr(agent, "utility_registry", None)
    if registry is None:
        return {"utilities": []}
    return {"utilities": registry.list_meta()}


@router.get("/traces")
async def list_traces(limit: int = 20, agent=Depends(get_agent)):
    """List recent UtilityManager traces (LLM call history)."""
    registry = getattr(agent, "utility_registry", None)
    if registry is None:
        return {"traces": []}

    from CAi.CAi_agent.utilities import UtilityManager

    manager = UtilityManager(registry, llm=agent.llm)
    return {"traces": manager.list_traces(limit=limit)}


@router.get("/traces/{filename}")
async def get_trace(filename: str, agent=Depends(get_agent)):
    """Fetch one trace by filename (full prompt + response + actions)."""
    registry = getattr(agent, "utility_registry", None)
    if registry is None:
        return {"error": "utilities disabled"}

    from CAi.CAi_agent.utilities import UtilityManager

    manager = UtilityManager(registry, llm=agent.llm)
    trace = manager.get_trace(filename)
    if trace is None:
        return {"error": "trace not found"}
    return trace
