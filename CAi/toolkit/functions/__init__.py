"""Agent-facing tool functions.

Two submodules:
    generation — molecule generation tools (6)
    evaluation — molecule property / binding evaluation tools (4)

All functions take agent-friendly arguments and return a JSON string so
the REPL can dump the result cleanly.
"""

from .evaluation import (
    calculate_scscore,
    perform_molecular_docking_vina,
    predict_antibacterial_pmic,
    predict_molecule_toxicity,
)
from .generation import (
    generate_libinvent_decorations,
    generate_molecules_for_pocket,
    generate_molecules_reinvent4_denovo,
    generate_molecules_reinvent4_libinvent,
    generate_molecules_reinvent4_mol2mol,
    generate_scaffold_analogs,
)

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
]
