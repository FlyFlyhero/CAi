"""Session management — load, save, and manage conversation state."""

from __future__ import annotations

from typing import Any


def load_conversation(store: Any, conv_id: str) -> tuple[list[dict], list[dict]] | None:
    """
    Load a conversation from the store.

    Returns (model_history, display_messages) or None if not found.
    model_history contains clean text for the LLM context.
    display_messages contains the full records for UI rendering.
    """
    conv = store.get_conversation(conv_id)
    if not conv:
        return None

    from CAi.web_ui.backend.chat_service import clean_stored_answer

    model_history: list[dict] = []
    display_messages: list[dict] = list(conv.get("messages", []))

    for m in display_messages:
        role = m.get("role")
        if role in ("user", "assistant"):
            clean = clean_stored_answer(m.get("content", "")) or m.get("content", "")
            model_history.append({"role": role, "content": clean})

    return model_history, display_messages


def new_conversation(store: Any) -> tuple[str, list[dict], list[dict]]:
    """Create a new conversation and return (conv_id, model_history, display_messages)."""
    meta = store.create_conversation()
    return meta["id"], [], []


def append_turn(
    model_history: list[dict],
    display_messages: list[dict],
    user_input: str,
    assistant_content: str,
    clean_content: str,
    *,
    interrupted: bool = False,
    interrupted_at_char: int | None = None,
) -> None:
    """Append a completed user/assistant turn to both history lists in-place."""
    from datetime import datetime

    model_history.append({"role": "user", "content": user_input})
    model_history.append({"role": "assistant", "content": clean_content})

    display_messages.append({
        "role": "user",
        "content": user_input,
        "timestamp": datetime.now().isoformat(),
    })

    assistant_record: dict = {
        "role": "assistant",
        "content": assistant_content,
        "timestamp": datetime.now().isoformat(),
    }
    if interrupted:
        assistant_record["interrupted"] = True
        if interrupted_at_char is not None:
            assistant_record["interrupted_at_char"] = interrupted_at_char

    display_messages.append(assistant_record)
