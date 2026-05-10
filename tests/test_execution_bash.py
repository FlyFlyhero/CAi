"""Tests for CAi.CAi_agent.execution.bash.

On Windows there's no real bash; we skip the tests that need a shell.
"""

from __future__ import annotations

import shutil

import pytest

from CAi.CAi_agent.execution.bash import run_bash_script

has_bash = shutil.which("bash") is not None


# ---------------------------------------------------------------------------
# Error handling (does not require a shell)
# ---------------------------------------------------------------------------


def test_empty_script_returns_error():
    out = run_bash_script("")
    assert "Error" in out
    assert "Empty" in out


def test_whitespace_only_returns_error():
    out = run_bash_script("   \n  \t  ")
    assert "Error" in out


# ---------------------------------------------------------------------------
# Execution (requires a real bash)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not has_bash, reason="bash not available on this machine")
def test_echo_captured():
    out = run_bash_script("echo hello_world")
    # WSL stub on Windows might fail even when bash.exe exists — skip if so
    if "execvpe" in out or "No such file or directory" in out:
        pytest.skip("bash executable exists but can't actually run (e.g. WSL missing rootfs)")
    assert "hello_world" in out


@pytest.mark.skipif(not has_bash, reason="bash not available on this machine")
def test_multiline_script():
    out = run_bash_script(
        """
        a=1
        b=2
        echo $((a + b))
        """
    )
    if "execvpe" in out or "No such file or directory" in out:
        pytest.skip("bash executable exists but can't actually run")
    assert "3" in out


@pytest.mark.skipif(not has_bash, reason="bash not available on this machine")
def test_nonzero_exit_reports_error():
    out = run_bash_script("exit 7")
    if "execvpe" in out or "No such file or directory" in out:
        pytest.skip("bash executable exists but can't actually run")
    assert "exit code 7" in out


@pytest.mark.skipif(not has_bash, reason="bash not available on this machine")
def test_set_e_is_added():
    """Without set -e, the second command would run. With it, exit on first failure."""
    out = run_bash_script("false\necho should_not_run")
    if "execvpe" in out or "No such file or directory" in out:
        pytest.skip("bash executable exists but can't actually run")
    assert "should_not_run" not in out
