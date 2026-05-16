"""ToolSpec — immutable metadata for a registered tool."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field


# Markers that, once seen in a docstring, indicate the start of structured
# sections (args, returns, ...) which we don't want in the system prompt.
_DOC_CUT_MARKERS = ("Args:", "Parameters:", "Returns:", "Example", "Notes:", "---")


def _truncate_doc(doc: str, max_chars: int = 400) -> str:
    """Trim a docstring to its first meaningful paragraph for prompt use."""
    if not doc:
        return "No description."

    for marker in _DOC_CUT_MARKERS:
        idx = doc.find(marker)
        if idx > 0:
            doc = doc[:idx].rstrip()
            break

    if len(doc) > max_chars:
        doc = doc[:max_chars].rsplit("\n", 1)[0] + "\n    ..."

    return doc


@dataclass(frozen=True)
class ToolSpec:
    """Immutable descriptor for a tool callable.

    Attributes:
        name:       Public name used in the REPL and in the prompt catalog.
        func:       The callable itself.
        signature:  Pre-computed str(inspect.signature(func)).
        short_doc:  Docstring already truncated for prompt display.
        source:     Provenance label (e.g. "module:CAi.toolkit",
                    "runtime", "config:tools.yaml"). Purely informational.
        hidden:     If True, the tool is callable from REPL but is NOT listed
                    in the prompt's tool catalog (useful for skill helpers).
        tags:       Optional labels for filtering / grouping.
    """

    name: str
    func: Callable
    signature: str
    short_doc: str
    source: str = "runtime"
    hidden: bool = False
    tags: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_function(
        cls,
        func: Callable,
        *,
        name: str | None = None,
        source: str = "runtime",
        hidden: bool = False,
        tags: Iterable[str] = (),
        max_doc_chars: int = 400,
    ) -> "ToolSpec":
        """Build a ToolSpec by introspecting the callable.

        Args:
            func:          The function to wrap.
            name:          Override the public name (defaults to func.__name__).
            source:        Provenance label.
            hidden:        Hide from prompt catalog (but keep callable).
            tags:          Any labels you want to attach.
            max_doc_chars: Cap for the stored short_doc.
        """
        fn_name = name or getattr(func, "__name__", None) or repr(func)
        try:
            sig = str(inspect.signature(func))
        except (TypeError, ValueError):
            sig = "(...)"
        raw_doc = (getattr(func, "__doc__", None) or "").strip()
        return cls(
            name=fn_name,
            func=func,
            signature=sig,
            short_doc=_truncate_doc(raw_doc, max_chars=max_doc_chars),
            source=source,
            hidden=hidden,
            tags=frozenset(tags),
        )
