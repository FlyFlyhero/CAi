"""
Persistent Python REPL backed by a Jupyter kernel subprocess.

Replaces the previous exec()-based implementation. Key improvements:

  - True process isolation: the kernel runs in a separate process and can
    be interrupted via SIGINT or killed via SIGKILL on timeout — Python
    threads cannot be forcibly terminated, processes can.

  - Thread-safe output capture: stdout/stderr come back as ZeroMQ messages
    over the Jupyter wire protocol, not via sys.stdout replacement.

  - Real timeout enforcement: run_python_repl() sends an interrupt to the
    kernel when the deadline elapses, then restarts if unresponsive.

Public interface is identical to the old module so callers (base.py,
ReplBridge, tests) need no changes beyond the new `timeout` parameter on
run_python_repl().
"""

from __future__ import annotations

import atexit
import builtins
import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from datetime import datetime

logger = logging.getLogger("CAi.execution.repl")

# ---------------------------------------------------------------------------
# Module-level utility monitoring state
# ---------------------------------------------------------------------------

# Session-level utility usage accumulator (parent-side).
_session_usage: dict[str, dict] = {}

# Whether utilities have been injected with monitoring wrappers.
_utilities_injected: bool = False

# Cache of injected utility names (for re-injection on kernel restart).
_injected_utility_names: list[str] = []


def _accumulate_utility_usage(kernel_usage: dict) -> None:
    """Merge kernel-reported usage into parent-side accumulator."""
    global _session_usage
    for name, stats in kernel_usage.items():
        entry = _session_usage.setdefault(name, {"calls": 0, "errors": 0})
        entry["calls"] += stats.get("calls", 0)
        entry["errors"] += stats.get("errors", 0)


def flush_utility_usage() -> dict[str, dict]:
    """Return accumulated usage and reset. Called at session end."""
    global _session_usage
    result = _session_usage
    _session_usage = {}
    return result


def inject_utilities_with_monitoring(utilities: dict[str, Callable]) -> None:
    """Inject utilities into kernel with monitoring wrappers.

    1. Inject raw functions via cloudpickle (directly, NOT via inject_custom_functions
       to avoid registering on builtins — _sync_builtins_to_kernel would overwrite wrappers)
    2. Inject monitoring bootstrap (_utility_usage dict + _monitor_utility decorator)
    3. Wrap each function with _monitor_utility in kernel
    """
    global _utilities_injected, _injected_utility_names
    if not utilities:
        return

    # Step 1: inject raw functions directly into kernel (bypass builtins registry)
    _inject_into_kernel(utilities)

    # Step 2: inject monitoring infrastructure
    kc = _get_or_start_kernel()
    bootstrap = (
        "import functools as _functools\n"
        "_utility_usage = {}\n"
        "def _monitor_utility(func, name):\n"
        "    @_functools.wraps(func)\n"
        "    def wrapper(*args, **kwargs):\n"
        "        entry = _utility_usage.setdefault(name, {'calls': 0, 'errors': 0})\n"
        "        entry['calls'] += 1\n"
        "        try:\n"
        "            return func(*args, **kwargs)\n"
        "        except Exception:\n"
        "            entry['errors'] += 1\n"
        "            raise\n"
        "    return wrapper\n"
    )
    _execute_in_kernel(kc, bootstrap, timeout=10)

    # Step 3: wrap each utility with the monitor
    names = list(utilities.keys())
    for name in names:
        _execute_in_kernel(kc, f"{name} = _monitor_utility({name}, {name!r})", timeout=5)

    _utilities_injected = True
    _injected_utility_names = names


