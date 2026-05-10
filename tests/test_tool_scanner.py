"""Unit tests for ModuleScanner."""

from __future__ import annotations

import sys
import types

from CAi.CAi_agent.tools.scanner import ModuleScanner


def _make_fake_module(name: str, **attrs) -> types.ModuleType:
    """Create & register a fake module for the duration of a test."""
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def test_scans_public_functions():
    def alpha():
        """alpha doc"""

    def beta():
        """beta doc"""

    def _private():
        """hidden"""

    _make_fake_module("_test_scan_mod_1", alpha=alpha, beta=beta, _private=_private)
    try:
        specs = list(ModuleScanner("_test_scan_mod_1").scan())
        names = {s.name for s in specs}
        assert names == {"alpha", "beta"}
    finally:
        sys.modules.pop("_test_scan_mod_1", None)


def test_exclude_filters_functions():
    def alpha():
        """a"""

    def beta():
        """b"""

    _make_fake_module("_test_scan_mod_2", alpha=alpha, beta=beta)
    try:
        specs = list(ModuleScanner("_test_scan_mod_2", exclude={"beta"}).scan())
        assert [s.name for s in specs] == ["alpha"]
    finally:
        sys.modules.pop("_test_scan_mod_2", None)


def test_hidden_names_marked_on_spec():
    def foo():
        """f"""

    def helper():
        """h"""

    _make_fake_module("_test_scan_mod_3", foo=foo, helper=helper)
    try:
        specs = {s.name: s for s in ModuleScanner("_test_scan_mod_3", hidden={"helper"}).scan()}
        assert specs["foo"].hidden is False
        assert specs["helper"].hidden is True
    finally:
        sys.modules.pop("_test_scan_mod_3", None)


def test_tags_applied_to_all_specs():
    def foo():
        pass

    _make_fake_module("_test_scan_mod_4", foo=foo)
    try:
        specs = list(ModuleScanner("_test_scan_mod_4", tags=["x", "y"]).scan())
        assert specs[0].tags == frozenset({"x", "y"})
    finally:
        sys.modules.pop("_test_scan_mod_4", None)


def test_missing_module_yields_nothing():
    specs = list(ModuleScanner("module.that.does.not.exist").scan())
    assert specs == []


def test_source_label_reflects_module_name():
    def foo():
        pass

    _make_fake_module("_test_scan_mod_5", foo=foo)
    try:
        specs = list(ModuleScanner("_test_scan_mod_5").scan())
        assert specs[0].source == "module:_test_scan_mod_5"
    finally:
        sys.modules.pop("_test_scan_mod_5", None)


def test_respects_module_all_when_present():
    """If the module defines __all__, private-ish names not in it are skipped."""

    def public():
        pass

    def semi():
        pass

    _make_fake_module("_test_scan_mod_6", public=public, semi=semi, __all__=["public"])
    try:
        specs = list(ModuleScanner("_test_scan_mod_6").scan())
        assert [s.name for s in specs] == ["public"]
    finally:
        sys.modules.pop("_test_scan_mod_6", None)


def test_scans_real_additional_tools_module():
    """Smoke test against the actual CAi.additional_tools module."""
    specs = list(ModuleScanner("CAi.additional_tools").scan())
    # At least some tools should be discovered
    assert len(specs) > 0
    assert all(isinstance(s.name, str) for s in specs)
