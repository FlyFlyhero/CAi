#!/usr/bin/env python3
"""Analyze completed ABFE calculations.

Scans for bar.xvg files in project directories, computes DG_bind.

Params:
    project_dirs: List of ligand project directories to analyze.
    search_dir:   Directory to scan for ligand_*/ subdirs (alternative to project_dirs).
    restraint_correction_kj: Restraint correction in kJ/mol (default 0).
    standard_state_correction_kj: Standard-state correction in kJ/mol (default 0).
"""
import json
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))
from _lib import analyze_all_results, RT_KJ_MOL, KJ_TO_KCAL  # noqa


def main():
    try:
        with open("params.json", "r", encoding="utf-8") as f:
            params = json.load(f)

        project_dirs = params.get("project_dirs", [])
        search_dir = params.get("search_dir", None)
        restraint_kj = float(params.get("restraint_correction_kj", 0.0))
        ss_kj = float(params.get("standard_state_correction_kj", 0.0))

        search_dirs = []
        if project_dirs:
            search_dirs = [Path(d).resolve() for d in project_dirs]
        if search_dir:
            sd = Path(search_dir).resolve()
            if sd.exists():
                search_dirs.append(sd)
        if not search_dirs:
            raise ValueError("Provide 'project_dirs' (list) or 'search_dir' to analyze.")

        results = analyze_all_results(search_dirs, restraint_kj, ss_kj)

        # Compute summary
        completed = [r for r in results if r["status"] == "completed"]
        dg_values = [r["dg_bind_kj_mol"] for r in completed if r["dg_bind_kj_mol"] is not None]

        summary = {
            "total": len(results),
            "completed": len(completed),
            "incomplete": len(results) - len(completed),
        }
        if dg_values:
            summary["min_dg_bind_kj_mol"] = round(min(dg_values), 3)
            summary["max_dg_bind_kj_mol"] = round(max(dg_values), 3)
            summary["mean_dg_bind_kj_mol"] = round(sum(dg_values) / len(dg_values), 3)
            summary["min_dg_bind_kcal_mol"] = round(min(dg_values) * KJ_TO_KCAL, 3)
            summary["max_dg_bind_kcal_mol"] = round(max(dg_values) * KJ_TO_KCAL, 3)
            summary["mean_dg_bind_kcal_mol"] = round(sum(dg_values) / len(dg_values) * KJ_TO_KCAL, 3)

        output = {
            "success": True,
            "summary": summary,
            "results": results,
        }

    except Exception as e:
        import traceback
        output = {"success": False, "error": str(e), "traceback": traceback.format_exc()}

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