def _reinject_monitoring_bootstrap() -> None:
    """Re-inject monitoring bootstrap after kernel restart.

    The utilities themselves are re-synced via _sync_builtins_to_kernel,
    so we only need to re-inject the monitoring infrastructure and re-wrap.
    """
    global _utilities_injected
    if not _utilities_injected or not _injected_utility_names:
        return

    kc = _get_or_start_kernel()
    bootstrap = (
        "import functools as _functools\n"
        "_utility_usage = {}\n"
        "def _monitor_utility(func, name):\n"
        "    @_functools.wraps(func)\n"
        "    def wrapper(*args, **kwargs):\n"
        "        entry = _utility_usage.setdefault(name, {'calls': 0, 'errors': 0})\n"
        "        entry['calls'] += 1\n"
        "        try:\n"
        "            return func(*args, **kwargs)\n"
        "        except Exception:\n"
        "            entry['errors'] += 1\n"
        "            raise\n"
        "    return wrapper\n"
    )
    _execute_in_kernel(kc, bootstrap, timeout=10)

    # Re-wrap utilities (they'll have been re-injected by _sync_builtins_to_kernel)
    for name in _injected_utility_names:
        _execute_in_kernel(
            kc,
            f"if '{name}' in dir(): {name} = _monitor_utility({name}, {name!r})",
            timeout=5,
        )


# ---------------------------------------------------------------------------
# Module-level kernel singleton (mirrors old _PERSISTENT_NS pattern)
# ---------------------------------------------------------------------------

# Name of the builtins attribute used as a cross-module registry of agent
# tools. Must match ReplBridge / tools.repl_bridge.
_CUSTOM_FNS_ATTR = "_base_CAi_custom_functions"

# Where captured plots get saved. Set by the backend via set_workspace_dir().
_WORKSPACE_DIR: str | None = None

# Kernel manager + client — lazily initialised on first use.
_km = None   # jupyter_client.KernelManager
_kc = None   # jupyter_client.BlockingKernelClient

# Serialised access to the kernel: one execution at a time.
_kernel_lock = threading.Lock()

# Prevent duplicate atexit registration.
_atexit_registered = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def set_workspace_dir(path: str) -> None:
    """Configure where auto-captured plots are saved and set the kernel cwd."""
    global _WORKSPACE_DIR
    _WORKSPACE_DIR = path
    os.makedirs(path, exist_ok=True)
    # If kernel is already running, update its working directory.
    try:
        kc = _get_or_start_kernel()
        _execute_in_kernel(kc, f"import os as _os; _os.chdir({path!r}); del _os", timeout=10)
    except Exception:
        pass  # Best-effort; will be applied at next kernel init


def run_python_repl(code: str, timeout: float = 600) -> str:
    """Execute `code` in the persistent kernel; return captured output.

    On timeout: interrupts the kernel (SIGINT), drains remaining messages,
    and returns a "TIMEOUT: ..." string.  If the kernel becomes unresponsive
    after the interrupt, it is restarted automatically.

    After execution any new image files created in the workspace are detected
    and reported, and open matplotlib figures are auto-saved.
    """
    code = (code or "").strip("`").strip()
    if not code:
        return ""

    # Sync any tools registered on builtins since last call.
    _sync_builtins_to_kernel()

    # Snapshot existing images before execution.
    images_before = _snapshot_workspace_images()

    kc = _get_or_start_kernel()
    output, error = _execute_in_kernel(kc, code, timeout=timeout)

    # Auto-capture matplotlib figures and detect new workspace images.
    saved_plots = _capture_plots(kc)
    new_images = _detect_new_images(images_before)
    all_images = list(dict.fromkeys(saved_plots + new_images))

    result = output
    if error:
        result = result + error if result else error
    if all_images:
        result += "\n" + "\n".join(f"[Image saved]: {p}" for p in all_images) + "\n"

    # Collect utility usage stats from kernel (non-blocking, never affects result).
    if _utilities_injected:
        try:
            import json as _json_mod  # noqa: PLC0415
            collect_code = (
                "import json as _json; "
                "print('__UTIL_USAGE__:' + _json.dumps(dict(_utility_usage))); "
                "_utility_usage.clear()"
            )
            usage_out, _ = _execute_in_kernel(kc, collect_code, timeout=5)
            if usage_out:
                for line in usage_out.splitlines():
                    if line.startswith("__UTIL_USAGE__:"):
                        data = _json_mod.loads(line[len("__UTIL_USAGE__:"):])
                        _accumulate_utility_usage(data)
                        break
        except Exception:
            pass  # Monitoring failure must never affect normal execution

    return result


