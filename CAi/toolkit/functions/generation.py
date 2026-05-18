"""Molecule generation tools.

Each function wraps a server-side model (scaffold-constrained generators,
de-novo generators, pocket-aware generators). The wrappers validate
inputs, call run_tool(...), and return a native Python dictionary
for the agent to easily parse.
"""

from __future__ import annotations

from .._validators import (
    reject_chiral,
    require_attachment_point,
    require_pocket_definition,
    valid_complete_molecule_smiles,
)
from ..client import run_tool


def generate_scaffold_analogs(smiles: str, num_analogs: int = 10) -> dict:
    """Generate novel molecular analogs from a scaffold SMILES using a pre-trained
    RNN-based scaffold generation model.

    When to use:
        The user provides a scaffold SMILES with an explicit '*' growth point.

    Do not use when:
        - The input is a complete molecule rather than a scaffold.
        - The SMILES lacks a '*' attachment point.
        - The SMILES contains '@@' stereochemistry.

    Args:
        smiles:      Scaffold SMILES (must contain '*'). Example: 'c1ccccc1*'.
        num_analogs: How many analogs to request (default 10; actual count may be smaller).

    Returns:
        On success:
          {
            "success": True,
            "input_scaffold": str,           # echoed scaffold SMILES
            "requested_batch_size": int,     # requested analog count
            "generated_count": int,          # actual unique molecules generated
            "molecules": [str, ...],         # list of SMILES strings
          }
        On error:
          {"success": False, "error": str}
    """
    if err := require_attachment_point(smiles):
        return {"success": False, "error": err}
    if err := reject_chiral(smiles):
        return {"success": False, "error": err}

    result = run_tool("scaffold", {"smiles": smiles, "num_analogs": num_analogs})
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    generated_smiles = [item["smiles"] for item in result.get("results", [])]

    return {
        "success": True,
        "input_scaffold": summary.get("input_scaffold", smiles),
        "requested_batch_size": summary.get("requested_batch_size", num_analogs),
        "generated_count": summary.get("valid_unique_generated"),
        "molecules": generated_smiles,
    }


def generate_libinvent_decorations(smiles: str, num_decorations: int = 3) -> dict:
    """Decorate a chemical scaffold using the Lib-INVENT reaction-based model.

    Generates decorated molecules by attaching substituents at the scaffold's
    '[*]' attachment points.

    Args:
        smiles:           Scaffold SMILES with at least one '*' or '[*:1]' attachment point
                          (no '@@' stereochemistry).
        num_decorations:  How many decorated variants to request (default 3).

    Returns:
        On success:
          {
            "success": True,
            "input_scaffold": str,                     # echoed scaffold SMILES
            "requested_num_decorations": int,          # requested decoration count
            "generated_count": int,                    # actual unique molecules generated
            "csv_columns": [str, ...],                 # column names in server output
            "molecules_smiles": [str, ...],            # list of SMILES strings
            "decorated_molecules_preview": [           # top-N preview rows
              {"SMILES": str, "status": str, "message": str},
              ...
            ],
          }
        On error:
          {"success": False, "error": str}
    """
    if err := require_attachment_point(smiles):
        return {"success": False, "error": err}
    if err := reject_chiral(smiles):
        return {"success": False, "error": err}

    payload = {"smiles": smiles, "number_of_decorations_per_scaffold": num_decorations}
    result = run_tool("libinvent", payload)
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    results = result.get("results", [])
    molecules_smiles = [row.get("SMILES") for row in results if row.get("SMILES")]
    input_scaffold = results[0].get("input_scaffold") if results else None

    return {
        "success": True,
        "input_scaffold": input_scaffold,
        "requested_num_decorations": num_decorations,
        "generated_count": summary.get("row_count"),
        "csv_columns": summary.get("columns", []),
        "molecules_smiles": molecules_smiles,
        "decorated_molecules_preview": summary.get("preview", []),
    }


def generate_molecules_for_pocket(
    protein_pdb_path: str,
    center_xyz: list | None = None,
    ref_ligand_path: str | None = None,
    num_samples: int = 10,
) -> dict:
    """Target-aware zero-shot molecular generation with RxnFlow.

    Generates candidate molecules for a protein target using either:
      1. protein file + binding-pocket center coordinates, or
      2. protein file + reference-ligand file.

    Args:
        protein_pdb_path: Target protein structure (.sdf / .mol2 / .pdb).
        center_xyz:        [x, y, z] pocket center (optional if ref_ligand_path is given).
        ref_ligand_path:   Reference ligand structure (optional if center_xyz is given).
        num_samples:       Molecules to generate (default 10).

    Returns:
        On success:
          {
            "success": True,
            "generated_count": int,                        # number of molecules generated
            "sampling_time_sec": float,                    # time spent sampling
            "full_results_csv_path": str,                  # path to the full results CSV
            "top_molecules_preview": [                     # preview of top molecules
              {"smiles": str, "qed": float, "proxy_score": float},
              ...
            ],
          }
        On error:
          {"success": False, "error": str}
    """
    if err := require_pocket_definition(protein_pdb_path, center_xyz, ref_ligand_path):
        return {"success": False, "error": err}

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
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    results_data = result.get("results", {})

    return {
        "success": True,
        "generated_count": summary.get("generated_count"),
        "sampling_time_sec": summary.get("sampling_time_sec"),
        "full_results_csv_path": summary.get("output_file"),
        "top_molecules_preview": results_data.get("generated_preview", []),
    }


