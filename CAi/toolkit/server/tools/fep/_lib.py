"""Shared ABFE library — self-contained in tools/fep/.

All MDP templates, structure processing, topology generation, leg preparation,
and BAR analysis logic lives here. Action scripts import from this module.
"""
from __future__ import annotations

import csv
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WATER_AND_BUFFER = {
    "HOH", "WAT", "SOL", "NA", "CL", "K", "MG", "CA", "ZN", "MN",
    "GOL", "MES", "DMS", "SO4", "PO4",
}

RT_KJ_MOL = 8.314e-3 * 300  # 2.4942 kJ/mol at 300K
KJ_TO_KCAL = 1.0 / 4.184

# ---------------------------------------------------------------------------
# MDP templates
# ---------------------------------------------------------------------------

ION_MDP = """\
integrator  = steep
emtol       = 1000.0
emstep      = 0.01
nsteps      = 500
cutoff-scheme = Verlet
nstlist     = 10
rcoulomb    = 1.0
rvdw        = 1.0
coulombtype = PME
pbc         = xyz
"""

EM_MDP = """\
integrator  = steep
emtol       = 1000.0
emstep      = 0.01
nsteps      = 50000
cutoff-scheme = Verlet
nstlist     = 10
rcoulomb    = 1.0
rvdw        = 1.0
coulombtype = PME
pbc         = xyz
"""

NVT_MDP = """\
define      = -DPOSRES
integrator  = md
dt          = 0.002
nsteps      = 25000
nstxout-compressed = 5000
nstenergy   = 1000
nstlog      = 1000
continuation = no
constraint_algorithm = lincs
constraints = h-bonds
cutoff-scheme = Verlet
nstlist     = 40
rlist       = 2.0
rcoulomb    = 1.0
rvdw        = 1.0
coulombtype = PME
tcoupl      = V-rescale
tc-grps     = System
tau_t       = 0.1
ref_t       = 300
pcoupl      = no
pbc         = xyz
gen_vel     = yes
gen_temp    = 300
gen_seed    = -1
"""

NPT_MDP = """\
define      = -DPOSRES
integrator  = md
dt          = 0.002
nsteps      = 50000
nstxout-compressed = 5000
nstenergy   = 1000
nstlog      = 1000
continuation = yes
constraint_algorithm = lincs
constraints = h-bonds
cutoff-scheme = Verlet
nstlist     = 40
rlist       = 2.0
rcoulomb    = 1.0
rvdw        = 1.0
coulombtype = PME
tcoupl      = V-rescale
tc-grps     = System
tau_t       = 0.1
ref_t       = 300
pcoupl      = C-rescale
pcoupltype  = isotropic
tau_p       = 2.0
ref_p       = 1.0
compressibility = 4.5e-5
pbc         = xyz
gen_vel     = no
"""

ABFE_MDP_TEMPLATE = """\
integrator  = md
dt          = 0.002
nsteps      = {nsteps}
nstxout-compressed = 5000
nstenergy   = 1000
nstlog      = 1000
continuation = yes
constraint_algorithm = lincs
constraints = h-bonds
cutoff-scheme = Verlet
nstlist     = 40
rlist       = 2.0
rcoulomb    = 1.0
rvdw        = 1.0
coulombtype = PME
tcoupl      = V-rescale
tc-grps     = System
tau_t       = 0.1
ref_t       = 300
pcoupl      = C-rescale
pcoupltype  = isotropic
tau_p       = 2.0
ref_p       = 1.0
compressibility = 4.5e-5
pbc         = xyz
gen_vel     = no

free_energy              = yes
init-lambda-state        = {lambda_state}
delta-lambda             = 0
calc-lambda-neighbors    = 1
nstdhdl                  = 100
separate-dhdl-file       = yes
couple-moltype           = LIG
couple-lambda0           = vdw-q
couple-lambda1           = none
couple-intramol          = yes
sc-alpha                 = 0.5
sc-power                 = 1
sc-sigma                 = 0.3
coul-lambdas             = {coul_lambdas}
vdw-lambdas              = {vdw_lambdas}
bonded-lambdas           = {bonded_lambdas}
restraint-lambdas        = {restraint_lambdas}
mass-lambdas             = {mass_lambdas}
temperature-lambdas      = {temperature_lambdas}
"""

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