def inject_custom_functions(custom_functions: dict[str, Callable] | None) -> None:
    """Inject functions into the kernel namespace and the builtins registry.

    Uses cloudpickle so closures and locally-defined functions work, not
    just module-level callables.
    """
    if not custom_functions:
        return

    # Always update the process-side builtins registry — ReplBridge and
    # tests check it.
    registry = getattr(builtins, _CUSTOM_FNS_ATTR, None)
    if registry is None:
        registry = {}
        setattr(builtins, _CUSTOM_FNS_ATTR, registry)
    registry.update(custom_functions)

    # Inject into the kernel via cloudpickle serialisation.
    _inject_into_kernel(custom_functions)


def reset_namespace() -> None:
    """Clear the persistent REPL namespace.

    Uses IPython's %reset magic to wipe user-defined names without
    restarting the kernel process — fast (~50 ms) and safe for test
    isolation.
    """
    try:
        kc = _get_or_start_kernel()
        _execute_in_kernel(kc, "%reset -f", timeout=15)
    except Exception as exc:
        logger.warning("reset_namespace: %s", exc)


# ---------------------------------------------------------------------------
# Kernel lifecycle
# ---------------------------------------------------------------------------

def _get_or_start_kernel():
    """Return the live kernel client, starting the kernel if needed."""
    global _km, _kc, _atexit_registered
    with _kernel_lock:
        if _km is None or not _km.is_alive():
            _start_kernel()
        if not _atexit_registered:
            atexit.register(_shutdown_kernel)
            _atexit_registered = True
    return _kc


def _start_kernel() -> None:
    """Start a fresh IPython kernel and prime its environment."""
    global _km, _kc
    from jupyter_client import KernelManager  # noqa: PLC0415

    logger.debug("Starting Jupyter kernel...")
    km = KernelManager(kernel_name="python3")
    km.start_kernel()
    kc = km.blocking_client()
    kc.start_channels()
    try:
        kc.wait_for_ready(timeout=30)
    except RuntimeError as exc:
        km.shutdown_kernel(now=True)
        raise RuntimeError(f"Jupyter kernel failed to start: {exc}") from exc

    _km = km
    _kc = kc
    logger.debug("Kernel started")
    _init_kernel_env(kc)


def _init_kernel_env(kc) -> None:
    """Run one-time setup code in a freshly started kernel."""
    setup_lines = [
        "import os, sys, warnings",
        # Suppress matplotlib font-not-found warnings — they flood stderr with
        # CJK glyph messages when the system lacks CJK fonts, and the raw
        # warning text can be mistaken for output by the image-capture parser.
        "warnings.filterwarnings('ignore', message='Glyph .* missing from font', category=UserWarning)",
        # Force non-interactive matplotlib backend.
        "try:\n    import matplotlib\n    matplotlib.use('Agg')\nexcept ImportError:\n    pass",
    ]

    # CJK font configuration — same candidates as the old module.
    setup_lines.append(
        "\n".join([
            "try:",
            "    import matplotlib",
            "    from matplotlib import font_manager as _fm",
            "    _candidates = ['Microsoft YaHei','SimHei','PingFang SC','Heiti SC',",
            "        'Noto Sans CJK SC','Noto Sans CJK JP','Source Han Sans SC',",
            "        'WenQuanYi Micro Hei','Arial Unicode MS']",
            "    _installed = {f.name for f in _fm.fontManager.ttflist}",
            "    _pick = next((c for c in _candidates if c in _installed), None)",
            "    _existing = list(matplotlib.rcParams.get('font.sans-serif', []))",
            "    if _pick and _pick not in _existing:",
            "        matplotlib.rcParams['font.sans-serif'] = [_pick] + _existing",
            "    matplotlib.rcParams['axes.unicode_minus'] = False",
            "    del _fm, _candidates, _installed, _pick, _existing",
            "except Exception:",
            "    pass",
        ])
    )

    if _WORKSPACE_DIR:
        setup_lines.append(f"os.chdir({_WORKSPACE_DIR!r})")

    _execute_in_kernel(kc, "\n".join(setup_lines), timeout=30)

    # Re-inject utility monitoring if utilities were previously injected.
    # This handles the case where _start_kernel() is called fresh after a
    # failed restart — _reinject_monitoring_bootstrap() is a no-op if
    # _utilities_injected is False.
    _reinject_monitoring_bootstrap()


