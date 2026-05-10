"""Unit tests for ToolSpec and its from_function factory."""

from __future__ import annotations

from CAi.CAi_agent.tools.spec import ToolSpec, _truncate_doc


# ---------------------------------------------------------------------------
# _truncate_doc
# ---------------------------------------------------------------------------


def test_truncate_empty():
    assert _truncate_doc("") == "No description."


def test_truncate_cuts_at_args():
    doc = "Compute X.\n\nArgs:\n    a: first"
    out = _truncate_doc(doc)
    assert "Args:" not in out
    assert "Compute X" in out


def test_truncate_cuts_at_returns():
    doc = "Short summary.\n\nReturns:\n    dict: stuff"
    out = _truncate_doc(doc)
    assert "Returns:" not in out
    assert "Short summary" in out


def test_truncate_respects_max_chars():
    out = _truncate_doc("A" * 1000, max_chars=100)
    assert len(out) < 250


# ---------------------------------------------------------------------------
# ToolSpec.from_function
# ---------------------------------------------------------------------------


def test_from_function_captures_name_and_signature():
    def foo(x: int, y: str = "a") -> dict:
        """Summary line."""
        return {}

    spec = ToolSpec.from_function(foo)
    assert spec.name == "foo"
    # Python may quote annotations (e.g. "'int'") — just check the names
    assert "x:" in spec.signature
    assert "y:" in spec.signature
    assert "Summary line" in spec.short_doc


def test_from_function_override_name():
    def foo():
        """doc"""

    spec = ToolSpec.from_function(foo, name="aliased")
    assert spec.name == "aliased"


def test_from_function_records_source():
    def foo():
        pass

    spec = ToolSpec.from_function(foo, source="module:foo.bar")
    assert spec.source == "module:foo.bar"


def test_from_function_supports_hidden():
    def foo():
        pass

    spec = ToolSpec.from_function(foo, hidden=True)
    assert spec.hidden is True


def test_from_function_tags_are_frozen():
    def foo():
        pass

    spec = ToolSpec.from_function(foo, tags=["a", "b"])
    assert spec.tags == frozenset({"a", "b"})


def test_from_function_handles_missing_docstring():
    def foo():
        pass

    spec = ToolSpec.from_function(foo)
    assert spec.short_doc == "No description."


def test_from_function_handles_unsignable_callable():
    class Weird:
        def __call__(self):
            pass

    obj = Weird()
    # Some objects trip inspect.signature — should fall back gracefully.
    spec = ToolSpec.from_function(obj, name="weird")
    assert spec.name == "weird"
    # Either we got a real sig, or the fallback
    assert isinstance(spec.signature, str)


def test_spec_is_immutable():
    def foo():
        pass

    spec = ToolSpec.from_function(foo)
    try:
        spec.name = "bar"
    except (AttributeError, Exception):
        pass
    else:
        raise AssertionError("ToolSpec should be frozen")
