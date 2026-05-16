"""
Conversation persistence layer.
Stores conversations as JSON files on disk with an index for fast listing.
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from threading import Lock


class ConversationStore:
    """File-based conversation storage."""

    def __init__(self, storage_dir: str):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.storage_dir / "index.json"
        self._lock = Lock()
        self._ensure_index()

    def _ensure_index(self):
        if not self.index_path.exists():
            self._write_index([])

    def _read_index(self) -> list[dict]:
        try:
            with open(self.index_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write_index(self, data: list[dict]):
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _conv_path(self, conv_id: str) -> Path:
        return self.storage_dir / f"conv_{conv_id}.json"

    # ========== Public API ==========

    def list_conversations(self) -> list[dict]:
        """Return list of conversation metadata, sorted by updated_at desc."""
        with self._lock:
            index = self._read_index()
            return sorted(index, key=lambda c: c.get("updated_at", ""), reverse=True)

    def create_conversation(self, title: str | None = None) -> dict:
        """Create a new conversation and return its metadata."""
        with self._lock:
            conv_id = uuid.uuid4().hex[:12]
            now = datetime.now().isoformat()
            meta = {
                "id": conv_id,
                "title": title or "新对话",
                "created_at": now,
                "updated_at": now,
                "message_count": 0,
            }
            # Write empty conversation file
            with open(self._conv_path(conv_id), "w", encoding="utf-8") as f:
                json.dump({"id": conv_id, "messages": []}, f, ensure_ascii=False, indent=2)
            # Update index
            index = self._read_index()
            index.append(meta)
            self._write_index(index)
            return meta

    def get_conversation(self, conv_id: str) -> dict | None:
        """Get full conversation content (messages included)."""
        with self._lock:
            path = self._conv_path(conv_id)
            if not path.exists():
                return None
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                # Merge metadata
                index = self._read_index()
                meta = next((c for c in index if c["id"] == conv_id), {})
                return {**meta, **data}
            except (json.JSONDecodeError, FileNotFoundError):
                return None

    def save_messages(self, conv_id: str, messages: list[dict]) -> bool:
        """Overwrite the message list of a conversation. Updates index metadata."""
        with self._lock:
            path = self._conv_path(conv_id)
            if not path.exists():
                return False

            with open(path, "w", encoding="utf-8") as f:
                json.dump({"id": conv_id, "messages": messages}, f, ensure_ascii=False, indent=2)

            # Update index metadata
            index = self._read_index()
            for meta in index:
                if meta["id"] == conv_id:
                    meta["updated_at"] = datetime.now().isoformat()
                    meta["message_count"] = len(messages)
                    # Auto-generate title from first user message
                    if meta.get("title") in (None, "", "新对话") and messages:
                        first_user = next((m for m in messages if m.get("role") == "user"), None)
                        if first_user:
                            content = first_user.get("content", "").strip()
                            meta["title"] = content[:40] + ("..." if len(content) > 40 else "")
                    break
            self._write_index(index)
            return True

    def delete_conversation(self, conv_id: str) -> bool:
        with self._lock:
            path = self._conv_path(conv_id)
            if path.exists():
                path.unlink()
            index = self._read_index()
            new_index = [c for c in index if c["id"] != conv_id]
            if len(new_index) == len(index):
                return False
            self._write_index(new_index)
            return True

    def update_title(self, conv_id: str, title: str) -> bool:
        with self._lock:
            index = self._read_index()
            for meta in index:
                if meta["id"] == conv_id:
                    meta["title"] = title
                    meta["updated_at"] = datetime.now().isoformat()
                    self._write_index(index)
                    return True
            return False
