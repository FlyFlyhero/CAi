"""Tests for the Web UI backend — SSE parsing and chat serialisation."""

from __future__ import annotations

import asyncio
import builtins

import pytest


@pytest.fixture(autouse=True)
def _reset_custom_functions():
    yield
    if hasattr(builtins, "_base_CAi_custom_functions"):
        builtins._base_CAi_custom_functions.clear()


# ---------------------------------------------------------------------------
# _extract_parts — SSE payload builder
# ---------------------------------------------------------------------------


def test_extract_parts_plain_text():
    from CAi.web_ui.backend.app import _extract_parts

    parts = _extract_parts("Hello world")
    assert parts == {"text": "Hello world"}


def test_extract_parts_with_thinking_and_code():
    from CAi.web_ui.backend.app import _extract_parts

    content = "Let me think about this.\n<execute>print(1)</execute>"
    parts = _extract_parts(content)
    assert "thinking" in parts
    assert "Let me think about this" in parts["thinking"]
    assert parts["code"] == "print(1)"


def test_extract_parts_observation():
    from CAi.web_ui.backend.app import _extract_parts

    content = "<observation>\nresult = 42\n</observation>"
    parts = _extract_parts(content)
    assert "observation" in parts
    assert "42" in parts["observation"]


def test_extract_parts_strips_done_tag_from_text():
    from CAi.web_ui.backend.app import _extract_parts

    parts = _extract_parts("Final answer is X. <done/>")
    assert parts.get("text") == "Final answer is X."
    assert "<done/>" not in parts.get("text", "")


def test_extract_parts_multiple_execute_blocks():
    from CAi.web_ui.backend.app import _extract_parts

    content = "<execute>a=1</execute>\n<execute>b=2</execute>"
    parts = _extract_parts(content)
    assert "a=1" in parts["code"]
    assert "b=2" in parts["code"]


# ---------------------------------------------------------------------------
# Concurrency: chat lock serialises requests
# ---------------------------------------------------------------------------


async def test_chat_lock_serialises_concurrent_requests(fake_llm_factory, tmp_path):
    """Three concurrent /api/chat calls must not run LLM invocations
    in parallel — the asyncio lock serialises them."""
    import time
    from CAi.web_ui.backend import app as app_module
    from CAi.CAi_agent.agent import A1pro

    # Fake LLM that sleeps to simulate real work and tracks concurrency
    class _SlowFake:
        def __init__(self):
            self.active = 0
            self.peak = 0

        def invoke(self, messages):
            self.active += 1
            self.peak = max(self.peak, self.active)
            time.sleep(0.15)
            self.active -= 1

            class _R:
                content = "ok"

            return _R()

    slow = _SlowFake()
    from CAi.CAi_agent import base as base_mod
    from CAi.CAi_agent import llm as llm_mod

    # Patch the factory in both places
    import types

    orig_factory = fake_llm_factory  # noqa: F841 (we use monkeypatch-like override below)

    # Use a monkeypatch via pytest fixture not available here — do it manually
    prev_llm = llm_mod.get_llm
    prev_base = base_mod.get_llm
    llm_mod.get_llm = lambda *a, **k: slow
    base_mod.get_llm = lambda *a, **k: slow

    # Redirect workspace to a temp dir to avoid polluting the real one
    prev_store = app_module._store
    prev_workspace = app_module._workspace_dir
    prev_conv_dir = app_module._conversations_dir
    from CAi.web_ui.backend.conversation_store import ConversationStore

    app_module._workspace_dir = str(tmp_path / "ws")
    app_module._conversations_dir = str(tmp_path / "ws" / "_conv")
    (tmp_path / "ws" / "_conv").mkdir(parents=True, exist_ok=True)
    app_module._store = ConversationStore(app_module._conversations_dir)

    # Reset the lock for a clean test
    app_module._chat_lock = asyncio.Lock()

    try:
        agent = A1pro(
            llm="fake",
            source="Custom",
            base_url="x",
            api_key="x",
            auto_load_tools=False,
            auto_load_skills=False,
        )
        app_module.set_agent(agent)

        conv = app_module._store.create_conversation(title="concurrent_test")

        async def send(msg):
            req = app_module.ChatRequest(message=msg, conversation_id=conv["id"])
            resp = await app_module.chat(req)
            async for _chunk in resp.body_iterator:
                pass

        await asyncio.gather(send("first"), send("second"), send("third"))

        assert slow.peak == 1, f"chat lock should serialise — saw peak={slow.peak}"
    finally:
        llm_mod.get_llm = prev_llm
        base_mod.get_llm = prev_base
        app_module._store = prev_store
        app_module._workspace_dir = prev_workspace
        app_module._conversations_dir = prev_conv_dir
