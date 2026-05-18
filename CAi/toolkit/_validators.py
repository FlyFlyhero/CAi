"""Small SMILES input validators used by multiple tool wrappers."""

from __future__ import annotations
import os
from collections.abc import Sequence
from pathlib import Path

from Bio.PDB import PDBParser, PDBIO, Select
import subprocess


# -----------------------------
# 1. Basic SMILES validators
# -----------------------------

def non_empty_smiles(smiles: str | None) -> str | None:
    """
    Return an error message if the SMILES is empty / whitespace.
    Success returns None.
    """
    if smiles is None:
        return "A non-empty SMILES string is required, but got None."

    if not isinstance(smiles, str):
        return f"SMILES must be a string, but got {type(smiles).__name__}."

    if not smiles.strip():
        return "A non-empty SMILES string is required."

    return None


def reject_chiral(smiles: str | None) -> str | None:
    """
    Reject SMILES containing '@' or '@@' stereochemistry markers.

    Some scaffold-decoration models, especially LibInvent-style workflows,
    may fail or behave unpredictably with explicit chirality.
    """
    if err := non_empty_smiles(smiles):
        return err

    assert smiles is not None

    if "@@" in smiles:
        return (
            "This tool does not support '@@' stereochemistry. "
            "Please use a non-chiral scaffold or use Mol2Mol mode for complete chiral molecules."
        )

    if "@" in smiles:
        return (
            "This tool does not support '@' stereochemistry. "
            "Please use a non-chiral scaffold or use Mol2Mol mode for complete chiral molecules."
        )

    return None


# -----------------------------
# 2. Scaffold attachment validators
# -----------------------------

def require_attachment_point(smiles: str | None) -> str | None:
    """
    Require at least one scaffold attachment point.

    Accepts:
    - '*'
    - '[*]'
    - '[*:1]', '[*:2]', etc.

    This is suitable for scaffold-decoration tools such as:
    - RNN scaffold generation
    - LibInvent
    - REINVENT4 LibInvent
    """
    if err := non_empty_smiles(smiles):
        return err

    assert smiles is not None

    has_attachment = (
        "*" in smiles
        or "[*]" in smiles
        or "[*:" in smiles
    )

    if not has_attachment:
        return (
            "A scaffold SMILES with an explicit attachment point is required. "
            "Use '*', '[*]', or '[*:1]'. Example: 'c1ccccc1*' or 'c1ccccc1[*:1]'."
        )

    return None


def reject_attachment_point(smiles: str | None) -> str | None:
    """
    Reject wildcard attachment points.

    This is suitable for Mol2Mol mode, where the input should be a complete molecule,
    not a scaffold template.
    """
    if err := non_empty_smiles(smiles):
        return err

    assert smiles is not None

    if "*" in smiles or "[*]" in smiles or "[*:" in smiles:
        return (
            "Mol2Mol requires a complete molecule SMILES, but the input contains an attachment point. "
            "Use LibInvent/scaffold-decoration mode for scaffold SMILES with '*' or '[*]'."
        )

    return None


def require_complete_molecule_smiles(smiles: str | None) -> str | None:
    """
    Validate that the input looks like a complete molecule SMILES.

    This is intended for Mol2Mol generation.
    It allows chirality '@' / '@@', but rejects scaffold wildcards.
    """
    if err := non_empty_smiles(smiles):
        return err

    if err := reject_attachment_point(smiles):
        return err

    return None

def valid_complete_molecule_smiles(smiles: str | None) -> str | None:
    """
    Validate complete molecule SMILES using RDKit.

    Requires:
        pip install rdkit
    """
    if err := require_complete_molecule_smiles(smiles):
        return err

    try:
        from rdkit import Chem
    except ImportError:
        return (
            "RDKit is not installed, so SMILES validity cannot be checked. "
            "Install RDKit or use require_complete_molecule_smiles() for lightweight validation."
        )

    assert smiles is not None

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return f"Invalid SMILES string: {smiles}"

    if mol.GetNumAtoms() == 0:
        return "Invalid SMILES: parsed molecule contains zero atoms."

    return None

# -----------------------------
# 3. Pocket / protein validators
# -----------------------------

def valid_existing_file(
    file_path: str | None,
    allowed_suffixes: Sequence[str] | None = None,
    field_name: str = "file_path",
) -> str | None:
    """
    Validate that a file path exists and optionally has an allowed suffix.
    """
    if file_path is None:
        return f"{field_name} is required, but got None."

    if not isinstance(file_path, str):
        return f"{field_name} must be a string path, but got {type(file_path).__name__}."

    if not file_path.strip():
        return f"{field_name} is required and cannot be empty."

    path = Path(file_path)

    if not path.exists():
        return f"{field_name} does not exist: {file_path}"

    if not path.is_file():
        return f"{field_name} must be a file, but got a directory or non-file path: {file_path}"

    if allowed_suffixes:
        suffix = path.suffix.lower()
        allowed = {s.lower() for s in allowed_suffixes}
        if suffix not in allowed:
            return (
                f"{field_name} has unsupported file format '{suffix}'. "
                f"Allowed formats: {sorted(allowed)}. Path: {file_path}"
            )

    return None


