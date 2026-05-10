"""Shared pytest fixtures and test utilities for the CAi test suite.

Key goal: make it easy to instantiate BaseAgent / A1pro without requiring
real LLM credentials, network access, or the data lake. We stub out the
LLM factory at the CAi.CAi_agent.llm module level so get_llm() returns a
scripted fake.
"""

from __future__ import annotations

import types
from typing import Any, Iterable

import pytest


# ---------------------------------------------------------------------------
# Fake LLM infrastructure
# ---------------------------------------------------------------------------


class FakeLLMResponse:
    """Minimal duck-type of an LLM response object (needs .content)."""

    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    """LLM stub that replays a scripted sequence of responses.

    Usage in tests:
        fake = FakeLLM(["Hi!", "<execute>print(1)</execute>", "<done/>"])
        # Each .invoke() returns the next canned response.

    The stub also records every message list it was invoked with, so tests
    can assert on prompt contents.
    """

    def __init__(self, responses: Iterable[str]):
        self._responses = list(responses)
        self._idx = 0
        self.calls: list[list[Any]] = []  # history of invoke() arguments

    def invoke(self, messages):
        self.calls.append(list(messages))
        if self._idx >= len(self._responses):
            # Return an <done/> fallback if the script runs out, so tests
            # don't loop forever.
            return FakeLLMResponse("<done/>")
        resp = self._responses[self._idx]
        self._idx += 1
        return FakeLLMResponse(resp)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_messages(self):
        return self.calls[-1] if self.calls else []


# ---------------------------------------------------------------------------
# Autouse fixture: replace get_llm with a factory that returns our fake
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_llm_factory(monkeypatch):
    """Returns a function you call to install a FakeLLM for the test.

    Example:
        def test_something(fake_llm_factory):
            fake = fake_llm_factory(["hello", "<done/>"])
            agent = BaseAgent(...)
            ...
            assert fake.call_count == 2
    """

    def _install(responses):
        fake = FakeLLM(responses)
        from CAi.CAi_agent import base as base_mod
        from CAi.CAi_agent import llm as llm_mod

        # Patch both: the factory module, and the already-imported name
        # inside CAi.CAi_agent.base. The latter is what BaseAgent.__init__
        # actually calls.
        monkeypatch.setattr(llm_mod, "get_llm", lambda *a, **k: fake)
        monkeypatch.setattr(base_mod, "get_llm", lambda *a, **k: fake)
        return fake

    return _install


@pytest.fixture
def base_agent(fake_llm_factory):
    """A BaseAgent backed by a FakeLLM (no responses pre-loaded)."""
    from CAi.CAi_agent.base import BaseAgent

    fake = fake_llm_factory([])

    def _make(responses=None):
        if responses is not None:
            fake._responses = list(responses)
            fake._idx = 0
        return BaseAgent(
            llm="fake",
            source="Custom",
            base_url="http://fake",
            api_key="fake",
        ), fake

    return _make


@pytest.fixture
def a1pro_agent(fake_llm_factory):
    """An A1pro with tools and skills disabled (pure prompt testing)."""
    from CAi.CAi_agent.agent import A1pro

    fake = fake_llm_factory([])

    def _make(responses=None, **kwargs):
        if responses is not None:
            fake._responses = list(responses)
            fake._idx = 0
        defaults = dict(
            llm="fake",
            source="Custom",
            base_url="http://fake",
            api_key="fake",
            auto_load_tools=False,
            auto_load_skills=False,
        )
        defaults.update(kwargs)
        return A1pro(**defaults), fake

    return _make
