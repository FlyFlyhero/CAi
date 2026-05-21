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
    valid_existing_file,
)
from ..client import run_tool


def run_gromacs_md(
    step: str,
    input_pdb: str | None = None,
    input_gro: str | None = None,
    ff: str = "amber99sb-ildn",
    water: str = "tip3p",
    mode: str = "nvt",
) -> dict:
    """Run a GROMACS molecular dynamics simulation step.

    Supports a full MD workflow: topology preparation → solvation →
    energy minimization → NVT/NPT equilibration → production MD.

    Each step operates in the job sandbox directory. Output files
    (.gro, .xtc, .top, etc.) are written to the sandbox and referenced
    in the result.

    Args:
        step:      MD step to run. One of: "prep", "solvate", "minimize",
                   "equilibrate", "production".
        input_pdb: Input PDB file (required for "prep" step).
        input_gro: Input GRO file (for steps after prep; defaults vary by step).
        ff:        Force field (default "amber99sb-ildn", used in "prep").
        water:     Water model (default "tip3p", used in "prep").
        mode:      Equilibration mode "nvt" or "npt" (used in "equilibrate").

    Returns:
        On success:
          {
            "success": True,
            "step": str,
            "data": { "output_gro": str, "message": str, ... },
          }
        On error:
          {"success": False, "error": str}
    """
    valid_steps = {"prep", "solvate", "minimize", "equilibrate", "production"}
    if step not in valid_steps:
        return {"success": False, "error": f"step must be one of {sorted(valid_steps)}, got '{step}'"}

    if step == "prep" and input_pdb:
        if err := valid_existing_file(input_pdb, field_name="input_pdb"):
            return {"success": False, "error": err}

    payload: dict = {"step": step}
    if input_pdb is not None:
        payload["input_pdb"] = input_pdb
    if input_gro is not None:
        payload["input_gro"] = input_gro
    if step == "prep":
        payload["ff"] = ff
        payload["water"] = water
    if step == "equilibrate":
        payload["mode"] = mode

    # MD production can be slow; allow up to 2 hours
    timeout = 120 if step == "production" else 60
    result = run_tool("gromacs_runner", payload, timeout_mins=timeout)
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    return {
        "success": True,
        "step": step,
        "data": result.get("data", {}),
    }


def generate_molecules_sc2mol(
    scaffolds: list[str],
    num_sample: int | None = None,
    ckpt: str = "sc2mol_smoke/ckpt-9",
    max_len: int = 64,
) -> dict:
    """Generate molecules from scaffold SMILES using the Sc2Mol transformer model.

    Sc2Mol is a scaffold-conditioned molecule generation model based on a
    VAE-transformer architecture. Each input scaffold produces one output molecule.

    Args:
        scaffolds: List of scaffold SMILES strings (e.g., ["c1ccccc1", "C1CCCCC1"]).
        num_sample: Number of scaffolds to use (default: all provided scaffolds).
        ckpt: Checkpoint path relative to Sc2Mol/checkpoints/ (default "sc2mol_smoke/ckpt-9").
        max_len: Maximum SMILES token length (default 64).

    Returns:
        On success:
          {
            "success": True,
            "mode": "scaffold",
            "checkpoint": str,
            "num_scaffolds": int,
            "num_sample_requested": int,
            "num_sample_used": int,
            "results": [
              {"index": int, "input_scaffold": str, "smiles": str, ...},
              ...
            ],
          }
        On error:
          {"success": False, "error": str}
    """
    if not scaffolds:
        return {"success": False, "error": "scaffolds must be a non-empty list of SMILES strings"}

    payload: dict = {
        "scaffolds": scaffolds,
        "ckpt": ckpt,
        "max_len": max_len,
    }
    if num_sample is not None:
        payload["num_sample"] = num_sample

    result = run_tool("sc2mol", payload, timeout_mins=10)
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    return {
        "success": True,
        "mode": "scaffold",
        "checkpoint": summary.get("checkpoint", ckpt),
        "num_scaffolds": summary.get("num_scaffolds"),
        "num_sample_requested": summary.get("num_sample_requested"),
        "num_sample_used": summary.get("num_sample_used"),
        "results": result.get("results", []),
    }


def infer_synthesis_synllama(
    smiles: list[str],
    sample_mode: str = "frozen_only",
    model: str = "91rxns",
    gpus: int = 1,
    max_molecules: int = 5,
) -> dict:
    """Infer synthesis pathways for target molecules using the SynLlama LLM model.

    SynLlama is a language model trained on chemical synthesis data that predicts
    possible synthetic routes for a given target molecule.

    Args:
        smiles: List of target molecule SMILES strings.
        sample_mode: Sampling strategy (default "frozen_only").
            Options: frozen_only, frugal, greedy, low_only, medium_only, high_only.
        model: Model identifier (default "91rxns").
        gpus: Number of GPUs to use (default 1).
        max_molecules: Maximum number of molecules to process (default 5).

    Returns:
        On success:
          {
            "success": True,
            "model": str,
            "sample_mode": str,
            "num_input_smiles": int,
            "results": [
              {"index": int, "smiles": str, "predictions": ...},
              ...
            ],
          }
        On error:
          {"success": False, "error": str}
    """
    if not smiles:
        return {"success": False, "error": "smiles must be a non-empty list of SMILES strings"}

    payload = {
        "smiles": smiles,
        "sample_mode": sample_mode,
        "model": model,
        "gpus": gpus,
        "max_molecules": max_molecules,
    }
    result = run_tool("synllama", payload, timeout_mins=15)
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    return {
        "success": True,
        "model": summary.get("model", model),
        "sample_mode": summary.get("sample_mode", sample_mode),
        "num_input_smiles": summary.get("num_input_smiles"),
        "results": result.get("results", []),
    }


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
