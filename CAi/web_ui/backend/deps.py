"""
Shared application state and FastAPI dependency providers.

All mutable singletons live here so routers can import them via Depends()
instead of referencing module-level globals scattered across app.py.
"""

from __future__ import annotations

import asyncio
import os

from CAi.config import WORKSPACE_DIR
from CAi.logger import get_logger

from .conversation_store import ConversationStore

logger = get_logger("CAi.web_ui.deps")

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_agent = None

_workspace_dir: str = str((WORKSPACE_DIR / "agent_workspace").resolve())
_conversations_dir: str = str(
    (WORKSPACE_DIR / "agent_workspace" / "_conversations").resolve()
)
os.makedirs(_workspace_dir, exist_ok=True)

_store = ConversationStore(_conversations_dir)

# Serialise chat requests — the agent's REPL is process-global.
_chat_lock = asyncio.Lock()

# Per-conversation cancellation signals.
_cancel_events: dict[str, asyncio.Event] = {}


# ---------------------------------------------------------------------------
# Initialisation (called by launch.py via app.set_agent)
# ---------------------------------------------------------------------------


def set_agent(agent) -> None:
    """Register the agent and tell the REPL kernel where to save plots."""
    global _agent
    from CAi.CAi_agent.execution.repl import set_workspace_dir as _set_repl_workspace

    _agent = agent
    _set_repl_workspace(_workspace_dir)
    logger.debug("Agent registered; workspace=%s", _workspace_dir)


# ---------------------------------------------------------------------------
# Dependency providers
# ---------------------------------------------------------------------------


def get_agent():
    """Return the live agent; raise 503 if not yet initialised."""
    from fastapi import HTTPException

    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return _agent


def get_agent_optional():
    """Return the agent or None — never raises (used by /health)."""
    return _agent


def get_store() -> ConversationStore:
    return _store


def get_workspace_dir() -> str:
    return _workspace_dir


def get_chat_lock() -> asyncio.Lock:
    return _chat_lock


def get_cancel_events() -> dict[str, asyncio.Event]:
    return _cancel_events
