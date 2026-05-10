"""Small SMILES input validators used by multiple tool wrappers."""

from __future__ import annotations


def require_attachment_point(smiles: str) -> str | None:
    """Return an error message if the scaffold has no '*' attachment point."""
    if not smiles or "*" not in smiles:
        return (
            "The input scaffold SMILES must contain at least one '*' "
            "character as the growth/attachment point."
        )
    return None


def reject_chiral(smiles: str) -> str | None:
    """Return an error message if the SMILES contains '@@' stereochemistry."""
    if smiles and "@@" in smiles:
        return (
            "The input scaffold SMILES must not contain '@@' stereochemistry "
            "(use the mol2mol tool for chiral molecules)."
        )
    return None


def non_empty_smiles(smiles: str) -> str | None:
    """Return an error message if the SMILES is empty / whitespace."""
    if not smiles or not smiles.strip():
        return "A non-empty SMILES string is required."
    return None
