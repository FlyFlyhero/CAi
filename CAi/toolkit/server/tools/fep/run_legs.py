#!/usr/bin/env python3
"""Run prepared ABFE legs for a ligand.

Expects a project directory prepared by prepare.py, containing
complex/ and solvent/ subdirectories with run_leg.sh scripts.

Params:
    project_dir (required): Path to ligand project dir (from prepare.py)
    gpus: GPU IDs, e.g. "0" or "0,1,2"
    parallel_legs: Run complex and solvent legs simultaneously (default True if >=2 GPUs)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))
from _lib import _log, run_cmd


def main():
    try:
        with open("params.json", "r", encoding="utf-8") as f:
            params = json.load(f)

        project_dir = Path(params["project_dir"]).resolve()
        gpus = params.get("gpus", None)
        parallel_legs = params.get("parallel_legs", None)

        if not project_dir.exists():
            raise FileNotFoundError(f"Project directory not found: {project_dir}")

        complex_leg = project_dir / "complex" / "run_leg.sh"
        solvent_leg = project_dir / "solvent" / "run_leg.sh"

        if not complex_leg.exists():
            raise FileNotFoundError(f"Complex leg script not found: {complex_leg}")
        if not solvent_leg.exists():
            raise FileNotFoundError(f"Solvent leg script not found: {solvent_leg}")

        # Determine parallel execution
        gpu_list = gpus.split(",") if gpus and "," in gpus else [gpus] if gpus else []
        if parallel_legs is None:
            parallel_legs = len(gpu_list) >= 2

        if parallel_legs and len(gpu_list) >= 2:
            # Split GPUs: even for complex, odd for solvent
            complex_gpus = ",".join(gpu_list[i] for i in range(0, len(gpu_list), 2))
            solvent_gpus = ",".join(gpu_list[i] for i in range(1, len(gpu_list), 2))
            _log(f"Parallel legs: complex GPU[{complex_gpus}], solvent GPU[{solvent_gpus}]")

            # Write master script
            master = project_dir / "run_abfe.sh"
            master.write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\n"
                f'CWD=$(cd "$(dirname "$0")" && pwd)\n'
                f'(cd "$CWD/complex" && CUDA_VISIBLE_DEVICES="{complex_gpus}" bash run_leg.sh) &\n'
                f'(cd "$CWD/solvent" && CUDA_VISIBLE_DEVICES="{solvent_gpus}" bash run_leg.sh) &\n'
                "wait\n"
                'echo "Both legs complete."\n'
            )
            master.chmod(0o755)
            run_cmd(["bash", str(master)], project_dir)
        else:
            # Sequential: complex then solvent
            env_gpus = gpus or ""
            _log(f"Running complex leg (GPU: {env_gpus})...")
            env = os.environ.copy()
            if env_gpus:
                env["CUDA_VISIBLE_DEVICES"] = env_gpus
            subprocess.run(["bash", str(complex_leg)], cwd=str(complex_leg.parent), env=env, check=True)

            _log(f"Running solvent leg (GPU: {env_gpus})...")
            subprocess.run(["bash", str(solvent_leg)], cwd=str(solvent_leg.parent), env=env, check=True)

        _log("Both legs complete.")

        result = {
            "success": True,
            "project_dir": str(project_dir),
            "message": "ABFE legs completed. Use action='analyze' to extract DG_bind.",
        }

    except Exception as e:
        import traceback
        result = {"success": False, "error": str(e), "traceback": traceback.format_exc()}

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