def valid_center_xyz(center_xyz: list | tuple | None) -> str | None:
    """
    Validate docking / pocket center coordinates.

    Expected:
        [x, y, z]
    where each value is int or float.
    """
    if center_xyz is None:
        return "center_xyz is required when ref_ligand_path is not provided."

    if not isinstance(center_xyz, (list, tuple)):
        return f"center_xyz must be a list or tuple of three numbers, but got {type(center_xyz).__name__}."

    if len(center_xyz) != 3:
        return f"center_xyz must contain exactly 3 values [x, y, z], but got {len(center_xyz)} values."

    for i, value in enumerate(center_xyz):
        if not isinstance(value, (int, float)):
            return (
                f"center_xyz[{i}] must be a number, but got {type(value).__name__}: {value}"
            )

    return None


def require_pocket_definition(
    protein_pdb_path: str | None,
    center_xyz: list | tuple | None = None,
    ref_ligand_path: str | None = None,
) -> str | None:
    """
    Validate RxnFlow / pocket-aware generation input.

    Requires:
    - protein file exists
    - either center_xyz or ref_ligand_path is provided
    - if center_xyz is provided, it must be [x, y, z]
    - if ref_ligand_path is provided, it must exist
    """
    if err := valid_existing_file(
        protein_pdb_path,
        allowed_suffixes=[".pdb", ".sdf", ".mol2"],
        field_name="protein_pdb_path",
    ):
        return err

    if center_xyz is None and not ref_ligand_path:
        return (
            "Pocket definition is missing. Provide either center_xyz=[x, y, z] "
            "or ref_ligand_path to define the binding pocket."
        )

    if center_xyz is not None:
        if err := valid_center_xyz(center_xyz):
            return err

    if ref_ligand_path:
        if err := valid_existing_file(
            ref_ligand_path,
            allowed_suffixes=[".sdf", ".mol2", ".pdb", ".pdbqt"],
            field_name="ref_ligand_path",
        ):
            return err

    return None


class ProteinSelect(Select):
    """Keep only protein chains, remove small molecules, water, ions, etc."""
    def accept_residue(self, residue):
        return residue.get_resname() in [
            'ALA','ARG','ASN','ASP','CYS','GLN','GLU','GLY','HIS',
            'ILE','LEU','LYS','MET','PHE','PRO','SER','THR','TRP','TYR','VAL'
        ]


def pdb_to_pdbqt_check(input_pdb: str, output_pdbqt: str, prepare_receptor_path: str = 'prepare_receptor4.py') -> str | None:
    """
    Clean a PDB file and convert it to PDBQT format.
    
    Parameters
    ----------
    input_pdb : str
        Path to the input PDB file.
    output_pdbqt : str
        Path to the output PDBQT file.
    prepare_receptor_path : str, optional
        Path to the prepare_receptor4.py script.
    
    Returns
    -------
    str or None
        None if successful, otherwise an error message string.
    """
    # Check if input file exists
    if not os.path.exists(input_pdb):
        return f"[ERROR] PDB file does not exist: {input_pdb}"
    
    # Step 1: Clean non-protein molecules
    clean_pdb = input_pdb.replace('.pdb', '_clean.pdb')
    try:
        parser = PDBParser(QUIET=True)
        structure = parser.get_structure('protein', input_pdb)
        io = PDBIO()
        io.set_structure(structure)
        io.save(clean_pdb, ProteinSelect())
    except Exception as e:
        return f"[ERROR] PDB cleaning failed: {e}"
    
    if not os.path.exists(clean_pdb):
        return f"[ERROR] Cleaned PDB file not generated: {clean_pdb}"

    # Step 2: Convert to PDBQT using AutoDockTools
    try:
        cmd = [
            "python", prepare_receptor_path,
            "-r", clean_pdb,
            "-o", output_pdbqt,
            "-A", "hydrogens"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("[INFO] prepare_receptor4.py output log:")
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        return f"[ERROR] PDBQT conversion failed: {e.stderr}"
    except Exception as e:
        return f"[ERROR] PDBQT conversion unknown error: {e}"

    if not os.path.exists(output_pdbqt):
        return f"[ERROR] PDBQT file not generated: {output_pdbqt}"

    # Success
    return None



# # generate_scaffold_analogs
    # if err := require_attachment_point(smiles):
    #     return json.dumps({"error": err}, ensure_ascii=False)

    # if err := reject_chiral(smiles):
    #     return json.dumps({"error": err}, ensure_ascii=False)
    
# # generate_libinvent_decorations 
    # if err := require_attachment_point(smiles):
    #     return json.dumps({"error": err}, ensure_ascii=False)

    # if err := reject_chiral(smiles):
    #     return json.dumps({"error": err}, ensure_ascii=False)
    
# # generate_molecules_reinvent4_libinvent
    # if err := require_attachment_point(smiles):
    #     return json.dumps({"error": err}, ensure_ascii=False)

    # if err := reject_chiral(smiles):
    #     return json.dumps({"error": err}, ensure_ascii=False)
    
# # generate_molecules_reinvent4_mol2mol
    # if err := valid_complete_molecule_smiles(smiles):
    #     return json.dumps({"error": err}, ensure_ascii=False)
    
# # generate_molecules_for_pocket
    # if err := require_pocket_definition(
    #     protein_pdb_path=protein_pdb_path,
    #     center_xyz=center_xyz,
    #     ref_ligand_path=ref_ligand_path,
    # ):
    #     return json.dumps({"error": err}, ensure_ascii=False)