"""Tests for CAi.toolkit._validators."""

from __future__ import annotations

from CAi.toolkit._validators import (
    non_empty_smiles,
    reject_chiral,
    require_attachment_point,
)


def test_require_attachment_point_ok():
    assert require_attachment_point("c1ccccc1*") is None
    assert require_attachment_point("[*]C(=O)N") is None


def test_require_attachment_point_missing():
    err = require_attachment_point("c1ccccc1")
    assert err is not None
    assert "*" in err


def test_require_attachment_point_empty():
    assert require_attachment_point("") is not None
    assert require_attachment_point(None) is not None  # type: ignore[arg-type]


def test_reject_chiral_flags_double_at():
    assert reject_chiral("C[C@@H](O)C") is not None


def test_reject_chiral_allows_non_chiral():
    assert reject_chiral("CCO") is None
    # Single @ is allowed (different chirality notation)
    assert reject_chiral("C[C@H](O)C") is None


def test_reject_chiral_handles_empty():
    assert reject_chiral("") is None
    assert reject_chiral(None) is None  # type: ignore[arg-type]


def test_non_empty_smiles_rejects_blanks():
    assert non_empty_smiles("") is not None
    assert non_empty_smiles("   ") is not None
    assert non_empty_smiles(None) is not None  # type: ignore[arg-type]


def test_non_empty_smiles_accepts_valid():
    assert non_empty_smiles("CCO") is None
