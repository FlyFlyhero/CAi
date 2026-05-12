"""
Persistent Python REPL for agent code execution.

A thin `exec()` wrapper that:
  - keeps a persistent namespace across calls (variables survive between
    <execute> blocks, matching the original behaviour)
  - captures stdout so print() output is returned to the caller
  - reports exceptions as 'Error: ...' strings (never raises)
  - lets ReplBridge inject agent-registered tools via
    inject_custom_functions()
  - configures matplotlib to use CJK-capable fonts on first use
    (avoids the 'Glyph XXX missing from font DejaVu Sans' warnings
    that flood stderr whenever the agent generates Chinese plot labels)
  - auto-captures matplotlib figures after each execution and saves
    them to the workspace directory so the frontend can render them
"""

from __future__ import annotations

import builtins
import os
import sys
from collections.abc import Callable
from datetime import datetime
from io import StringIO

# The namespace shared across all run_python_repl() calls.
# Variables defined in one execution are visible in the next.
_PERSISTENT_NS: dict[str, object] = {}

# Name of the builtins attribute used as a cross-module registry of
# agent tools. Must match ReplBridge / tools.repl_bridge.
_CUSTOM_FNS_ATTR = "_base_CAi_custom_functions"

# Tracks whether we've already tried to configure matplotlib fonts in
# this process. Import and configuration only run on first REPL call
# that actually imports matplotlib.
_MPL_CONFIGURED = False

# Where captured plots get saved. Set by the backend via
# set_workspace_dir(). Falls back to cwd if never set.
_WORKSPACE_DIR: str | None = None


def set_workspace_dir(path: str) -> None:
    """Configure where auto-captured plots are saved.

    Called by the backend during startup so figures land in the same
    workspace the frontend lists and previews.
    """
    global _WORKSPACE_DIR
    _WORKSPACE_DIR = path
    os.makedirs(path, exist_ok=True)


def _configure_matplotlib_cjk() -> None:
    """Prepend CJK-capable fonts to matplotlib's sans-serif stack.

    Matplotlib's default DejaVu Sans has no Chinese / Japanese / Korean
    glyphs — when the agent generates a plot with Chinese labels we get
    a wall of 'Glyph XXXX missing from font DejaVu Sans' warnings and
    boxes in place of characters. Here we try a handful of commonly
    installed CJK fonts and put the first one we find at the front of
    matplotlib's sans-serif fallback list.

    This is cheap when matplotlib isn't installed (we catch ImportError)
    and idempotent (gated by _MPL_CONFIGURED).
    """
    global _MPL_CONFIGURED
    if _MPL_CONFIGURED:
        return
    _MPL_CONFIGURED = True

    try:
        import matplotlib
    except ImportError:
        return

    # Candidate CJK fonts, ordered by availability on common platforms.
    # (Windows: Microsoft YaHei, SimHei; macOS: PingFang SC, Heiti SC,
    # Apple Color Emoji; Linux: Noto Sans CJK, WenQuanYi.)
    candidates = (
        "Microsoft YaHei",
        "SimHei",
        "PingFang SC",
        "Heiti SC",
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "Arial Unicode MS",
    )

    try:
        from matplotlib import font_manager

        installed = {f.name for f in font_manager.fontManager.ttflist}
        picked = next((c for c in candidates if c in installed), None)

        existing = list(matplotlib.rcParams.get("font.sans-serif", []))
        if picked and picked not in existing:
            matplotlib.rcParams["font.sans-serif"] = [picked] + existing
        # Keep minus signs rendering correctly after we change fonts.
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:
        # Never let font configuration break REPL execution.
        pass


def run_python_repl(code: str) -> str:
    """Execute `code` in the persistent namespace; return captured stdout
    (or an 'Error: ...' message).

    After execution:
      1. Any open matplotlib figures are auto-saved to the workspace.
      2. Any new image files created in the workspace during execution
         (by RDKit, Pillow, plotly, etc.) are detected and reported.
    """
    code = (code or "").strip("`").strip()

    # Ensure any custom tools registered since the last call are visible.
    _sync_custom_fns_into_namespace()

    # Configure matplotlib CJK fonts once (best-effort, silent on failure).
    _configure_matplotlib_cjk()

    # Force matplotlib to use non-interactive backend so plt.show() doesn't
    # block and figures are retained for capture.
    _ensure_agg_backend()

    # Snapshot existing image files before execution
    images_before = _snapshot_workspace_images()

    old_stdout = sys.stdout
    sys.stdout = buf = StringIO()
    try:
        exec(code, _PERSISTENT_NS)  # noqa: S102 — intentional; REPL-like env
        output = buf.getvalue()
    except Exception as e:  # noqa: BLE001 — surfaced to caller as string
        output = buf.getvalue() + f"Error: {e}"
    finally:
        sys.stdout = old_stdout

    # Auto-capture any matplotlib figures that were created during execution.
    saved_plots = _capture_plots()

    # Detect any new image files created by other libraries (RDKit, Pillow, etc.)
    new_images = _detect_new_images(images_before)

    # Combine all discovered images (deduplicate)
    all_images = list(dict.fromkeys(saved_plots + new_images))

    if all_images:
        plot_lines = "\n".join(f"[Image saved]: {p}" for p in all_images)
        output = output + "\n" + plot_lines + "\n"

    return output


