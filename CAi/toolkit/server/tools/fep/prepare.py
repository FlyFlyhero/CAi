#!/usr/bin/env python3
"""Prepare a single ligand ABFE project.

Params:
    smiles, ligand_id, charge, protein, pocket_center,
    box_distance, production_steps, grompp_maxwarn,
    mode (smoke/partial/full), lambda_indices, gpus, workdir
"""
import json, sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))
from _lib import *  # noqa


def main():
    try:
        with open("params.json", "r", encoding="utf-8") as f:
            params = json.load(f)

        smiles = params["smiles"]
        ligand_id = params.get("ligand_id", "ligand_000")
        charge = params.get("charge", 0)
        protein_path = params.get("protein", "")
        pocket_center_str = params.get("pocket_center", None)
        box_distance = float(params.get("box_distance", 1.2))
        production_steps = int(params.get("production_steps", 250000))
        grompp_maxwarn = int(params.get("grompp_maxwarn", 3))
        mode = params.get("mode", "full")
        lambda_indices_str = params.get("lambda_indices", None)
        gpus = params.get("gpus", None)
        workdir = Path(params.get("workdir", "."))
        pose_sdf = params.get("pose_sdf", None)
        allow_unposed = params.get("allow_unposed", False)
        allow_missing = params.get("allow_missing_protein_atoms", False)

        # Determine pocket center
        pocket_center = parse_center(pocket_center_str)

        # Lambda indices
        if mode == "smoke":
            lambda_indices = [0]
            production_steps = min(production_steps, 1000)
            grompp_maxwarn = max(grompp_maxwarn, 3)
            box_distance = max(box_distance, 1.5)
            _log("Smoke test: lambda_00, 1000 steps")
        elif lambda_indices_str:
            lambda_indices = parse_lambda_indices(lambda_indices_str)
        else:
            lambda_indices = None

        gpu = bool(gpus)

        # Validate protein
        if not protein_path:
            raise ValueError("Parameter 'protein' (path to PDB file) is required.")
        protein = Path(protein_path).resolve()
        if not protein.exists():
            raise FileNotFoundError(f"Protein file not found: {protein}")

        # Create ligand project
        project = workdir / ligand_id
        project.mkdir(parents=True, exist_ok=True)
        _log(f"Project directory: {project}")

        # 1. Clean protein PDB
        clean_pdb = project / "protein_clean.pdb"
        clean_protein_pdb(protein, clean_pdb)

        # 2. Detect pocket center
        if not pocket_center:
            pocket_center = detect_pocket_center(clean_pdb)
            if pocket_center:
                _log(f"Detected pocket center: {pocket_center}")
            elif not allow_unposed:
                raise RuntimeError(
                    "No pocket center detected in protein PDB. "
                    "Provide pocket_center parameter, or set allow_unposed=True for smoke tests."
                )

        # 3. Generate ligand 3D
        ligand_dir = project / "ligand"
        ligand_dir.mkdir(exist_ok=True)
        pose_path = Path(pose_sdf).resolve() if pose_sdf else None
        sdf = generate_ligand_3d(smiles, "LIG", ligand_dir, pocket_center, pose_path)
        _log(f"Ligand 3D generated: {sdf}")

        # 4. Parameterize ligand (ACPYPE)
        ligand_itp, ligand_gro = parameterize_ligand(sdf, ligand_dir, charge)
        _log(f"Ligand parameterized: {ligand_itp}")

        # 5. Protein topology
        protein_gro, protein_top = prepare_protein_topology(clean_pdb, project, allow_missing)
        _log("Protein topology prepared")

        # 6. System boxes
        complex_gro, complex_top, solvent_gro, solvent_top = prepare_system_boxes(
            project, protein_gro, protein_top, ligand_gro, ligand_itp, box_distance
        )
        _log("Complex and solvent systems built")

        # 7. Prepare legs (write MDP + run scripts, no execution)
        prepare_leg(
            project / "complex", complex_gro, complex_top, ligand_itp,
            nsteps=production_steps, lambda_indices=lambda_indices,
            grompp_maxwarn=grompp_maxwarn, execute=False, gpu=gpu, gpus=gpus,
        )
        prepare_leg(
            project / "solvent", solvent_gro, solvent_top, ligand_itp,
            nsteps=production_steps, lambda_indices=lambda_indices,
            grompp_maxwarn=grompp_maxwarn, execute=False, gpu=gpu, gpus=gpus,
        )
        _log("Leg inputs prepared (EM/NVT/NPT/production MDP + run_leg.sh)")

        # Save metadata
        meta = {
            "ligand_id": ligand_id,
            "smiles": smiles,
            "charge": charge,
            "mode": mode,
            "production_steps": production_steps,
            "lambda_indices": lambda_indices,
            "gpus": gpus,
            "pocket_center": list(pocket_center) if pocket_center else None,
            "gpu": gpu,
        }
        (project / "fep_params.json").write_text(json.dumps(meta, indent=2))

        result = {
            "success": True,
            "ligand_id": ligand_id,
            "smiles": smiles,
            "project_dir": str(project),
            "mode": mode,
            "production_steps": production_steps,
            "lambda_windows": len(lambda_indices) if lambda_indices else 18,
            "message": (
                f"ABFE project prepared for {ligand_id}. "
                f"Run with action='run' to execute the simulation."
            ),
        }

    except Exception as e:
        import traceback
        result = {"success": False, "error": str(e), "traceback": traceback.format_exc()}

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
