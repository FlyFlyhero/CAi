"""Small SMILES input validators used by multiple tool wrappers."""

from __future__ import annotations
import os
from Bio.PDB import PDBParser, PDBIO, Select
import subprocess


def require_attachment_point(smiles: str) -> str | None:
    """Return an error message if the scaffold has no '*' attachment point."""
    if not smiles or "*" not in smiles:
        return (
            "The input scaffold SMILES must contain at least one '*' "
            "character as the growth/attachment point."
        )
    return None


def reject_chiral(smiles: str) -> str | None:
    """Return an error message if the SMILES contains '@@' stereochemistry."""
    if smiles and "@@" in smiles:
        return (
            "The input scaffold SMILES must not contain '@@' stereochemistry "
            "(use the mol2mol tool for chiral molecules)."
        )
    return None


def non_empty_smiles(smiles: str) -> str | None:
    """Return an error message if the SMILES is empty / whitespace."""
    if not smiles or not smiles.strip():
        return "A non-empty SMILES string is required."
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