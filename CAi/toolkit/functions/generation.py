"""Molecule generation tools.

Each function wraps a server-side model (scaffold-constrained generators,
de-novo generators, pocket-aware generators). The wrappers validate
inputs, call run_tool(...), and reshape the response into a JSON string
for the agent.
"""

from __future__ import annotations

import json

from .._validators import non_empty_smiles, reject_chiral, require_attachment_point
from ..client import run_tool


def generate_scaffold_analogs(smiles: str, num_analogs: int = 10) -> str:
    """
    Generate novel molecular analogs from a scaffold SMILES using a pre-trained RNN-based scaffold generation model.

    When to use:
        The user provides a scaffold SMILES with an explicit '*' growth point.

    Do not use when:
        - The input is a complete molecule rather than a scaffold.
        - The SMILES lacks a '*' attachment point.
        - The SMILES contains '@@' stereochemistry.

    Args:
        smiles: Scaffold SMILES (must contain '*'). Example: 'c1ccccc1*'.
        num_analogs: How many analogs to request (default 10; actual count may be smaller).

    Returns:
        JSON string.
        Success: {"status": "success", "generated_count": int, "molecules": [smiles, ...]}
        Error:   {"error": str}
    """
    if err := require_attachment_point(smiles):
        return json.dumps({"error": err})

    result = run_tool("scaffold", {"smiles": smiles, "num_analogs": num_analogs})
    if result.get("error"):
        return json.dumps(result)

    summary = result.get("summary", {})
    generated_smiles = [item["smiles"] for item in result.get("results", [])]

    return json.dumps(
        {
            "status": "success",
            "generated_count": summary.get("valid_unique_generated"),
            "molecules": generated_smiles,
        },
        ensure_ascii=False,
    )


def generate_libinvent_decorations(smiles: str, num_decorations: int = 3) -> str:
    """
    Decorate a chemical scaffold using the Lib-INVENT reaction-based model.

    Generates decorated molecules by attaching substituents at the scaffold's
    '[*]' attachment points.

    Args:
        smiles: Scaffold SMILES with at least one '*' or '[*:1]' attachment point
                (no '@@' stereochemistry).
        num_decorations: How many decorated variants to request (default 3).

    Returns:
        JSON string. See status / error fields on success / failure.
    """
    if err := require_attachment_point(smiles):
        return json.dumps({"error": err})
    if err := reject_chiral(smiles):
        return json.dumps({"error": err})

    payload = {"smiles": smiles, "number_of_decorations_per_scaffold": num_decorations}
    result = run_tool("libinvent", payload)
    if result.get("error"):
        return json.dumps({"error": result["error"]})

    summary = result.get("summary", {})
    results = result.get("results", [])
    molecules_smiles = [row.get("SMILES") for row in results if row.get("SMILES")]
    input_scaffold = results[0].get("input_scaffold") if results else None

    return json.dumps(
        {
            "status": "success",
            "input_scaffold": input_scaffold,
            "requested_num_decorations": num_decorations,
            "generated_count": summary.get("row_count"),
            "csv_columns": summary.get("columns", []),
            "molecules_smiles": molecules_smiles,
            "decorated_molecules_preview": summary.get("preview", []),
        },
        ensure_ascii=False,
    )


def generate_molecules_for_pocket(
    protein_pdb_path: str,
    center_xyz: list | None = None,
    ref_ligand_path: str | None = None,
    num_samples: int = 10,
) -> str:
    """
    Target-aware zero-shot molecular generation with RxnFlow.

    Generates candidate molecules for a protein target using either:
      1. protein file + binding-pocket center coordinates, or
      2. protein file + reference-ligand file.

    Args:
        protein_pdb_path: Target protein structure (.sdf / .mol2 / .pdb).
        center_xyz: [x, y, z] pocket center (optional if ref_ligand_path is given).
        ref_ligand_path: Reference ligand structure (optional if center_xyz is given).
        num_samples: Molecules to generate (default 10).

    Returns:
        JSON string with generated_count, sampling_time_sec, full_results_csv_path,
        and top_molecules_preview (small preview, not the full set).
    """
    if not center_xyz and not ref_ligand_path:
        return json.dumps(
            {"error": "Provide either center_xyz or ref_ligand_path to define the pocket."}
        )

    payload: dict = {
        "protein_pdb_path": protein_pdb_path,
        "num_samples": num_samples,
        "save_reward": True,
    }
    if center_xyz:
        payload["center"] = center_xyz
    if ref_ligand_path:
        payload["ref_ligand_path"] = ref_ligand_path

    result = run_tool("rxnflow", payload, timeout_mins=15)
    if result.get("error"):
        return json.dumps({"error": result["error"]})

    summary = result.get("summary", {})
    results_data = result.get("results", {})

    return json.dumps(
        {
            "status": "success",
            "generated_count": summary.get("generated_count"),
            "sampling_time_sec": summary.get("sampling_time_sec"),
            "full_results_csv_path": summary.get("output_file"),
            "top_molecules_preview": results_data.get("generated_preview", []),
        },
        ensure_ascii=False,
    )