def _shutdown_kernel() -> None:
    """Gracefully shut down the kernel on interpreter exit."""
    global _km, _kc
    km, kc = _km, _kc
    _km = _kc = None
    if kc is not None:
        try:
            kc.stop_channels()
        except Exception:
            pass
    if km is not None:
        try:
            km.shutdown_kernel(now=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Core kernel execution
# ---------------------------------------------------------------------------

def _execute_in_kernel(kc, code: str, timeout: float) -> tuple[str, str]:
    """Send `code` to the kernel; collect and return (stdout_text, error_text).

    On timeout: sends SIGINT, drains remaining output, restarts if needed.
    Never raises — errors and timeouts are returned as strings.
    """
    try:
        msg_id = kc.execute(code)
    except Exception as exc:
        return "", f"Error: failed to send code to kernel: {exc}"

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    error_text: str = ""
    deadline = time.monotonic() + timeout
    timed_out = False

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 and not timed_out:
            timed_out = True
            _handle_timeout(kc, timeout)
            # Give a short window to collect any final messages.
            remaining = 3.0

        try:
            msg = kc.get_iopub_msg(timeout=min(max(remaining, 0.05), 1.0))
        except queue.Empty:
            if timed_out:
                break
            continue
        except Exception:
            break

        msg_type = msg.get("msg_type", "")
        content = msg.get("content", {})

        if msg_type == "stream":
            if content.get("name") == "stdout":
                stdout_parts.append(content.get("text", ""))
            else:
                stderr_parts.append(content.get("text", ""))
        elif msg_type == "execute_result":
            text = content.get("data", {}).get("text/plain", "")
            if text:
                stdout_parts.append(text + "\n")
        elif msg_type == "error":
            # Use the plain-text traceback lines (strip ANSI codes).
            tb_lines = content.get("traceback", [])
            clean_tb = "\n".join(_strip_ansi(line) for line in tb_lines)
            ename = content.get("ename", "Error")
            evalue = content.get("evalue", "")
            error_text = f"Error: {ename}: {evalue}\n{clean_tb}".rstrip()
        elif msg_type == "status":
            if content.get("execution_state") == "idle":
                break

    stdout = "".join(stdout_parts)
    # Append stderr so the agent can see warnings/logs, but keep it
    # visually separate so it's clear it's not stdout.
    stderr = "".join(stderr_parts).strip()
    if stderr:
        stdout = stdout + ("\n" if stdout else "") + stderr + "\n"
    if timed_out and not error_text:
        error_text = f"TIMEOUT: Code execution timed out after {timeout} seconds"

    return stdout, error_text


def _handle_timeout(kc, timeout: float) -> None:
    """Interrupt the kernel; restart it if it stays unresponsive."""
    global _km
    km = _km
    if km is None:
        return
    logger.warning("Execution timed out after %ss — interrupting kernel", timeout)
    try:
        km.interrupt_kernel()
    except Exception as exc:
        logger.warning("interrupt_kernel failed: %s", exc)

    # Check responsiveness with a quick ping.
    try:
        kc.kernel_info(reply=True, timeout=5)
    except Exception:
        logger.warning("Kernel unresponsive after interrupt — restarting")
        _restart_kernel()


def _restart_kernel() -> None:
    """Restart the kernel and re-initialise its environment."""
    global _km, _kc
    km, old_kc = _km, _kc
    if old_kc is not None:
        try:
            old_kc.stop_channels()
        except Exception:
            pass
    if km is not None:
        try:
            km.restart_kernel(now=True)
            new_kc = km.blocking_client()
            new_kc.start_channels()
            new_kc.wait_for_ready(timeout=30)
            _kc = new_kc
            _init_kernel_env(new_kc)
            logger.info("Kernel restarted successfully")
        except Exception as exc:
            logger.error("Kernel restart failed: %s — starting fresh", exc)
            _km = _kc = None
            _start_kernel()


# ---------------------------------------------------------------------------
# Tool injection
# ---------------------------------------------------------------------------

def _inject_into_kernel(funcs: dict[str, Callable]) -> None:
    """Serialize `funcs` with cloudpickle and inject them into the kernel."""
    try:
        import base64  # noqa: PLC0415
        import cloudpickle  # noqa: PLC0415
    except ImportError:
        logger.warning("cloudpickle not installed — skipping tool injection into kernel")
        return

    try:
        payload = base64.b64encode(cloudpickle.dumps(funcs)).decode()
    except Exception as exc:
        logger.warning("cloudpickle serialisation failed: %s", exc)
        return

    inject_code = (
        "import cloudpickle as _cp, base64 as _b64\n"
        f"globals().update(_cp.loads(_b64.b64decode({payload!r})))\n"
        "del _cp, _b64"
    )
    kc = _get_or_start_kernel()
    _execute_in_kernel(kc, inject_code, timeout=15)


def _sync_builtins_to_kernel() -> None:
    """Inject any tools registered on builtins since the last call.

    Mirrors the old _sync_custom_fns_into_namespace() behaviour so that
    code like ``setattr(builtins, _CUSTOM_FNS_ATTR, {...})`` (as done by
    ReplBridge and some tests) automatically becomes callable in the REPL.
    """
    registry = getattr(builtins, _CUSTOM_FNS_ATTR, None)
    if registry:
        _inject_into_kernel(registry)


# ---------------------------------------------------------------------------
# Matplotlib plot capture (unchanged from previous module)
# ---------------------------------------------------------------------------

_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".svg", ".gif", ".bmp", ".webp", ".tiff"})