def make_fep_result(ligand_id, smiles, vina_score, workdir,
                    dg_c_kj=None, err_c_kj=None, dg_s_kj=None, err_s_kj=None,
                    dg_bind=None, err_bind=None, status="incomplete",
                    error=None, lambda_windows=0):
    return {
        "ligand_id": ligand_id,
        "smiles": smiles,
        "vina_score": vina_score,
        "workdir": workdir,
        "dg_complex_decouple_kj_mol": dg_c_kj,
        "dg_complex_error_kj_mol": err_c_kj,
        "dg_solvent_decouple_kj_mol": dg_s_kj,
        "dg_solvent_error_kj_mol": err_s_kj,
        "dg_bind_kj_mol": dg_bind,
        "dg_bind_error_kj_mol": err_bind,
        "dg_bind_kcal_mol": round(dg_bind * KJ_TO_KCAL, 3) if dg_bind is not None else None,
        "status": status,
        "error": error,
        "lambda_windows": lambda_windows,
        "warning": "Uncorrected: no restraint or standard-state correction applied.",
    }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(msg: str):
    print(msg, file=sys.stderr)


def run_cmd(cmd: Sequence[str], cwd: Path, input_text: str | None = None,
            check: bool = True, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    _log(f"+ {' '.join(cmd)}  (cwd={cwd})")
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, cwd=str(cwd), input=input_text, text=True, check=check, env=env)


def which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    fallbacks = {
        "gmx": ["/mnt/shared-storage-gpfs2/chenjiangyu-gpfs02/gromacs-2026.2/bin/gmx"],
        "obabel": ["/home/chenjiangyu/miniconda3/envs/fep/bin/obabel"],
        "acpype": ["/home/chenjiangyu/miniconda3/envs/fep/bin/acpype"],
    }
    for candidate in fallbacks.get(name, []):
        if Path(candidate).exists():
            return candidate
    return None


def require(name: str) -> str:
    p = which(name)
    if not p:
        raise RuntimeError(f"Missing required command: {name}")
    return p


def parse_center(text: str | None) -> tuple[float, float, float] | None:
    if not text:
        return None
    parts = [float(x.strip()) for x in text.split(",")]
    if len(parts) != 3:
        raise ValueError('pocket_center must be "x,y,z"')
    return (parts[0], parts[1], parts[2])


def parse_lambda_indices(text: str | None) -> list[int] | None:
    if not text:
        return None
    return [int(x.strip()) for x in text.split(",") if x.strip()]


# ---------------------------------------------------------------------------
# Protein processing
# ---------------------------------------------------------------------------

def infer_pdb_element(atom_name: str, atom_type: str = "") -> str:
    cleaned_type = "".join(ch for ch in atom_type.strip() if ch.isalpha())
    if cleaned_type:
        if len(cleaned_type) >= 2 and cleaned_type[:2].capitalize() in {
            "Cl", "Br", "Na", "Mg", "Ca", "Zn", "Fe", "Mn", "Cu",
        }:
            return cleaned_type[:2].capitalize()
        return cleaned_type[0].upper()
    cleaned_name = "".join(ch for ch in atom_name.strip() if ch.isalpha())
    if not cleaned_name:
        return ""
    if len(cleaned_name) >= 2 and cleaned_name[:2].capitalize() in {
        "Cl", "Br", "Na", "Mg", "Ca", "Zn", "Fe", "Mn", "Cu",
    }:
        return cleaned_name[:2].capitalize()
    return cleaned_name[0].upper()