def generate_molecules_reinvent4_denovo(num_variants: int = 100) -> dict:
    """Generate completely novel molecules from scratch using the REINVENT4 de novo
    prior model.

    No input scaffold is needed. Suitable for broad chemical-space exploration.

    Args:
        num_variants: Number of molecules to generate (default 100).

    Returns:
        On success:
          {
            "success": True,
            "mode": "de_novo",                     # generation mode
            "requested_variants": int,             # requested molecule count
            "generated_count": int,                # actual unique molecules generated
            "molecules_smiles": [str, ...],        # list of SMILES strings
          }
        On error:
          {"success": False, "error": str}
    """
    result = run_tool(
        "reinvent4", {"num_variants": num_variants}, action="de_novo", timeout_mins=10
    )
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    molecules_data = result.get("results", {}).get("molecules", [])
    smiles_list = [mol["smiles"] for mol in molecules_data if mol.get("smiles")]

    return {
        "success": True,
        "mode": "de_novo",
        "requested_variants": num_variants,
        "generated_count": summary.get("generated_count", len(smiles_list)),
        "molecules_smiles": smiles_list,
    }


def generate_molecules_reinvent4_libinvent(smiles: str, num_variants: int = 50) -> dict:
    """Decorate a chemical scaffold by generating R-group variants at [*] attachment
    points using the REINVENT4 LibInvent model.

    The input MUST be a scaffold SMILES containing at least one [*] wildcard.
    Does NOT support '@@' stereochemistry — use mol2mol mode for chiral molecules.

    Args:
        smiles:        Scaffold SMILES with [*] attachment points.
        num_variants:  Variants to generate (default 50).

    Returns:
        On success:
          {
            "success": True,
            "mode": "libinvent",                   # generation mode
            "input_scaffold": str,                 # echoed scaffold SMILES
            "requested_variants": int,             # requested variant count
            "generated_count": int,                # actual unique molecules generated
            "molecules_smiles": [str, ...],        # list of SMILES strings
          }
        On error:
          {"success": False, "error": str}
    """
    if err := require_attachment_point(smiles):
        return {"success": False, "error": err}
    if err := reject_chiral(smiles):
        return {"success": False, "error": err}

    result = run_tool(
        "reinvent4",
        {"smiles_list": [smiles], "num_variants": num_variants},
        action="libinvent",
        timeout_mins=10,
    )
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    molecules_data = result.get("results", {}).get("molecules", [])
    smiles_list = [mol["smiles"] for mol in molecules_data if mol.get("smiles")]

    return {
        "success": True,
        "mode": "libinvent",
        "input_scaffold": smiles,
        "requested_variants": num_variants,
        "generated_count": summary.get("generated_count", len(smiles_list)),
        "molecules_smiles": smiles_list,
    }


def generate_molecules_reinvent4_mol2mol(
    smiles: str,
    num_variants: int = 50,
    strategy: str = "beamsearch",
    temperature: float = 1.0,
) -> dict:
    """Generate structural analogs of a reference molecule while preserving
    stereochemistry using the REINVENT4 Mol2Mol model.

    Input should be a complete SMILES string (supports '@@' chirality).
    Does NOT support [*] wildcards — use libinvent for scaffold decoration.

    Args:
        smiles:       Complete reference-molecule SMILES.
        num_variants: Analogs to generate (default 50).
        strategy:     'beamsearch' or 'multinomial' (default beamsearch).
        temperature:  Sampling temperature (default 1.0).

    Returns:
        On success:
          {
            "success": True,
            "mode": "mol2mol",                     # generation mode
            "input_smiles": str,                   # echoed reference SMILES
            "strategy": str,                       # sampling strategy used
            "temperature": float,                  # sampling temperature used
            "requested_variants": int,             # requested analog count
            "generated_count": int,                # actual unique molecules generated
            "molecules_smiles": [str, ...],        # list of SMILES strings
          }
        On error:
          {"success": False, "error": str}
    """
    if err := valid_complete_molecule_smiles(smiles):
        return {"success": False, "error": err}

    payload = {
        "smiles_list": [smiles],
        "num_variants": num_variants,
        "strategy": strategy,
        "temperature": temperature,
    }
    result = run_tool("reinvent4", payload, action="mol2mol", timeout_mins=10)
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    molecules_data = result.get("results", {}).get("molecules", [])
    smiles_out = [mol["smiles"] for mol in molecules_data if mol.get("smiles")]

    return {
        "success": True,
        "mode": "mol2mol",
        "input_smiles": smiles,
        "strategy": strategy,
        "temperature": temperature,
        "requested_variants": num_variants,
        "generated_count": summary.get("generated_count", len(smiles_out)),
        "molecules_smiles": smiles_out,
    }