def generate_molecules_reinvent4_denovo(num_variants: int = 100) -> str:
    """
    Generate completely novel molecules from scratch using the REINVENT4 de novo prior model.

    No input scaffold is needed. Suitable for broad chemical-space exploration.

    Args:
        num_variants: Number of molecules to generate (default 100).

    Returns:
        JSON string with status, generated_count, and molecules_smiles.
    """
    result = run_tool("reinvent4", {"num_variants": num_variants}, action="de_novo", timeout_mins=10)
    if result.get("error"):
        return json.dumps({"error": result["error"]})

    summary = result.get("summary", {})
    molecules_data = result.get("results", {}).get("molecules", [])
    smiles_list = [mol["smiles"] for mol in molecules_data if mol.get("smiles")]

    return json.dumps(
        {
            "status": "success",
            "generated_count": summary.get("generated_count", len(smiles_list)),
            "molecules_smiles": smiles_list,
        },
        ensure_ascii=False,
    )


def generate_molecules_reinvent4_libinvent(smiles: str, num_variants: int = 50) -> str:
    """
    Decorate a chemical scaffold by generating R-group variants at [*] attachment points
    using the REINVENT4 LibInvent model.

    The input MUST be a scaffold SMILES containing at least one [*] wildcard.
    Does NOT support '@@' stereochemistry — use mol2mol mode for chiral molecules.

    Args:
        smiles: Scaffold SMILES with [*] attachment points.
        num_variants: Variants to generate (default 50).
    """
    if err := require_attachment_point(smiles):
        return json.dumps({"error": err})

    result = run_tool(
        "reinvent4",
        {"smiles_list": [smiles], "num_variants": num_variants},
        action="libinvent",
        timeout_mins=10,
    )
    if result.get("error"):
        return json.dumps({"error": result["error"]})

    summary = result.get("summary", {})
    molecules_data = result.get("results", {}).get("molecules", [])
    smiles_list = [mol["smiles"] for mol in molecules_data if mol.get("smiles")]

    return json.dumps(
        {
            "status": "success",
            "input_scaffold": smiles,
            "generated_count": summary.get("generated_count", len(smiles_list)),
            "molecules_smiles": smiles_list,
        },
        ensure_ascii=False,
    )


def generate_molecules_reinvent4_mol2mol(
    smiles: str,
    num_variants: int = 50,
    strategy: str = "beamsearch",
    temperature: float = 1.0,
) -> str:
    """
    Generate structural analogs of a reference molecule while preserving stereochemistry
    using the REINVENT4 Mol2Mol model.

    Input should be a complete SMILES string (supports '@@' chirality).
    Does NOT support [*] wildcards — use libinvent for scaffold decoration.

    Args:
        smiles: Complete reference-molecule SMILES.
        num_variants: Analogs to generate (default 50).
        strategy: 'beamsearch' or 'multinomial' (default beamsearch).
        temperature: Sampling temperature (default 1.0).
    """
    if err := non_empty_smiles(smiles):
        return json.dumps({"error": err})

    payload = {
        "smiles_list": [smiles],
        "num_variants": num_variants,
        "strategy": strategy,
        "temperature": temperature,
    }
    result = run_tool("reinvent4", payload, action="mol2mol", timeout_mins=10)
    if result.get("error"):
        return json.dumps({"error": result["error"]})

    summary = result.get("summary", {})
    molecules_data = result.get("results", {}).get("molecules", [])
    smiles_out = [mol["smiles"] for mol in molecules_data if mol.get("smiles")]

    return json.dumps(
        {
            "status": "success",
            "input_smiles": smiles,
            "generated_count": summary.get("generated_count", len(smiles_out)),
            "molecules_smiles": smiles_out,
        },
        ensure_ascii=False,
    )