def format_pdb_atom_line(line: str, serial: int) -> str | None:
    if not line.startswith(("ATOM  ", "HETATM")):
        return line if line.startswith(("TER", "MODEL", "ENDMDL")) else None
    record = line[:6].strip() or "ATOM"
    atom_name = line[12:16].strip()
    altloc = line[16:17] if len(line) > 16 else " "
    resname = line[17:20].strip() or "UNK"
    chain = line[21:22].strip() or "A"
    try:
        resseq = int(line[22:26])
    except ValueError:
        resseq = serial
    icode = line[26:27] if len(line) > 26 else " "
    try:
        x = float(line[30:38])
        y = float(line[38:46])
        z = float(line[46:54])
    except ValueError:
        parts = line.split()
        if len(parts) < 9:
            return None
        atom_name = parts[2]
        resname = parts[3]
        chain = parts[4] if len(parts[4]) == 1 else "A"
        resseq = int(parts[5] if chain != "A" else parts[4])
        coord_start = 6 if chain != "A" else 5
        x, y, z = map(float, parts[coord_start: coord_start + 3])
    try:
        occupancy = float(line[54:60])
    except ValueError:
        occupancy = 1.0
    try:
        bfactor = float(line[60:66])
    except ValueError:
        bfactor = 0.0
    atom_type_field = line.split()[-1] if line.split() else ""
    element = infer_pdb_element(atom_name, atom_type_field)
    padded_name = atom_name[:4].rjust(4) if len(element) == 1 else atom_name[:4].ljust(4)
    return (
        f"{record:<6}{serial:5d} {padded_name}{altloc:1s}{resname:>3s} {chain:1s}"
        f"{resseq:4d}{icode:1s}   {x:8.3f}{y:8.3f}{z:8.3f}"
        f"{occupancy:6.2f}{bfactor:6.2f}          {element:>2s}\n"
    )


def convert_protein_to_pdb(source: Path, out_pdb: Path, keep_heterogens: bool = False, altloc: str = "A"):
    serial = 1
    kept = 0
    seen: set = set()
    with source.open() as src, out_pdb.open("w") as dst:
        for line in src:
            if line.startswith("HETATM") and not keep_heterogens:
                continue
            if line.startswith(("ATOM  ", "HETATM")):
                line_altloc = line[16:17] if len(line) > 16 else " "
                if line_altloc not in (" ", altloc):
                    continue
                chain = line[21:22].strip() or "A"
                try:
                    resseq = int(line[22:26])
                except ValueError:
                    resseq = serial
                icode = line[26:27] if len(line) > 26 else " "
                atom_name = line[12:16].strip()
                atom_key = (chain, resseq, icode, line[17:20].strip(), atom_name)
                if atom_key in seen:
                    continue
                seen.add(atom_key)
                if line_altloc == altloc:
                    line = line[:16] + " " + line[17:]
            converted = format_pdb_atom_line(line, serial)
            if converted is None:
                continue
            if converted.startswith(("ATOM  ", "HETATM")):
                dst.write(converted)
                serial += 1
                kept += 1
            elif converted.startswith("TER"):
                dst.write("TER\n")
        dst.write("END\n")
    if kept == 0:
        raise RuntimeError(f"No ATOM/HETATM records from {source}")


def clean_protein_pdb(protein: Path, out_pdb: Path):
    convert_protein_to_pdb(protein, out_pdb, keep_heterogens=False)


def detect_pocket_center(protein: Path) -> tuple[float, float, float] | None:
    coords = []
    with protein.open() as f:
        for line in f:
            if not line.startswith("HETATM"):
                continue
            resname = line[17:20].strip()
            if resname in WATER_AND_BUFFER:
                continue
            try:
                coords.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
            except ValueError:
                pass
    if not coords:
        return None
    return tuple(sum(v[i] for v in coords) / len(coords) for i in range(3))


# ---------------------------------------------------------------------------
# Ligand 3D generation & parameterization
# ---------------------------------------------------------------------------

def generate_ligand_3d(smiles: str, name: str, ligand_dir: Path,
                       pocket_center: tuple | None, pose_sdf: Path | None = None) -> Path:
    sdf = ligand_dir / f"{name}.sdf"
    smi = ligand_dir / f"{name}.smi"
    smi.write_text(f"{smiles} {name}\n")

    if pose_sdf is not None:
        if not pose_sdf.exists():
            raise RuntimeError(f"Pose SDF does not exist: {pose_sdf}")
        shutil.copy2(pose_sdf, sdf)
        return sdf

    try:
        import rdkit.Chem as Chem
        from rdkit.Chem import AllChem
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise RuntimeError(f"RDKit could not parse SMILES: {smiles}")
        mol = Chem.AddHs(mol)
        status = AllChem.EmbedMolecule(mol, AllChem.ETKDGv3())
        if status != 0:
            raise RuntimeError(f"RDKit 3D embedding failed for {name}")
        AllChem.MMFFOptimizeMolecule(mol, maxIters=1000)
        mol.SetProp("_Name", name)
        writer = Chem.SDWriter(str(sdf))
        writer.write(mol)
        writer.close()
    except ImportError:
        obabel = require("obabel")
        run_cmd([obabel, "-ismi", str(smi), "-osdf", "-O", str(sdf), "--gen3d", "-h"], ligand_dir)

    if pocket_center:
        translate_sdf_to_center(sdf, pocket_center)
    return sdf


