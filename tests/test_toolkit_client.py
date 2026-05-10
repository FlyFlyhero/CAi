"""Tests for CAi.toolkit.client (HTTP client for the local tool server).

All HTTP calls are stubbed via monkeypatching `requests.get` / `requests.post`
on the client module so the tests never hit the network.
"""

from __future__ import annotations

import pytest

from CAi.toolkit import client as client_mod
from CAi.toolkit.client import ToolServerError, ping, run_tool


# ---------------------------------------------------------------------------
# Fake requests plumbing
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            err = requests.exceptions.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err


def _patch_requests(monkeypatch, *, get=None, post=None):
    """Attach stubs for requests.get / requests.post on the module level."""
    import requests

    if get is not None:
        monkeypatch.setattr(requests, "get", get)
    if post is not None:
        monkeypatch.setattr(requests, "post", post)


# Skip the real sleep in the polling loop — otherwise every polling test
# would wait seconds of real time.
@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# ping()
# ---------------------------------------------------------------------------


def test_ping_returns_server_payload(monkeypatch):
    def fake_get(url, **kwargs):
        assert url.endswith("/health")
        return _Resp({"status": "ok", "tools": ["scscore", "vina"]})

    _patch_requests(monkeypatch, get=fake_get)
    out = ping()
    assert out["status"] == "ok"
    assert "scscore" in out["tools"]


def test_ping_wraps_network_errors(monkeypatch):
    import requests

    def fake_get(url, **kwargs):
        raise requests.ConnectionError("connection refused")

    _patch_requests(monkeypatch, get=fake_get)
    with pytest.raises(ToolServerError) as excinfo:
        ping()
    assert "unreachable" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# run_tool — submission errors
# ---------------------------------------------------------------------------


def test_run_tool_submission_network_error(monkeypatch):
    import requests

    def fake_post(url, **kwargs):
        raise requests.ConnectionError("refused")

    _patch_requests(monkeypatch, post=fake_post)
    out = run_tool("x", {})
    assert "error" in out
    assert "cannot reach" in out["error"].lower()


def test_run_tool_submission_http_error(monkeypatch):
    def fake_post(url, **kwargs):
        return _Resp({"detail": "bad params"}, status_code=500)

    _patch_requests(monkeypatch, post=fake_post)
    out = run_tool("x", {})
    assert "error" in out
    assert "500" in out["error"]


def test_run_tool_no_job_id_returned(monkeypatch):
    def fake_post(url, **kwargs):
        return _Resp({"unexpected": "shape"})

    _patch_requests(monkeypatch, post=fake_post)
    out = run_tool("x", {})
    assert "error" in out
    assert "job_id" in out["error"]


def test_run_tool_submission_rejected(monkeypatch):
    def fake_post(url, **kwargs):
        return _Resp({"error": "tool not found"})

    _patch_requests(monkeypatch, post=fake_post)
    out = run_tool("x", {})
    assert "tool not found" in out["error"]


# ---------------------------------------------------------------------------
# run_tool — polling → finished
# ---------------------------------------------------------------------------


def test_run_tool_happy_path(monkeypatch):
    def fake_post(url, **kwargs):
        return _Resp({"job_id": "abc"})

    polls = [
        {"status": "running"},
        {"status": "running"},
        {"status": "finished", "data": {"summary": {"avg": 1.2}}},
    ]

    def fake_get(url, **kwargs):
        assert "/job/abc" in url
        return _Resp(polls.pop(0))

    _patch_requests(monkeypatch, post=fake_post, get=fake_get)
    out = run_tool("x", {})
    assert out == {"summary": {"avg": 1.2}}


def test_run_tool_failed_state(monkeypatch):
    def fake_post(url, **kwargs):
        return _Resp({"job_id": "abc"})

    def fake_get(url, **kwargs):
        return _Resp({"status": "failed", "data": "boom"})

    _patch_requests(monkeypatch, post=fake_post, get=fake_get)
    out = run_tool("x", {})
    assert "error" in out
    assert "boom" in out["error"]


def test_run_tool_unknown_state(monkeypatch):
    def fake_post(url, **kwargs):
        return _Resp({"job_id": "abc"})

    def fake_get(url, **kwargs):
        return _Resp({"status": "cosmic-ray-flipped-a-bit"})

    _patch_requests(monkeypatch, post=fake_post, get=fake_get)
    out = run_tool("x", {})
    assert "error" in out
    assert "unknown" in out["error"].lower()


def test_run_tool_polling_network_error(monkeypatch):
    def fake_post(url, **kwargs):
        return _Resp({"job_id": "abc"})

    import requests

    def fake_get(url, **kwargs):
        raise requests.ConnectionError("network flaked")

    _patch_requests(monkeypatch, post=fake_post, get=fake_get)
    out = run_tool("x", {})
    assert "Polling failed" in out["error"]


def test_run_tool_timeout(monkeypatch):
    """If the job stays 'running' past the deadline, the client returns an error."""
    import time

    def fake_post(url, **kwargs):
        return _Resp({"job_id": "abc"})

    def fake_get(url, **kwargs):
        return _Resp({"status": "running"})

    # Fake clock: jump far ahead on every time() call
    clock = iter([0.0, 0.0, 10_000.0])

    def fake_time():
        return next(clock, 20_000.0)

    monkeypatch.setattr(client_mod.time, "time", fake_time)
    _patch_requests(monkeypatch, post=fake_post, get=fake_get)

    out = run_tool("x", {}, timeout_mins=1)
    assert "Timeout" in out["error"]


# ---------------------------------------------------------------------------
# _unwrap_result edge cases
# ---------------------------------------------------------------------------


def test_run_tool_finished_with_no_data(monkeypatch):
    def fake_post(url, **kwargs):
        return _Resp({"job_id": "abc"})

    def fake_get(url, **kwargs):
        return _Resp({"status": "finished"})

    _patch_requests(monkeypatch, post=fake_post, get=fake_get)
    out = run_tool("x", {})
    assert "error" in out
    assert "no data" in out["error"].lower()


def test_run_tool_finished_with_stringified_json(monkeypatch):
    """Legacy path: older tools put stringified JSON on stdout."""

    def fake_post(url, **kwargs):
        return _Resp({"job_id": "abc"})

    def fake_get(url, **kwargs):
        return _Resp(
            {"status": "finished", "stdout": "{'summary': {'ok': 1}}"}
        )

    _patch_requests(monkeypatch, post=fake_post, get=fake_get)
    out = run_tool("x", {})
    assert out == {"summary": {"ok": 1}}


def test_run_tool_finished_explicit_failure(monkeypatch):
    """If the tool explicitly says success=False, surface it as an error."""

    def fake_post(url, **kwargs):
        return _Resp({"job_id": "abc"})

    def fake_get(url, **kwargs):
        return _Resp(
            {
                "status": "finished",
                "data": {"success": False, "error": "bad input"},
            }
        )

    _patch_requests(monkeypatch, post=fake_post, get=fake_get)
    out = run_tool("x", {})
    assert "error" in out
    assert "bad input" in out["error"]