def _capture_plots(kc) -> list[str]:
    """Save all open matplotlib figures in the kernel to workspace."""
    save_dir = _WORKSPACE_DIR or os.getcwd()
    os.makedirs(save_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    capture_code = "\n".join([
        "try:",
        "    import matplotlib.pyplot as _plt",
        "    _fignums = _plt.get_fignums()",
        "    _saved = []",
        "    for _i, _n in enumerate(_fignums):",
        f"        _fp = {save_dir!r} + '/' + f'plot_{timestamp}_{{_i}}.png'",
        "        try:",
        "            _plt.figure(_n).savefig(_fp, dpi=150, bbox_inches='tight', facecolor='white')",
        "            _saved.append(_fp)",
        "        except Exception:",
        "            pass",
        "    _plt.close('all')",
        "    print('\\n'.join(_saved))",
        "    del _plt, _fignums, _saved, _i, _n, _fp",
        "except ImportError:",
        "    pass",
    ])
    stdout, _ = _execute_in_kernel(kc, capture_code, timeout=15)
    return [
        p.strip() for p in stdout.splitlines()
        if p.strip() and os.path.splitext(p.strip())[1].lower() in _IMAGE_EXTENSIONS
    ]


def _snapshot_workspace_images() -> set[str]:
    save_dir = _WORKSPACE_DIR
    if not save_dir or not os.path.isdir(save_dir):
        return set()
    result = set()
    try:
        for f in os.listdir(save_dir):
            if os.path.splitext(f)[1].lower() in _IMAGE_EXTENSIONS:
                result.add(os.path.join(save_dir, f))
    except OSError:
        pass
    return result


def _detect_new_images(before: set[str]) -> list[str]:
    save_dir = _WORKSPACE_DIR
    if not save_dir or not os.path.isdir(save_dir):
        return []
    after: set[str] = set()
    try:
        for f in os.listdir(save_dir):
            if os.path.splitext(f)[1].lower() in _IMAGE_EXTENSIONS:
                after.add(os.path.join(save_dir, f))
    except OSError:
        return []
    return sorted(after - before)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import re as _re

_ANSI_ESCAPE = _re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)