def inject_custom_functions(custom_functions: dict[str, Callable]) -> None:
    """Make the given functions callable from within the REPL.

    Also records them on `builtins._base_CAi_custom_functions` so other
    modules (e.g. ReplBridge, or code running inside the REPL that needs
    to introspect available tools) can find them.
    """
    if not custom_functions:
        return

    for name, func in custom_functions.items():
        _PERSISTENT_NS[name] = func

    registry = getattr(builtins, _CUSTOM_FNS_ATTR, None)
    if registry is None:
        registry = {}
        setattr(builtins, _CUSTOM_FNS_ATTR, registry)
    registry.update(custom_functions)


def reset_namespace() -> None:
    """Clear the persistent REPL namespace. Primarily for tests."""
    global _MPL_CONFIGURED
    _PERSISTENT_NS.clear()
    _MPL_CONFIGURED = False


def _sync_custom_fns_into_namespace() -> None:
    """Pull anything registered on builtins into the REPL namespace.

    This lets ReplBridge update builtins outside of the REPL and still
    have those tools appear in `exec()` on the next call.
    """
    registry = getattr(builtins, _CUSTOM_FNS_ATTR, None)
    if registry:
        for name, func in registry.items():
            _PERSISTENT_NS.setdefault(name, func)


# ---------------------------------------------------------------------------
# Matplotlib plot capture
# ---------------------------------------------------------------------------

_AGG_SET = False


def _ensure_agg_backend() -> None:
    """Switch matplotlib to the non-interactive 'Agg' backend.

    This prevents plt.show() from opening a GUI window and ensures
    figures stay in memory so we can save them. Only runs once.
    """
    global _AGG_SET
    if _AGG_SET:
        return
    _AGG_SET = True
    try:
        import matplotlib
        matplotlib.use("Agg")
    except (ImportError, Exception):
        pass


def _capture_plots() -> list[str]:
    """Save all open matplotlib figures to disk and close them.

    Returns a list of absolute file paths for saved images.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    figs = [plt.figure(n) for n in plt.get_fignums()]
    if not figs:
        return []

    save_dir = _WORKSPACE_DIR or os.getcwd()
    os.makedirs(save_dir, exist_ok=True)

    saved = []
    for i, fig in enumerate(figs):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"plot_{timestamp}_{i}.png"
        filepath = os.path.join(save_dir, filename)
        try:
            fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="white")
            saved.append(filepath)
        except Exception:
            pass  # Don't let save failures break execution

    # Close all figures to free memory
    plt.close("all")
    return saved


# ---------------------------------------------------------------------------
# Generic image file detection (RDKit, Pillow, plotly, etc.)
# ---------------------------------------------------------------------------

_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".svg", ".gif", ".bmp", ".webp", ".tiff"})


def _snapshot_workspace_images() -> set[str]:
    """Return the set of image file paths currently in the workspace."""
    save_dir = _WORKSPACE_DIR
    if not save_dir or not os.path.isdir(save_dir):
        return set()
    result = set()
    try:
        for f in os.listdir(save_dir):
            ext = os.path.splitext(f)[1].lower()
            if ext in _IMAGE_EXTENSIONS:
                result.add(os.path.join(save_dir, f))
    except OSError:
        pass
    return result


def _detect_new_images(before: set[str]) -> list[str]:
    """Return image files that appeared in the workspace since `before` snapshot."""
    save_dir = _WORKSPACE_DIR
    if not save_dir or not os.path.isdir(save_dir):
        return []
    after = set()
    try:
        for f in os.listdir(save_dir):
            ext = os.path.splitext(f)[1].lower()
            if ext in _IMAGE_EXTENSIONS:
                after.add(os.path.join(save_dir, f))
    except OSError:
        return []
    new_files = sorted(after - before)
    return new_files