def translate_sdf_to_center(sdf: Path, target: tuple):
    lines = sdf.read_text().splitlines()
    if len(lines) < 4:
        return
    try:
        atom_count = int(lines[3][0:3])
    except ValueError:
        return
    atom_lines = lines[4: 4 + atom_count]
    coords = []
    for line in atom_lines:
        coords.append((float(line[0:10]), float(line[10:20]), float(line[20:30])))
    center = tuple(sum(v[i] for v in coords) / len(coords) for i in range(3))
    delta = tuple(target[i] - center[i] for i in range(3))
    for i, line in enumerate(atom_lines):
        x = float(line[0:10]) + delta[0]
        y = float(line[10:20]) + delta[1]
        z = float(line[20:30]) + delta[2]
        atom_lines[i] = f"{x:10.4f}{y:10.4f}{z:10.4f}{line[30:]}"
    lines[4: 4 + atom_count] = atom_lines
    sdf.write_text("\n".join(lines) + "\n")


def parameterize_ligand(sdf: Path, ligand_dir: Path, charge: int) -> tuple[Path, Path]:
    obabel = require("obabel")
    acpype = require("acpype")
    mol2 = ligand_dir / "LIG.mol2"
    run_cmd([obabel, str(sdf), "-O", str(mol2), "--partialcharge", "gasteiger"], ligand_dir)

    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    lib_dirs = []
    if conda_prefix:
        lib_dirs.append(Path(conda_prefix) / "lib")
    lib_dirs.append(Path("/home/chenjiangyu/miniconda3/envs/fep/lib"))
    dyld_path = ":".join(str(p) for p in lib_dirs if p.exists())

    run_cmd(
        [acpype, "-i", str(mol2), "-b", "LIG", "-n", str(charge), "-a", "gaff2"],
        ligand_dir,
        env_extra={"DYLD_LIBRARY_PATH": dyld_path, "DYLD_FALLBACK_LIBRARY_PATH": dyld_path},
    )

    candidates = sorted(ligand_dir.glob("*.acpype/LIG_GMX.itp"))
    gro_candidates = sorted(ligand_dir.glob("*.acpype/LIG_GMX.gro"))
    if not candidates or not gro_candidates:
        raise RuntimeError("ACPYPE finished but LIG_GMX.itp/LIG_GMX.gro not found.")
    return candidates[0], gro_candidates[0]


# ---------------------------------------------------------------------------
# Topology & system building
# ---------------------------------------------------------------------------

def atom_lines_from_gro(gro: Path) -> tuple[list[str], str]:
    lines = gro.read_text().splitlines()
    if len(lines) < 3:
        raise RuntimeError(f"Invalid GRO file: {gro}")
    atom_count = int(lines[1].strip())
    return lines[2: 2 + atom_count], lines[-1]


def combine_gro(protein_gro: Path, ligand_gro: Path, out_gro: Path):
    p_atoms, box = atom_lines_from_gro(protein_gro)
    l_atoms, _ = atom_lines_from_gro(ligand_gro)
    out_gro.write_text(
        "Protein plus LIG\n"
        f"{len(p_atoms) + len(l_atoms):5d}\n"
        + "\n".join(p_atoms + l_atoms) + "\n" + box + "\n"
    )


