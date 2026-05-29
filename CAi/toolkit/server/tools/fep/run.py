#!/usr/bin/env python3
"""Full ABFE pipeline: prepare → run legs → analyze.

This is the default action — one call goes end-to-end.
For finer control, use the individual actions: prepare, run, analyze.

Params (same as prepare.py, plus):
    smiles (required)
    ligand_id
    charge
    protein (required, path to PDB)
    pocket_center ("x,y,z" or auto-detect)
    box_distance
    production_steps
    grompp_maxwarn
    mode (smoke/partial/full)
    lambda_indices
    gpus
    workdir
    pose_sdf (optional, pre-docked ligand pose)
    allow_unposed
    allow_missing_protein_atoms
    restraint_correction_kj
    standard_state_correction_kj
"""
import json
import os
import subprocess
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))
from _lib import (  # noqa
    _log, run_cmd, parse_center, parse_lambda_indices,
    clean_protein_pdb, detect_pocket_center,
    generate_ligand_3d, parameterize_ligand,
    prepare_protein_topology, prepare_system_boxes,
    prepare_leg, analyze_single_result,
)


def main():
    try:
        with open("params.json", "r", encoding="utf-8") as f:
            params = json.load(f)

        smiles = params["smiles"]
        ligand_id = params.get("ligand_id", "ligand_000")
        charge = int(params.get("charge", 0))
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
        allow_unposed = bool(params.get("allow_unposed", False))
        allow_missing = bool(params.get("allow_missing_protein_atoms", False))
        restraint_kj = float(params.get("restraint_correction_kj", 0.0))
        ss_kj = float(params.get("standard_state_correction_kj", 0.0))

        # Resolve pocket center
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

        # Create project
        project = workdir / ligand_id
        project.mkdir(parents=True, exist_ok=True)
        _log(f"Project: {project}")

        # ===== STEP 1: PREPARE =====
        _log("=" * 60)
        _log("STEP 1: Preparing ABFE inputs...")
        _log("=" * 60)

        clean_pdb = project / "protein_clean.pdb"
        clean_protein_pdb(protein, clean_pdb)

        if not pocket_center:
            pocket_center = detect_pocket_center(clean_pdb)
            if pocket_center:
                _log(f"Detected pocket center: {pocket_center}")
            elif not allow_unposed:
                raise RuntimeError(
                    "No pocket center detected. Provide pocket_center or set allow_unposed=True."
                )

        ligand_dir = project / "ligand"
        ligand_dir.mkdir(exist_ok=True)
        pose_path = Path(pose_sdf).resolve() if pose_sdf else None
        sdf = generate_ligand_3d(smiles, "LIG", ligand_dir, pocket_center, pose_path)
        _log(f"Ligand 3D: {sdf}")

        ligand_itp, ligand_gro = parameterize_ligand(sdf, ligand_dir, charge)
        _log(f"Ligand topology: {ligand_itp}")

        protein_gro, protein_top = prepare_protein_topology(clean_pdb, project, allow_missing)
        _log("Protein topology done")

        complex_gro, complex_top, solvent_gro, solvent_top = prepare_system_boxes(
            project, protein_gro, protein_top, ligand_gro, ligand_itp, box_distance
        )
        _log("System boxes built")

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
        _log("Leg scripts prepared")

        # Save metadata
        meta = {
            "ligand_id": ligand_id, "smiles": smiles, "charge": charge,
            "mode": mode, "production_steps": production_steps,
            "lambda_indices": lambda_indices, "gpus": gpus,
            "pocket_center": list(pocket_center) if pocket_center else None,
            "gpu": gpu,
        }
        (project / "fep_params.json").write_text(json.dumps(meta, indent=2))

        # ===== STEP 2: RUN LEGS =====
        _log("=" * 60)
        _log("STEP 2: Running ABFE legs (this takes time)...")
        _log("=" * 60)

        gpu_list = gpus.split(",") if gpus and "," in gpus else [gpus] if gpus else []
        parallel = len(gpu_list) >= 2

        if parallel:
            complex_gpus = ",".join(gpu_list[i] for i in range(0, len(gpu_list), 2))
            solvent_gpus = ",".join(gpu_list[i] for i in range(1, len(gpu_list), 2))
            master = project / "run_abfe.sh"
            master.write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\n"
                f'CWD=$(cd "$(dirname "$0")" && pwd)\n'
                f'(cd "$CWD/complex" && CUDA_VISIBLE_DEVICES="{complex_gpus}" bash run_leg.sh) &\n'
                f'(cd "$CWD/solvent" && CUDA_VISIBLE_DEVICES="{solvent_gpus}" bash run_leg.sh) &\n'
                "wait\n"
            )
            master.chmod(0o755)
            run_cmd(["bash", str(master)], project)
        else:
            env = os.environ.copy()
            if gpus:
                env["CUDA_VISIBLE_DEVICES"] = gpus
            _log("Running complex leg...")
            subprocess.run(
                ["bash", str(project / "complex" / "run_leg.sh")],
                cwd=str(project / "complex"), env=env, check=True,
            )
            _log("Running solvent leg...")
            subprocess.run(
                ["bash", str(project / "solvent" / "run_leg.sh")],
                cwd=str(project / "solvent"), env=env, check=True,
            )

        _log("Both legs complete.")

        # ===== STEP 3: ANALYZE =====
        _log("=" * 60)
        _log("STEP 3: Analyzing results...")
        _log("=" * 60)

        result_data = analyze_single_result(
            project, ligand_id, smiles, None, restraint_kj, ss_kj,
        )

        output = {
            "success": True,
            "ligand_id": ligand_id,
            "smiles": smiles,
            "project_dir": str(project),
            "mode": mode,
            "production_steps": production_steps,
            "lambda_windows": result_data["lambda_windows"],
            "dg_complex_decouple_kj_mol": result_data["dg_complex_decouple_kj_mol"],
            "dg_complex_error_kj_mol": result_data["dg_complex_error_kj_mol"],
            "dg_solvent_decouple_kj_mol": result_data["dg_solvent_decouple_kj_mol"],
            "dg_solvent_error_kj_mol": result_data["dg_solvent_error_kj_mol"],
            "dg_bind_kj_mol": result_data["dg_bind_kj_mol"],
            "dg_bind_error_kj_mol": result_data["dg_bind_error_kj_mol"],
            "dg_bind_kcal_mol": result_data["dg_bind_kcal_mol"],
            "status": result_data["status"],
            "warning": result_data["warning"],
        }

    except Exception as e:
        import traceback
        output = {"success": False, "error": str(e), "traceback": traceback.format_exc()}

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
