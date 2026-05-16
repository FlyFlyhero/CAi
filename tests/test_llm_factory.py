"""Tests for CAi.CAi_agent.llm.get_llm.

Goals:
  - Verify source auto-detection from model name / base_url / env.
  - Verify each provider branch calls the right langchain class with the
    right kwargs (without actually needing network access or API keys).
  - Verify clear errors when something is missing (unknown source,
    no API key, Custom without base_url).

Strategy: each provider branch lazy-imports its langchain class. We
monkeypatch those classes with a recording stub, so the tests don't
need real credentials and don't touch langchain internals.
"""

from __future__ import annotations

import types

import pytest

from CAi.CAi_agent import llm as llm_mod
from CAi.CAi_agent.llm import ALLOWED_SOURCES, DEEPSEEK_BASE_URL, get_llm


# ---------------------------------------------------------------------------
# Recording stub for langchain classes
# ---------------------------------------------------------------------------


class _RecordingChat:
    """Records the kwargs it was constructed with."""

    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        _RecordingChat.last_kwargs = dict(kwargs)

    # BaseChatModel-ish surface — tests only inspect the recorded kwargs.


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure provider env vars start unset so tests control them explicitly."""
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "LLM_SOURCE",
    ):
        monkeypatch.delenv(var, raising=False)
    _RecordingChat.last_kwargs = {}


def _patch_openai(monkeypatch):
    """Make langchain_openai.ChatOpenAI a recording stub."""
    fake_module = types.ModuleType("langchain_openai")
    fake_module.ChatOpenAI = _RecordingChat
    monkeypatch.setitem(__import__("sys").modules, "langchain_openai", fake_module)


def _patch_anthropic(monkeypatch):
    fake_module = types.ModuleType("langchain_anthropic")
    fake_module.ChatAnthropic = _RecordingChat
    monkeypatch.setitem(__import__("sys").modules, "langchain_anthropic", fake_module)


# ---------------------------------------------------------------------------
# Source auto-detection
# ---------------------------------------------------------------------------


class TestDetectSource:
    def test_claude_is_anthropic(self):
        assert llm_mod._detect_source("claude-sonnet-4-5", None) == "Anthropic"

    def test_gpt_is_openai(self):
        assert llm_mod._detect_source("gpt-4o-mini", None) == "OpenAI"

    def test_o1_is_openai(self):
        assert llm_mod._detect_source("o1-preview", None) == "OpenAI"

    def test_o3_is_openai(self):
        assert llm_mod._detect_source("o3-mini", None) == "OpenAI"

    def test_deepseek_is_deepseek(self):
        assert llm_mod._detect_source("deepseek-chat", None) == "DeepSeek"
        assert llm_mod._detect_source("deepseek-reasoner", None) == "DeepSeek"

    def test_base_url_triggers_custom(self):
        assert llm_mod._detect_source("qwen2.5-72b", "http://x/v1") == "Custom"

    def test_unknown_without_base_url_raises(self):
        with pytest.raises(ValueError):
            llm_mod._detect_source("qwen2.5-72b", None)

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("LLM_SOURCE", "Custom")
        # Even a claude- name gets overridden
        assert llm_mod._detect_source("claude-sonnet", "http://x") == "Custom"

    def test_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("LLM_SOURCE", "NotAProvider")
        assert llm_mod._detect_source("gpt-4o", None) == "OpenAI"


# ---------------------------------------------------------------------------
# OpenAI branch
# ---------------------------------------------------------------------------


class TestOpenAI:
    def test_requires_api_key(self, monkeypatch):
        _patch_openai(monkeypatch)
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            get_llm("gpt-4o-mini")

    def test_uses_env_api_key(self, monkeypatch):
        _patch_openai(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        get_llm("gpt-4o-mini")
        assert _RecordingChat.last_kwargs["api_key"] == "sk-env"
        assert _RecordingChat.last_kwargs["model"] == "gpt-4o-mini"

    def test_explicit_api_key_wins(self, monkeypatch):
        _patch_openai(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        get_llm("gpt-4o-mini", api_key="sk-explicit")
        assert _RecordingChat.last_kwargs["api_key"] == "sk-explicit"

    def test_temperature_default(self, monkeypatch):
        _patch_openai(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk")
        get_llm("gpt-4o-mini")
        assert _RecordingChat.last_kwargs["temperature"] == 0.7

    def test_stop_sequences_forwarded(self, monkeypatch):
        _patch_openai(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk")
        get_llm("gpt-4o-mini", stop_sequences=["</execute>"])
        assert _RecordingChat.last_kwargs["stop_sequences"] == ["</execute>"]


# ---------------------------------------------------------------------------
# Anthropic branch
# ---------------------------------------------------------------------------


class TestAnthropic:
    def test_requires_api_key(self, monkeypatch):
        _patch_anthropic(monkeypatch)
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            get_llm("claude-sonnet-4-5")

    def test_uses_env_api_key(self, monkeypatch):
        _patch_anthropic(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        get_llm("claude-sonnet-4-5")
        assert _RecordingChat.last_kwargs["api_key"] == "sk-ant"
        assert _RecordingChat.last_kwargs["model"] == "claude-sonnet-4-5"

    def test_max_tokens_set(self, monkeypatch):
        _patch_anthropic(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        get_llm("claude-sonnet-4-5")
        assert _RecordingChat.last_kwargs["max_tokens"] == 8192


# ---------------------------------------------------------------------------
# DeepSeek branch
# ---------------------------------------------------------------------------


class TestDeepSeek:
    def test_requires_api_key(self, monkeypatch):
        _patch_openai(monkeypatch)
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            get_llm("deepseek-chat")

    def test_uses_official_base_url(self, monkeypatch):
        _patch_openai(monkeypatch)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        get_llm("deepseek-chat")
        assert _RecordingChat.last_kwargs["base_url"] == DEEPSEEK_BASE_URL
        assert _RecordingChat.last_kwargs["api_key"] == "sk-ds"

    def test_reads_deepseek_env_only(self, monkeypatch):
        """Setting OPENAI_API_KEY alone is not enough for deepseek-*."""
        _patch_openai(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            get_llm("deepseek-reasoner")


# ---------------------------------------------------------------------------
# Custom branch
# ---------------------------------------------------------------------------


class TestCustom:
    def test_requires_base_url(self, monkeypatch):
        _patch_openai(monkeypatch)
        with pytest.raises(ValueError, match="base_url"):
            get_llm("qwen2.5-72b", source="Custom", api_key="k")

    def test_accepts_empty_api_key(self, monkeypatch):
        """Local servers (SGLang / vLLM) usually don't care about auth."""
        _patch_openai(monkeypatch)
        get_llm("qwen2.5-72b", source="Custom", base_url="http://localhost:8000/v1")
        assert _RecordingChat.last_kwargs["api_key"] == "EMPTY"
        assert _RecordingChat.last_kwargs["base_url"] == "http://localhost:8000/v1"

    def test_forwards_model_and_base_url(self, monkeypatch):
        _patch_openai(monkeypatch)
        get_llm(
            "my-model",
            source="Custom",
            base_url="http://x/v1",
            api_key="my-key",
        )
        assert _RecordingChat.last_kwargs["model"] == "my-model"
        assert _RecordingChat.last_kwargs["base_url"] == "http://x/v1"
        assert _RecordingChat.last_kwargs["api_key"] == "my-key"


# ---------------------------------------------------------------------------
# Top-level error cases
# ---------------------------------------------------------------------------


def test_missing_model_raises():
    with pytest.raises(ValueError, match="model must be specified"):
        get_llm(None)


def test_unknown_source_raises(monkeypatch):
    _patch_openai(monkeypatch)
    with pytest.raises(ValueError, match="Unknown LLM source"):
        get_llm("gpt-4o", source="Palantir")  # type: ignore[arg-type]


def test_allowed_sources_are_four():
    """Regression: if you added a provider, update this test."""
    assert ALLOWED_SOURCES == {"OpenAI", "Anthropic", "DeepSeek", "Custom"}