def patch_topology(topol: Path, ligand_itp: Path):
    include = f'#include "{ligand_itp.name}"\n'
    lines = topol.read_text().splitlines(keepends=True)
    lines = [l for l in lines if l.strip() != include.strip()]
    insert_at = None
    for i, line in enumerate(lines):
        if "forcefield.itp" in line and line.lstrip().startswith("#include"):
            insert_at = i + 1
            break
    if insert_at is None:
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#include"):
                insert_at = i + 1
                break
    if insert_at is None:
        insert_at = 0
    lines.insert(insert_at, "\n" + include)
    text = "".join(lines)
    if "LIG              1" not in text and "LIG 1" not in text:
        text = text.rstrip() + "\nLIG              1\n"
    topol.write_text(text)
    shutil.copy2(ligand_itp, topol.parent / ligand_itp.name)


def prepare_protein_topology(protein_pdb: Path, out_dir: Path, allow_missing: bool = False) -> tuple[Path, Path]:
    gmx = require("gmx")
    cmd = [gmx, "pdb2gmx", "-f", str(protein_pdb), "-o", "protein_processed.gro",
           "-p", "topol.top", "-ff", "amber99sb-ildn", "-water", "tip3p", "-ignh"]
    if allow_missing:
        cmd.append("-missing")
    run_cmd(cmd, out_dir)
    return out_dir / "protein_processed.gro", out_dir / "topol.top"


def prepare_system_boxes(project: Path, protein_gro: Path, protein_top: Path,
                         ligand_gro: Path, ligand_itp: Path, box_distance: float):
    gmx = require("gmx")
    complex_dir = project / "complex_build"
    solvent_dir = project / "solvent_build"
    complex_dir.mkdir(exist_ok=True)
    solvent_dir.mkdir(exist_ok=True)

    # Complex
    complex_start = complex_dir / "complex_raw.gro"
    combine_gro(protein_gro, ligand_gro, complex_start)
    complex_top = complex_dir / "topol.top"
    shutil.copy2(protein_top, complex_top)
    for itp in sorted(protein_top.parent.glob("*.itp")):
        shutil.copy2(itp, complex_dir / itp.name)
    patch_topology(complex_top, ligand_itp)

    run_cmd([gmx, "editconf", "-f", str(complex_start), "-o", "boxed.gro",
             "-c", "-d", str(box_distance), "-bt", "dodecahedron"], complex_dir)
    shutil.copy2(complex_top, complex_dir / "topol_box.top")
    run_cmd([gmx, "solvate", "-cp", "boxed.gro", "-cs", "spc216.gro",
             "-p", "topol_box.top", "-o", "solvated.gro"], complex_dir)

    # Solvent
    solvent_top = solvent_dir / "topol.top"
    solvent_top.write_text(
        '#include "amber99sb-ildn.ff/forcefield.itp"\n'
        f'#include "{ligand_itp.name}"\n'
        '#include "amber99sb-ildn.ff/tip3p.itp"\n'
        '#ifdef POSRES_WATER\n#include "amber99sb-ildn.ff/posre.itp"\n#endif\n'
        '#include "amber99sb-ildn.ff/ions.itp"\n\n'
        "[ system ]\nLIG in water\n\n[ molecules ]\nLIG              1\n"
    )
    shutil.copy2(ligand_itp, solvent_dir / ligand_itp.name)
    shutil.copy2(ligand_gro, solvent_dir / "ligand.gro")
    run_cmd([gmx, "editconf", "-f", "ligand.gro", "-o", "boxed.gro",
             "-c", "-d", str(box_distance), "-bt", "dodecahedron"], solvent_dir)
    run_cmd([gmx, "solvate", "-cp", "boxed.gro", "-cs", "spc216.gro",
             "-p", "topol.top", "-o", "solvated.gro"], solvent_dir)

    return (complex_dir / "solvated.gro", complex_dir / "topol_box.top",
            solvent_dir / "solvated.gro", solvent_top)


# ---------------------------------------------------------------------------
# Lambda MDP & leg preparation
# ---------------------------------------------------------------------------

def write_common_mdp_files(base: Path):
    (base / "ions.mdp").write_text(ION_MDP)
    (base / "em.mdp").write_text(EM_MDP)
    (base / "nvt.mdp").write_text(NVT_MDP)
    (base / "npt.mdp").write_text(NPT_MDP)


