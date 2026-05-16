"""
CAi Toolkit — drug-discovery tools exposed to the agent.

Layout:
    client.py         HTTP client for the tool server (lower-level)
    functions/        The agent-callable tool functions:
        generation.py   molecule generators (6)
        evaluation.py   molecule evaluators (4)
    skill_helpers.py  get_skill_content / list_available_skills
    server/           FastAPI tool server (separate process)

How to add a new tool:
    1. Put the backend implementation under  toolkit/server/tools/<your_tool>/
       with config.json + run.py (see server/tools/test_tool/README.md).
    2. Add a Python wrapper to  toolkit/functions/generation.py  or  evaluation.py.
       Use `from ..client import run_tool` to call the backend.
    3. Re-export the name below and in functions/__init__.py.
    4. Restart the agent (or call agent.reload_tools()).
"""

from .functions import (
    calculate_scscore,
    generate_libinvent_decorations,
    generate_molecules_for_pocket,
    generate_molecules_reinvent4_denovo,
    generate_molecules_reinvent4_libinvent,
    generate_molecules_reinvent4_mol2mol,
    generate_scaffold_analogs,
    perform_molecular_docking_vina,
    predict_antibacterial_pmic,
    predict_molecule_toxicity,
)
from .skill_helpers import get_skill_content, list_available_skills

__all__ = [
    # evaluation
    "calculate_scscore",
    "perform_molecular_docking_vina",
    "predict_antibacterial_pmic",
    "predict_molecule_toxicity",
    # generation
    "generate_libinvent_decorations",
    "generate_molecules_for_pocket",
    "generate_molecules_reinvent4_denovo",
    "generate_molecules_reinvent4_libinvent",
    "generate_molecules_reinvent4_mol2mol",
    "generate_scaffold_analogs",
    # skill helpers (registered as hidden tools in A1pro)
    "get_skill_content",
    "list_available_skills",
]
