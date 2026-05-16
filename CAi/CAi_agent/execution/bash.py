"""
run_bash_script — execute a bash snippet via subprocess.

Keeps the runtime contract of the original helper:
  - runs in the current working directory (so the agent's workspace
    chdir takes effect)
  - returns captured stdout on success
  - returns 'Error running Bash script (exit code ...): ...' on failure
  - returns 'Error: ...' for anything else

Unlike the old `base_CAi.utils.run_bash_script` which relied on the
kernel honouring the `#!/bin/bash` shebang (Linux-only), we invoke
bash explicitly. That makes the helper work on Windows too, as long
as a `bash` executable is on PATH (Git Bash, WSL, MSYS2, etc.).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile


def run_bash_script(script: str) -> str:
    """Execute a bash script and return its stdout (or an error string)."""
    try:
        script = (script or "").strip()
        if not script:
            return "Error: Empty script"

        bash = shutil.which("bash")
        if bash is None:
            return (
                "Error: no 'bash' executable found on PATH. "
                "Install bash (or on Windows: Git Bash / WSL) to use #!BASH blocks."
            )

        # Prepend `set -e` if absent so scripts fail fast on errors.
        # We skip the shebang — we invoke bash explicitly below.
        if "set -e" not in script:
            script = "set -e\n" + script

        with tempfile.NamedTemporaryFile(
            suffix=".sh", mode="w", delete=False, encoding="utf-8", newline="\n"
        ) as f:
            f.write(script)
            temp_file = f.name

        try:
            proc = subprocess.run(
                [bash, temp_file],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                env=os.environ.copy(),
                cwd=os.getcwd(),
            )
        finally:
            try:
                os.unlink(temp_file)
            except OSError:
                pass

        if proc.returncode != 0:
            return (
                f"Error running Bash script (exit code {proc.returncode}):\n"
                f"{proc.stderr}"
            )
        return proc.stdout

    except Exception as e:  # noqa: BLE001
        return f"Error running Bash script: {e}"