def lambda_arrays() -> tuple[list[float], list[float]]:
    coul = [0.0, 0.1, 0.25, 0.5, 0.75, 1.0] + [1.0] * 12
    vdw = [0.0] * 6 + [0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.75, 0.85, 0.92, 0.97, 1.0, 1.0]
    return coul, vdw


def write_lambda_mdps(leg_dir: Path, nsteps: int, lambda_indices: Sequence[int] | None = None):
    coul, vdw = lambda_arrays()
    zeroes = [0.0] * len(coul)
    fmt = lambda values: " ".join(f"{v:.3f}" for v in values)
    indices = list(lambda_indices) if lambda_indices is not None else list(range(len(coul)))
    for i in indices:
        if i < 0 or i >= len(coul):
            raise ValueError(f"Invalid lambda index {i}; valid range 0..{len(coul)-1}")
        lam_dir = leg_dir / f"lambda_{i:02d}"
        lam_dir.mkdir(parents=True, exist_ok=True)
        mdp = ABFE_MDP_TEMPLATE.format(
            nsteps=nsteps, lambda_state=i,
            coul_lambdas=fmt(coul), vdw_lambdas=fmt(vdw),
            bonded_lambdas=fmt(zeroes), restraint_lambdas=fmt(zeroes),
            mass_lambdas=fmt(zeroes), temperature_lambdas=fmt(zeroes),
        )
        (lam_dir / "prod.mdp").write_text(mdp)


def prepare_leg(leg_dir: Path, start_gro: Path, topol: Path, ligand_itp: Path,
                nsteps: int, lambda_indices: Sequence[int] | None,
                grompp_maxwarn: int, execute: bool, gpu: bool = False, gpus: str | None = None):
    leg_dir.mkdir(parents=True, exist_ok=True)
    write_common_mdp_files(leg_dir)
    shutil.copy2(start_gro, leg_dir / "start.gro")
    shutil.copy2(topol, leg_dir / "topol.top")
    for itp in sorted(topol.parent.glob("*.itp")):
        shutil.copy2(itp, leg_dir / itp.name)
    shutil.copy2(ligand_itp, leg_dir / ligand_itp.name)
    write_lambda_mdps(leg_dir, nsteps=nsteps, lambda_indices=lambda_indices)

    gpu_prefix = ""
    if gpu and gpus is not None:
        gpu_prefix = f'CUDA_VISIBLE_DEVICES="{gpus}" '
    mdrun_extra = ""
    mdrun_extra_em = ""
    if gpu:
        mdrun_extra = "-nb gpu -bonded gpu -pme gpu -update gpu -ntmpi 1 -ntomp 8"
        mdrun_extra_em = "-ntmpi 1 -ntomp 8"

    run_script_text = """#!/usr/bin/env bash
set -euo pipefail

{gpu_prefix}gmx grompp -f em.mdp -c start.gro -p topol.top -o em.tpr -maxwarn {maxwarn}
{gpu_prefix}gmx mdrun -deffnm em {mdrun_extra_em}
{gpu_prefix}gmx grompp -f nvt.mdp -c em.gro -r em.gro -p topol.top -o nvt.tpr -maxwarn {maxwarn}
{gpu_prefix}gmx mdrun -deffnm nvt {mdrun_extra}
{gpu_prefix}gmx grompp -f npt.mdp -c nvt.gro -r nvt.gro -t nvt.cpt -p topol.top -o npt.tpr -maxwarn {maxwarn}
{gpu_prefix}gmx mdrun -deffnm npt {mdrun_extra}

for d in lambda_*; do
  cp npt.gro npt.cpt topol.top *.itp "$d"/
  (cd "$d" && {gpu_prefix}gmx grompp -f prod.mdp -c npt.gro -t npt.cpt -p topol.top -o prod.tpr -maxwarn {maxwarn} && {gpu_prefix}gmx mdrun -deffnm prod {mdrun_extra})
done

if [ "$(find . -maxdepth 1 -type d -name 'lambda_*' | wc -l | tr -d ' ')" -gt 1 ]; then
  gmx bar -b 100 -f lambda_*/prod.xvg -o bar.xvg || gmx bar -b 100 -f lambda_*/dhdl.xvg -o bar.xvg
else
  echo "Only one lambda window; skipping gmx bar."
fi
"""
    run_script = leg_dir / "run_leg.sh"
    run_script.write_text(run_script_text.format(
        maxwarn=grompp_maxwarn, gpu_prefix=gpu_prefix,
        mdrun_extra=mdrun_extra, mdrun_extra_em=mdrun_extra_em,
    ))
    run_script.chmod(0o755)

    if execute:
        run_cmd(["bash", str(run_script)], leg_dir)


