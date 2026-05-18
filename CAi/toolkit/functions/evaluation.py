"""Molecule evaluation tools.

Covers synthesizability (SCScore), toxicity, antibacterial potency (pMIC),
and docking-based binding affinity (AutoDock Vina).
"""

from __future__ import annotations

import base64
import json
import os

from ..client import run_tool

from .._validators import pdb_to_pdbqt_check

def calculate_scscore(
    smiles: str | None = None,
    smiles_list: list[str] | None = None,
    model_type: str = "1024bool",
) -> dict:
    """
    Estimate synthetic accessibility via the SCScore model.

    SCScore ranges roughly 1 (easy) to 5 (very hard to synthesize).

    Args:
        smiles:       Single SMILES string (convenience; turns into a 1-item list).
        smiles_list:  Batch of SMILES.
        model_type:   Fingerprint model. Default '1024bool'.

    Returns:
        Dictionary with status and results.
        Success: {"success": True, "summary": {...}, "results": [...], "errors": None}
        Error:   {"success": False, "error": str}
    """
    if smiles:
        smiles_list = [smiles]
    if not smiles_list:
        return {"success": False, "error": "smiles or smiles_list must be provided"}

    result = run_tool(
        "scscore",
        {"smiles_list": smiles_list, "model_type": model_type},
    )
    # 假设 run_tool 本身已经返回了包含状态的字典，直接透传
    return result


def predict_molecule_toxicity(smiles: str) -> dict:
    """
    Predict hepatotoxicity (HepG2 from toxcast) of a complete molecule and
    return a SHAP-style substructure contribution breakdown.

    If the server returns a visualization image, it is decoded and saved
    locally as 'latest_toxicity_explanation.png' (relative to the agent's
    current working directory — i.e. the agent workspace).

    Args:
        smiles: Complete molecule SMILES (not a scaffold).

    Returns:
        Dictionary with success status, verdict, probability, and explanation.
    """
    result = run_tool("toxicity", {"smiles": smiles})
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    results_data = result.get("results", {})

    agent_response = {
        "success": True,  # 明确展示成功状态
        "verdict": "Toxic" if summary.get("is_toxic") else "Non-Toxic",
        "toxicity_probability": summary.get("toxicity_probability"),
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

    return agent_response


def predict_antibacterial_pmic(smiles: str) -> dict:
    """
    Predict antibacterial potency of a complete molecule (Chemprop MPNN, pMIC).

    Higher pMIC and lower MIC_uM mean stronger activity. A typical active
    threshold is pMIC > 5.0 (MIC < 10 µM).

    Args:
        smiles: Complete molecule SMILES.

    Returns:
        Dictionary with success status, pMIC_value, and estimated_MIC_uM.
    """
    result = run_tool("pmic", {"smiles": smiles})
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    return {
        "success": True,
        "status": "success",
        "pMIC_value": summary.get("pMIC_value"),
        "estimated_MIC_uM": summary.get("estimated_MIC_uM"),
        "interpretation": (
            "Higher pMIC means stronger activity. Typical active threshold: "
            "pMIC > 5.0 (MIC < 10 µM)."
        ),
    }


def perform_molecular_docking_vina(
    receptor_pdbqt_path: str,
    ligand_pdbqt_path: str,
    center_xyz: list,
    box_size_xyz: list,
    exhaustiveness: int = 32,
) -> dict:
    """
    Dock a ligand into a receptor with AutoDock Vina.

    Accepts receptor / ligand in .pdbqt, .pdb, or .sdf. Non-pdbqt inputs
    are auto-converted by the server. Returns docking scores and the
    paths to the output pose files.

    Args:
        receptor_pdbqt_path: Path to receptor file.
        ligand_pdbqt_path:   Path to ligand file.
        center_xyz:          Docking box center [x, y, z].
        box_size_xyz:        Docking box dimensions (Å).
        exhaustiveness:      Vina exhaustiveness (default 32).

    Returns:
        Dictionary containing binding scores. More negative scores indicate stronger binding.
    """
    # add pdb file check 
    if err := pdb_to_pdbqt_check(input_pdb_file, output_pdbqt_file):
        print(json.dumps({"error": err}, ensure_ascii=False))
    else:
        print(json.dumps({"success": f"pdbqt 文件生成成功: {output_pdbqt_file}"}, ensure_ascii=False))
        
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
        return json.dumps({"error": result["error"]})

    summary = result.get("summary", {})
    results_data = result.get("results", {})

    return {
        "success": True,
        "status": "success",
        "best_docking_score_kcal_mol": summary.get("best_docking_score"),
        "minimized_score_kcal_mol": summary.get("score_after_minimization"),
        "docked_poses_file_path": results_data.get("docked_poses_file"),
        "minimized_pose_file_path": results_data.get("minimized_pose_file"),
        "interpretation": "More negative scores indicate stronger binding affinity.",
    }
