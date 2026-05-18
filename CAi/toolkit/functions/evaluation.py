"""Molecule evaluation tools.

Covers synthesizability (SCScore), toxicity, antibacterial potency (pMIC),
and docking-based binding affinity (AutoDock Vina).
"""

from __future__ import annotations

import base64
import os

from .._validators import valid_center_xyz, valid_complete_molecule_smiles, valid_existing_file
from ..client import run_tool


def calculate_scscore(
    smiles: str | None = None,
    smiles_list: list[str] | None = None,
    model_type: str = "1024bool",
) -> dict:
    """Estimate synthetic accessibility via the SCScore model.

    SCScore ranges roughly 1 (easy) to 5 (very hard to synthesize).

    Args:
        smiles:      Single SMILES string (convenience; turns into a 1-item list).
        smiles_list: Batch of SMILES.
        model_type:  Fingerprint model. Default '1024bool'.

    Returns:
        On success:
          {
            "success": True,
            "summary": {
              "total": int,
              "successful": int,
              "failed": int,
              "model": str,
              "avg_scscore": float,
              "min_scscore": float,
              "max_scscore": float,
              "median_scscore": float,
            },
            "results": [
              {
                "index": int,
                "input_smiles": str,
                "canonical_smiles": str,
                "scscore": float,
                "interpretation": str,
              },
              ...
            ],
            "errors": null | [{...}],
          }
        On error:
          {"success": False, "error": str}
    """
    if smiles:
        smiles_list = [smiles]
    if not smiles_list:
        return {"success": False, "error": "smiles or smiles_list must be provided"}

    return run_tool(
        "scscore",
        {"smiles_list": smiles_list, "model_type": model_type},
    )


def predict_molecule_toxicity(smiles: str) -> dict:
    """Predict hepatotoxicity (HepG2 from toxcast) with SHAP substructure contributions.

    If the server returns a visualization image, it is decoded and saved
    locally as 'latest_toxicity_explanation.png' in the agent workspace.

    Args:
        smiles: Complete molecule SMILES (not a scaffold).

    Returns:
        On success:
          {
            "success": True,
            "verdict": "Toxic" | "Non-Toxic",
            "toxicity_probability": float,        # 0.0 – 1.0
            "is_toxic": bool,                     # probability > 0.5
            "structural_explanation": [
              {"fragment": str, "contribution": float, ...},
              ...
            ],
            "image_saved_at": str | None,         # absolute path to saved PNG
            "vision_prompt": str | None,          # hint to open the image
          }
        On error:
          {"success": False, "error": str}
    """
    if err := valid_complete_molecule_smiles(smiles):
        return {"success": False, "error": err}

    result = run_tool("toxicity", {"smiles": smiles})
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    results_data = result.get("results", {})

    agent_response = {
        "success": True,
        "verdict": "Toxic" if summary.get("is_toxic") else "Non-Toxic",
        "toxicity_probability": summary.get("toxicity_probability"),
        "is_toxic": summary.get("is_toxic"),
        "structural_explanation": results_data.get("interpretation", []),
    }

    image_base64 = results_data.get("image_base64")
    if image_base64:
        local_filename = "latest_toxicity_explanation.png"
        with open(local_filename, "wb") as f:
            f.write(base64.b64decode(image_base64))
        agent_response["image_saved_at"] = os.path.abspath(local_filename)
        agent_response["vision_prompt"] = (
            "The structural interpretation image has been saved locally. "
            "Open it to inspect the toxic fragments."
        )
    else:
        agent_response["image_saved_at"] = None
        agent_response["vision_prompt"] = None

    return agent_response


def predict_antibacterial_pmic(smiles: str) -> dict:
    """Predict antibacterial potency of a complete molecule (Chemprop MPNN, pMIC).

    Higher pMIC and lower MIC_uM mean stronger activity. A typical active
    threshold is pMIC > 5.0 (MIC < 10 uM).

    Args:
        smiles: Complete molecule SMILES.

    Returns:
        On success:
          {
            "success": True,
            "smiles": str,                  # input SMILES echoed back
            "pMIC_value": float,            # predicted pMIC
            "estimated_MIC_uM": float,      # MIC in uM = 10^(6 - pMIC)
            "interpretation": str,          # human-readable guidance
          }
        On error:
          {"success": False, "error": str}
    """
    if err := valid_complete_molecule_smiles(smiles):
        return {"success": False, "error": err}

    result = run_tool("pmic", {"smiles": smiles})
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    results_data = result.get("results", {})

    return {
        "success": True,
        "smiles": results_data.get("smiles", smiles),
        "pMIC_value": summary.get("pMIC_value"),
        "estimated_MIC_uM": summary.get("estimated_MIC_uM"),
        "interpretation": (
            "Higher pMIC means stronger activity. Typical active threshold: "
            "pMIC > 5.0 (MIC < 10 uM)."
        ),
    }


def perform_molecular_docking_vina(
    receptor_pdbqt_path: str,
    ligand_pdbqt_path: str,
    center_xyz: list,
    box_size_xyz: list,
    exhaustiveness: int = 32,
) -> dict:
    """Dock a ligand into a receptor with AutoDock Vina.

    Accepts receptor / ligand in .pdbqt, .pdb, or .sdf. Non-pdbqt inputs
    are auto-converted by the server.

    Args:
        receptor_pdbqt_path: Path to receptor file.
        ligand_pdbqt_path:   Path to ligand file.
        center_xyz:          Docking box center [x, y, z] in Angstrom.
        box_size_xyz:        Docking box dimensions [x, y, z] in Angstrom.
        exhaustiveness:      Vina exhaustiveness (default 32).

    Returns:
        On success:
          {
            "success": True,
            "best_docking_score_kcal_mol": float | None,   # more negative = stronger binding
            "score_before_minimization_kcal_mol": float,   # initial pose score
            "score_after_minimization_kcal_mol": float,    # after local optimization
            "docked_poses_file_path": str,                 # path to docked poses (.pdbqt)
            "minimized_pose_file_path": str,               # path to minimized pose (.pdbqt)
            "interpretation": str,                         # human-readable guidance
          }
        On error:
          {"success": False, "error": str}
    """
    if err := valid_existing_file(receptor_pdbqt_path, field_name="receptor_pdbqt_path"):
        return {"success": False, "error": err}
    if err := valid_existing_file(ligand_pdbqt_path, field_name="ligand_pdbqt_path"):
        return {"success": False, "error": err}
    if err := valid_center_xyz(center_xyz):
        return {"success": False, "error": err}

    payload = {
        "receptor_file": receptor_pdbqt_path,
        "ligand_file": ligand_pdbqt_path,
        "center": center_xyz,
        "box_size": box_size_xyz,
        "exhaustiveness": exhaustiveness,
        "n_poses": 20,
    }
    result = run_tool("vina", payload, timeout_mins=20)
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    results_data = result.get("results", {})

    return {
        "success": True,
        "best_docking_score_kcal_mol": summary.get("best_docking_score"),
        "score_before_minimization_kcal_mol": results_data.get("score_before_minimization"),
        "score_after_minimization_kcal_mol": summary.get("score_after_minimization"),
        "docked_poses_file_path": results_data.get("docked_poses_file"),
        "minimized_pose_file_path": results_data.get("minimized_pose_file"),
        "interpretation": "More negative scores indicate stronger binding affinity.",
    }