# ---------------------------------------------------------------------------
# BAR analysis
# ---------------------------------------------------------------------------

def parse_bar_xvg(path: Path) -> tuple[float | None, float | None]:
    """Parse bar.xvg -> (delta_g_kT, error_kT) summed over all windows."""
    if not path.exists():
        return None, None
    dg_sum = 0.0
    err_sq = 0.0
    count = 0
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(("@", "#")):
                continue
            parts = line.split()
            if len(parts) >= 3:
                try:
                    dg_sum += float(parts[1])
                    err_sq += float(parts[2]) ** 2
                    count += 1
                except ValueError:
                    continue
    if count == 0:
        return None, None
    return dg_sum, math.sqrt(err_sq)


def analyze_single_result(ligand_dir: Path, ligand_id: str, smiles: str = "",
                          vina_score: float | None = None,
                          restraint_correction_kj: float = 0.0,
                          standard_state_correction_kj: float = 0.0) -> dict:
    nested = ligand_dir / ligand_id
    base = nested if nested.is_dir() else ligand_dir

    complex_bar = base / "complex" / "bar.xvg"
    solvent_bar = base / "solvent" / "bar.xvg"

    dg_c, err_c = parse_bar_xvg(complex_bar)
    dg_s, err_s = parse_bar_xvg(solvent_bar)

    if dg_c is None and dg_s is None:
        return make_fep_result(ligand_id, smiles, vina_score, str(ligand_dir), status="incomplete")

    dg_c_kj = round(dg_c * RT_KJ_MOL, 3) if dg_c is not None else None
    dg_s_kj = round(dg_s * RT_KJ_MOL, 3) if dg_s is not None else None
    err_c_kj = round(err_c * RT_KJ_MOL, 3) if err_c is not None else None
    err_s_kj = round(err_s * RT_KJ_MOL, 3) if err_s is not None else None

    dg_bind = None
    err_bind = None
    if dg_c_kj is not None and dg_s_kj is not None:
        dg_bind = round(dg_c_kj - dg_s_kj + restraint_correction_kj + standard_state_correction_kj, 3)
        if err_c_kj is not None and err_s_kj is not None:
            err_bind = round((err_c_kj ** 2 + err_s_kj ** 2) ** 0.5, 3)

    lambda_count = len(list((base / "complex").glob("lambda_*/"))) if (base / "complex").exists() else 0
    status = "completed" if dg_bind is not None else "incomplete"

    return make_fep_result(
        ligand_id, smiles, vina_score, str(base),
        dg_c_kj=dg_c_kj, err_c_kj=err_c_kj,
        dg_s_kj=dg_s_kj, err_s_kj=err_s_kj,
        dg_bind=dg_bind, err_bind=err_bind,
        status=status, lambda_windows=lambda_count,
    )


def analyze_all_results(search_dirs: list[Path],
                        restraint_correction_kj: float = 0.0,
                        standard_state_correction_kj: float = 0.0) -> list[dict]:
    import re
    results = []
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for ligand_dir in sorted(search_dir.glob("ligand_*/")):
            if not ligand_dir.is_dir():
                continue
            ligand_id = ligand_dir.name
            # Try to read SMILES from metadata
            smiles = ""
            vina_score = None
            meta = ligand_dir / "fep_params.json"
            if meta.exists():
                try:
                    m = json.loads(meta.read_text())
                    smiles = m.get("smiles", "")
                except Exception:
                    pass
            results.append(analyze_single_result(
                ligand_dir, ligand_id, smiles, vina_score,
                restraint_correction_kj, standard_state_correction_kj,
            ))
    # Sort by ligand number
    results.sort(key=lambda r: int(re.sub(r"\D", "", r["ligand_id"]) or "0"))
    return results
