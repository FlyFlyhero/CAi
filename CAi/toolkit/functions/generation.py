"""Molecule generation tools.

Each function wraps a server-side model (scaffold-constrained generators,
de-novo generators, pocket-aware generators). The wrappers validate
inputs, call run_tool(...), and return a native Python dictionary
for the agent to easily parse.
"""

from __future__ import annotations

from .._validators import (
    reject_chiral,
    require_attachment_point,
    require_pocket_definition,
    valid_complete_molecule_smiles,
    valid_existing_file,
)
from ..client import run_tool


def run_gromacs_md(
    step: str,
    input_pdb: str | None = None,
    input_gro: str | None = None,
    ff: str = "amber99sb-ildn",
    water: str = "tip3p",
    mode: str = "nvt",
) -> dict:
    """Run a GROMACS molecular dynamics simulation step.

    Supports a full MD workflow: topology preparation → solvation →
    energy minimization → NVT/NPT equilibration → production MD.

    Each step operates in the job sandbox directory. Output files
    (.gro, .xtc, .top, etc.) are written to the sandbox and referenced
    in the result.

    Args:
        step:      MD step to run. One of: "prep", "solvate", "minimize",
                   "equilibrate", "production".
        input_pdb: Input PDB file (required for "prep" step).
        input_gro: Input GRO file (for steps after prep; defaults vary by step).
        ff:        Force field (default "amber99sb-ildn", used in "prep").
        water:     Water model (default "tip3p", used in "prep").
        mode:      Equilibration mode "nvt" or "npt" (used in "equilibrate").

    Returns:
        On success:
          {
            "success": True,
            "step": str,
            "data": { "output_gro": str, "message": str, ... },
          }
        On error:
          {"success": False, "error": str}
    """
    valid_steps = {"prep", "solvate", "minimize", "equilibrate", "production"}
    if step not in valid_steps:
        return {"success": False, "error": f"step must be one of {sorted(valid_steps)}, got '{step}'"}

    if step == "prep" and input_pdb:
        if err := valid_existing_file(input_pdb, field_name="input_pdb"):
            return {"success": False, "error": err}

    payload: dict = {"step": step}
    if input_pdb is not None:
        payload["input_pdb"] = input_pdb
    if input_gro is not None:
        payload["input_gro"] = input_gro
    if step == "prep":
        payload["ff"] = ff
        payload["water"] = water
    if step == "equilibrate":
        payload["mode"] = mode

    # MD production can be slow; allow up to 2 hours
    timeout = 120 if step == "production" else 60
    result = run_tool("gromacs_runner", payload, timeout_mins=timeout)
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    return {
        "success": True,
        "step": step,
        "data": result.get("data", {}),
    }


def generate_molecules_sc2mol(
    scaffolds: list[str],
    num_sample: int | None = None,
    ckpt: str = "sc2mol_smoke/ckpt-9",
    max_len: int = 64,
) -> dict:
    """Generate molecules from scaffold SMILES using the Sc2Mol transformer model.

    Sc2Mol is a scaffold-conditioned molecule generation model based on a
    VAE-transformer architecture. Each input scaffold produces one output molecule.

    Args:
        scaffolds: List of scaffold SMILES strings (e.g., ["c1ccccc1", "C1CCCCC1"]).
        num_sample: Number of scaffolds to use (default: all provided scaffolds).
        ckpt: Checkpoint path relative to Sc2Mol/checkpoints/ (default "sc2mol_smoke/ckpt-9").
        max_len: Maximum SMILES token length (default 64).

    Returns:
        On success:
          {
            "success": True,
            "mode": "scaffold",
            "checkpoint": str,
            "num_scaffolds": int,
            "num_sample_requested": int,
            "num_sample_used": int,
            "results": [
              {"index": int, "input_scaffold": str, "smiles": str, ...},
              ...
            ],
          }
        On error:
          {"success": False, "error": str}
    """
    if not scaffolds:
        return {"success": False, "error": "scaffolds must be a non-empty list of SMILES strings"}

    payload: dict = {
        "scaffolds": scaffolds,
        "ckpt": ckpt,
        "max_len": max_len,
    }
    if num_sample is not None:
        payload["num_sample"] = num_sample

    result = run_tool("sc2mol", payload, timeout_mins=10)
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    return {
        "success": True,
        "mode": "scaffold",
        "checkpoint": summary.get("checkpoint", ckpt),
        "num_scaffolds": summary.get("num_scaffolds"),
        "num_sample_requested": summary.get("num_sample_requested"),
        "num_sample_used": summary.get("num_sample_used"),
        "results": result.get("results", []),
    }


def infer_synthesis_synllama(
    smiles: list[str],
    sample_mode: str = "frozen_only",
    model: str = "91rxns",
    gpus: int = 1,
    max_molecules: int = 5,
) -> dict:
    """Infer synthesis pathways for target molecules using the SynLlama LLM model.

    SynLlama is a language model trained on chemical synthesis data that predicts
    possible synthetic routes for a given target molecule.

    Args:
        smiles: List of target molecule SMILES strings.
        sample_mode: Sampling strategy (default "frozen_only").
            Options: frozen_only, frugal, greedy, low_only, medium_only, high_only.
        model: Model identifier (default "91rxns").
        gpus: Number of GPUs to use (default 1).
        max_molecules: Maximum number of molecules to process (default 5).

    Returns:
        On success:
          {
            "success": True,
            "model": str,
            "sample_mode": str,
            "num_input_smiles": int,
            "results": [
              {"index": int, "smiles": str, "predictions": ...},
              ...
            ],
          }
        On error:
          {"success": False, "error": str}
    """
    if not smiles:
        return {"success": False, "error": "smiles must be a non-empty list of SMILES strings"}

    payload = {
        "smiles": smiles,
        "sample_mode": sample_mode,
        "model": model,
        "gpus": gpus,
        "max_molecules": max_molecules,
    }
    result = run_tool("synllama", payload, timeout_mins=15)
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    return {
        "success": True,
        "model": summary.get("model", model),
        "sample_mode": summary.get("sample_mode", sample_mode),
        "num_input_smiles": summary.get("num_input_smiles"),
        "results": result.get("results", []),
    }


def generate_scaffold_analogs(smiles: str, num_analogs: int = 10) -> dict:
    """Generate novel molecular analogs from a scaffold SMILES using a pre-trained
    RNN-based scaffold generation model.

    When to use:
        The user provides a scaffold SMILES with an explicit '*' growth point.

    Do not use when:
        - The input is a complete molecule rather than a scaffold.
        - The SMILES lacks a '*' attachment point.
        - The SMILES contains '@@' stereochemistry.

    Args:
        smiles:      Scaffold SMILES (must contain '*'). Example: 'c1ccccc1*'.
        num_analogs: How many analogs to request (default 10; actual count may be smaller).

    Returns:
        On success:
          {
            "success": True,
            "input_scaffold": str,           # echoed scaffold SMILES
            "requested_batch_size": int,     # requested analog count
            "generated_count": int,          # actual unique molecules generated
            "molecules": [str, ...],         # list of SMILES strings
          }
        On error:
          {"success": False, "error": str}
    """
    if err := require_attachment_point(smiles):
        return {"success": False, "error": err}
    if err := reject_chiral(smiles):
        return {"success": False, "error": err}

    result = run_tool("scaffold", {"smiles": smiles, "num_analogs": num_analogs})
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    generated_smiles = [item["smiles"] for item in result.get("results", [])]

    return {
        "success": True,
        "input_scaffold": summary.get("input_scaffold", smiles),
        "requested_batch_size": summary.get("requested_batch_size", num_analogs),
        "generated_count": summary.get("valid_unique_generated"),
        "molecules": generated_smiles,
    }


def generate_libinvent_decorations(smiles: str, num_decorations: int = 3) -> dict:
    """Decorate a chemical scaffold using the Lib-INVENT reaction-based model.

    Generates decorated molecules by attaching substituents at the scaffold's
    '[*]' attachment points.

    Args:
        smiles:           Scaffold SMILES with at least one '*' or '[*:1]' attachment point
                          (no '@@' stereochemistry).
        num_decorations:  How many decorated variants to request (default 3).

    Returns:
        On success:
          {
            "success": True,
            "input_scaffold": str,                     # echoed scaffold SMILES
            "requested_num_decorations": int,          # requested decoration count
            "generated_count": int,                    # actual unique molecules generated
            "csv_columns": [str, ...],                 # column names in server output
            "molecules_smiles": [str, ...],            # list of SMILES strings
            "decorated_molecules_preview": [           # top-N preview rows
              {"SMILES": str, "status": str, "message": str},
              ...
            ],
          }
        On error:
          {"success": False, "error": str}
    """
    if err := require_attachment_point(smiles):
        return {"success": False, "error": err}
    if err := reject_chiral(smiles):
        return {"success": False, "error": err}

    payload = {"smiles": smiles, "number_of_decorations_per_scaffold": num_decorations}
    result = run_tool("libinvent", payload)
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    results = result.get("results", [])
    molecules_smiles = [row.get("SMILES") for row in results if row.get("SMILES")]
    input_scaffold = results[0].get("input_scaffold") if results else None

    return {
        "success": True,
        "input_scaffold": input_scaffold,
        "requested_num_decorations": num_decorations,
        "generated_count": summary.get("row_count"),
        "csv_columns": summary.get("columns", []),
        "molecules_smiles": molecules_smiles,
        "decorated_molecules_preview": summary.get("preview", []),
    }


def generate_molecules_for_pocket(
    protein_pdb_path: str,
    center_xyz: list | None = None,
    ref_ligand_path: str | None = None,
    num_samples: int = 10,
) -> dict:
    """Target-aware zero-shot molecular generation with RxnFlow.

    Generates candidate molecules for a protein target using either:
      1. protein file + binding-pocket center coordinates, or
      2. protein file + reference-ligand file.

    Args:
        protein_pdb_path: Target protein structure (.sdf / .mol2 / .pdb).
        center_xyz:        [x, y, z] pocket center (optional if ref_ligand_path is given).
        ref_ligand_path:   Reference ligand structure (optional if center_xyz is given).
        num_samples:       Molecules to generate (default 10).

    Returns:
        On success:
          {
            "success": True,
            "generated_count": int,                        # number of molecules generated
            "sampling_time_sec": float,                    # time spent sampling
            "full_results_csv_path": str,                  # path to the full results CSV
            "top_molecules_preview": [                     # preview of top molecules
              {"smiles": str, "qed": float, "proxy_score": float},
              ...
            ],
          }
        On error:
          {"success": False, "error": str}
    """
    if err := require_pocket_definition(protein_pdb_path, center_xyz, ref_ligand_path):
        return {"success": False, "error": err}

    payload: dict = {
        "protein_pdb_path": protein_pdb_path,
        "num_samples": num_samples,
        "save_reward": True,
    }
    if center_xyz:
        payload["center"] = center_xyz
    if ref_ligand_path:
        payload["ref_ligand_path"] = ref_ligand_path

    result = run_tool("rxnflow", payload, timeout_mins=15)
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    results_data = result.get("results", {})

    return {
        "success": True,
        "generated_count": summary.get("generated_count"),
        "sampling_time_sec": summary.get("sampling_time_sec"),
        "full_results_csv_path": summary.get("output_file"),
        "top_molecules_preview": results_data.get("generated_preview", []),
    }


def generate_molecules_reinvent4_denovo(num_variants: int = 100) -> dict:
    """Generate completely novel molecules from scratch using the REINVENT4 de novo
    prior model.

    No input scaffold is needed. Suitable for broad chemical-space exploration.

    Args:
        num_variants: Number of molecules to generate (default 100).

    Returns:
        On success:
          {
            "success": True,
            "mode": "de_novo",                     # generation mode
            "requested_variants": int,             # requested molecule count
            "generated_count": int,                # actual unique molecules generated
            "molecules_smiles": [str, ...],        # list of SMILES strings
          }
        On error:
          {"success": False, "error": str}
    """
    result = run_tool(
        "reinvent4", {"num_variants": num_variants}, action="de_novo", timeout_mins=10
    )
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    molecules_data = result.get("results", {}).get("molecules", [])
    smiles_list = [mol["smiles"] for mol in molecules_data if mol.get("smiles")]

    return {
        "success": True,
        "mode": "de_novo",
        "requested_variants": num_variants,
        "generated_count": summary.get("generated_count", len(smiles_list)),
        "molecules_smiles": smiles_list,
    }


def generate_molecules_reinvent4_libinvent(smiles: str, num_variants: int = 50) -> dict:
    """Decorate a chemical scaffold by generating R-group variants at [*] attachment
    points using the REINVENT4 LibInvent model.

    The input MUST be a scaffold SMILES containing at least one [*] wildcard.
    Does NOT support '@@' stereochemistry — use mol2mol mode for chiral molecules.

    Args:
        smiles:        Scaffold SMILES with [*] attachment points.
        num_variants:  Variants to generate (default 50).

    Returns:
        On success:
          {
            "success": True,
            "mode": "libinvent",                   # generation mode
            "input_scaffold": str,                 # echoed scaffold SMILES
            "requested_variants": int,             # requested variant count
            "generated_count": int,                # actual unique molecules generated
            "molecules_smiles": [str, ...],        # list of SMILES strings
          }
        On error:
          {"success": False, "error": str}
    """
    if err := require_attachment_point(smiles):
        return {"success": False, "error": err}
    if err := reject_chiral(smiles):
        return {"success": False, "error": err}

    result = run_tool(
        "reinvent4",
        {"smiles_list": [smiles], "num_variants": num_variants},
        action="libinvent",
        timeout_mins=10,
    )
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    molecules_data = result.get("results", {}).get("molecules", [])
    smiles_list = [mol["smiles"] for mol in molecules_data if mol.get("smiles")]

    return {
        "success": True,
        "mode": "libinvent",
        "input_scaffold": smiles,
        "requested_variants": num_variants,
        "generated_count": summary.get("generated_count", len(smiles_list)),
        "molecules_smiles": smiles_list,
    }


def drugex_finetune(
    base_dir: str | None = None,
    input_prefix: str = "arl",
    output_prefix: str | None = None,
    agent_path: str | None = None,
    mol_type: str = "graph",
    algorithm: str = "trans",
    epochs: int = 2,
    batch_size: int = 32,
    gpu: str = "0",
) -> dict:
    """Fine-tune a pre-trained DrugEx generator model on a custom dataset.

    Use this when you have a dataset of molecules and want to adapt a
    pre-trained model to generate similar compounds. Must be run before
    `drugex_rl` (the RL step requires a fine-tuned agent model).

    Args:
        base_dir:      Base directory containing DrugEx data/models. Uses default
                       from paths.json if not provided.
        input_prefix:  Prefix for input data files (default "arl").
        output_prefix: Prefix for output model name (default: same as input_prefix).
        agent_path:    Path to pre-trained model (.pkg). Uses default from paths.json.
        mol_type:      Molecule representation: "graph" or "smiles" (default "graph").
        algorithm:     Architecture: "trans" (transformer) or "rnn" (default "trans").
        epochs:        Training epochs (default 2).
        batch_size:    Batch size (default 32).
        gpu:           GPU device IDs, comma-separated (default "0").

    Returns:
        On success:
          {
            "success": True,
            "mode": "finetune",
            "output_model": str,      # path to the fine-tuned .pkg model
            "output_name": str,       # model identifier (e.g. "arl_graph_trans_FT")
          }
        On error:
          {"success": False, "error": str}
    """
    payload: dict = {
        "mol_type": mol_type,
        "algorithm": algorithm,
        "epochs": epochs,
        "batch_size": batch_size,
        "gpu": gpu,
    }
    if base_dir is not None:
        payload["base_dir"] = base_dir
    payload["input"] = input_prefix
    if output_prefix is not None:
        payload["output"] = output_prefix
    if agent_path is not None:
        payload["agent_path"] = agent_path

    result = run_tool("drugex", payload, action="finetune", timeout_mins=60)
    if result.get("error"):
        return {"success": False, "error": result["error"]}
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "Finetune failed for unknown reason")}

    summary = result.get("summary", {})
    return {
        "success": True,
        "mode": "finetune",
        "output_model": summary.get("output_model", ""),
        "output_model_found": summary.get("output_model_found", False),
        "output_name": result.get("results", {}).get("output_name", ""),
    }


def drugex_rl(
    base_dir: str | None = None,
    input_prefix: str = "arl",
    output_prefix: str | None = None,
    agent_path: str | None = None,
    prior_path: str | None = None,
    predictor: list[str] | None = None,
    active_targets: list[str] | None = None,
    mol_type: str = "graph",
    algorithm: str = "trans",
    epochs: int = 2,
    batch_size: int = 32,
    gpu: str = "0",
    scheme: str = "PRCD",
) -> dict:
    """Run reinforcement learning (RL) on a fine-tuned DrugEx generator.

    Uses QSAR predictors as reward signals to steer molecule generation
    toward desired properties. Requires a fine-tuned agent model (from
    `drugex_finetune`).

    Args:
        base_dir:       Base directory. Uses default from paths.json if not provided.
        input_prefix:   Prefix for input data (default "arl").
        output_prefix:  Prefix for output model name (default: same as input_prefix).
        agent_path:     Path to fine-tuned agent model (name or path). Uses paths.json default.
        prior_path:     Path to pre-trained prior model. Uses paths.json default.
        predictor:      List of QSAR predictor paths for reward. Uses paths.json default.
        active_targets: Target names to activate (default ["A2AR_RandomForestClassifier"]).
        mol_type:       "graph" or "smiles" (default "graph").
        algorithm:      "trans" or "rnn" (default "trans").
        epochs:         Training epochs (default 2).
        batch_size:     Batch size (default 32).
        gpu:            GPU device IDs (default "0").
        scheme:         RL reward scheme (default "PRCD").

    Returns:
        On success:
          {
            "success": True,
            "mode": "rl",
            "output_model": str,      # path to the RL-trained .pkg model
            "output_name": str,       # model identifier (e.g. "arl_graph_trans_RL")
          }
        On error:
          {"success": False, "error": str}
    """
    payload: dict = {
        "mol_type": mol_type,
        "algorithm": algorithm,
        "epochs": epochs,
        "batch_size": batch_size,
        "gpu": gpu,
        "scheme": scheme,
    }
    if base_dir is not None:
        payload["base_dir"] = base_dir
    payload["input"] = input_prefix
    if output_prefix is not None:
        payload["output"] = output_prefix
    if agent_path is not None:
        payload["agent_path"] = agent_path
    if prior_path is not None:
        payload["prior_path"] = prior_path
    if predictor is not None:
        payload["predictor"] = predictor
    if active_targets is not None:
        payload["active_targets"] = active_targets

    result = run_tool("drugex", payload, action="rl", timeout_mins=120)
    if result.get("error"):
        return {"success": False, "error": result["error"]}
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "RL training failed for unknown reason")}

    summary = result.get("summary", {})
    return {
        "success": True,
        "mode": "rl",
        "output_model": summary.get("output_model", ""),
        "output_model_found": summary.get("output_model_found", False),
        "output_name": result.get("results", {}).get("output_name", ""),
    }


def drugex_generate(
    base_dir: str | None = None,
    generator: str = "arl_graph_trans_RL",
    input_fragments: str | None = None,
    num_samples: int = 100,
    batch_size: int = 128,
    gpu: str = "0",
    voc_files: list[str] | None = None,
) -> dict:
    """Generate molecules using a trained DrugEx generator model.

    Takes fragment inputs and produces new molecules conditioned on those
    fragments and the trained model's learned distribution. Use this after
    running `drugex_finetune` + `drugex_rl` to get a reward-optimized model.

    Args:
        base_dir:         Base directory. Uses default from paths.json.
        generator:        Name of trained generator model (default "arl_graph_trans_RL").
        input_fragments:  Input fragment file name. Uses paths.json default.
        num_samples:      Number of molecules to generate (default 100).
        batch_size:       Generation batch size (default 128).
        gpu:              GPU device IDs (default "0").
        voc_files:        Vocabulary file list. Default ["smiles"].

    Returns:
        On success:
          {
            "success": True,
            "mode": "generate",
            "total_molecules_generated": int,
            "molecules": [{"smiles": str, ...}, ...],  # preview of generated molecules
          }
        On error:
          {"success": False, "error": str}
    """
    payload: dict = {
        "generator": generator,
        "num_samples": num_samples,
        "batch_size": batch_size,
        "gpu": gpu,
    }
    if base_dir is not None:
        payload["base_dir"] = base_dir
    if input_fragments is not None:
        payload["input_fragments"] = input_fragments
    if voc_files is not None:
        payload["voc_files"] = voc_files

    result = run_tool("drugex", payload, action="generate", timeout_mins=30)
    if result.get("error"):
        return {"success": False, "error": result["error"]}
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "Generation failed for unknown reason")}

    summary = result.get("summary", {})
    results = result.get("results", {})
    return {
        "success": True,
        "mode": "generate",
        "generator_model": summary.get("generator_model", generator),
        "total_molecules_generated": summary.get("total_molecules_generated", 0),
        "molecules": results.get("molecules_preview", []),
    }


def deepchem_seq2seq_train(
    dataset_name: str = "muv",
    epochs: int = 2,
    batch_size: int = 100,
    embedding_dimension: int = 256,
    encoder_layers: int = 2,
    decoder_layers: int = 2,
) -> dict:
    """Train a Seq2Seq autoencoder on molecular SMILES data.

    Learns fixed-length vector representations (embeddings) of molecules.
    The trained model can be used for downstream tasks via
    `deepchem_seq2seq_evaluate`.

    Args:
        dataset_name:          Dataset to train on. Currently only "muv" is supported.
        epochs:                Training epochs (default 2).
        batch_size:            Batch size (default 100).
        embedding_dimension:   Latent vector dimension (default 256).
        encoder_layers:        Number of encoder RNN layers (default 2).
        decoder_layers:        Number of decoder RNN layers (default 2).

    Returns:
        On success:
          {
            "success": True,
            "mode": "seq2seq_train",
            "dataset": str,
            "train_samples": int,
            "valid_samples": int,
            "epochs": int,
            "reconstruction_accuracy": float,
            "model_dir": str,
            "tokens_file": str,
          }
        On error:
          {"success": False, "error": str}
    """
    if dataset_name != "muv":
        return {"success": False, "error": "Only dataset_name='muv' is currently supported."}

    payload = {
        "dataset_name": dataset_name,
        "epochs": epochs,
        "batch_size": batch_size,
        "embedding_dimension": embedding_dimension,
        "encoder_layers": encoder_layers,
        "decoder_layers": decoder_layers,
    }
    result = run_tool("deepchem", payload, action="seq2seq_train", timeout_mins=30)
    if result.get("error"):
        return {"success": False, "error": result["error"]}
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "Seq2Seq training failed")}

    summary = result.get("summary", {})
    results = result.get("results", {})
    return {
        "success": True,
        "mode": "seq2seq_train",
        "dataset": summary.get("dataset", dataset_name),
        "train_samples": summary.get("train_samples"),
        "valid_samples": summary.get("valid_samples"),
        "epochs": summary.get("epochs", epochs),
        "reconstruction_accuracy": summary.get("reconstruction_accuracy", 0.0),
        "model_dir": results.get("model_dir"),
        "tokens_file": results.get("tokens_file"),
    }


def deepchem_seq2seq_evaluate(
    dataset_name: str = "muv",
    classifier_epochs: int = 3,
    batch_size: int = 100,
    embedding_dimension: int = 256,
    encoder_layers: int = 2,
    decoder_layers: int = 2,
) -> dict:
    """Evaluate a trained Seq2Seq model by training a downstream classifier.

    Loads the Seq2Seq model trained by `deepchem_seq2seq_train`, generates
    embeddings for train/valid datasets, and trains a multitask classifier.
    Returns ROC-AUC scores as a measure of embedding quality.

    Args:
        dataset_name:          Dataset name. Currently only "muv".
        classifier_epochs:     Epochs for downstream classifier training (default 3).
        batch_size:            Batch size (default 100).
        embedding_dimension:   Must match the trained Seq2Seq model (default 256).
        encoder_layers:        Must match the trained Seq2Seq model (default 2).
        decoder_layers:        Must match the trained Seq2Seq model (default 2).

    Returns:
        On success:
          {
            "success": True,
            "mode": "seq2seq_evaluate",
            "dataset": str,
            "num_tasks": int,
            "classifier_epochs": int,
            "train_roc_auc": float,
            "valid_roc_auc": float,
            "embeddings_train_shape": [int, int],
            "embeddings_valid_shape": [int, int],
          }
        On error:
          {"success": False, "error": str}
    """
    if dataset_name != "muv":
        return {"success": False, "error": "Only dataset_name='muv' is currently supported."}

    payload = {
        "dataset_name": dataset_name,
        "classifier_epochs": classifier_epochs,
        "batch_size": batch_size,
        "embedding_dimension": embedding_dimension,
        "encoder_layers": encoder_layers,
        "decoder_layers": decoder_layers,
    }
    result = run_tool("deepchem", payload, action="seq2seq_evaluate", timeout_mins=30)
    if result.get("error"):
        return {"success": False, "error": result["error"]}
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "Seq2Seq evaluation failed")}

    summary = result.get("summary", {})
    results = result.get("results", {})
    emb_shape = results.get("embeddings_shape", {})
    return {
        "success": True,
        "mode": "seq2seq_evaluate",
        "dataset": summary.get("dataset", dataset_name),
        "num_tasks": summary.get("tasks"),
        "classifier_epochs": summary.get("classifier_epochs", classifier_epochs),
        "train_roc_auc": summary.get("train_roc_auc"),
        "valid_roc_auc": summary.get("valid_roc_auc"),
        "embeddings_train_shape": emb_shape.get("train"),
        "embeddings_valid_shape": emb_shape.get("valid"),
    }


def deepchem_molgan_train(
    dataset_name: str = "tox21",
    num_atoms: int = 12,
    epochs: int = 5,
    atom_labels: list | None = None,
) -> dict:
    """Train a MolGAN model to generate small molecules.

    MolGAN is a generative adversarial network that produces molecular
    graphs. The trained model can be used for molecule generation via
    `deepchem_molgan_generate`.

    Args:
        dataset_name: Dataset to train on. Currently only "tox21".
        num_atoms:    Maximum number of atoms in generated molecules (default 12).
        epochs:       Training epochs (default 5).
        atom_labels:  List of atom types to include. Default: [0,5,6,7,8,9,11,12,13,14].

    Returns:
        On success:
          {
            "success": True,
            "mode": "molgan_train",
            "dataset": str,
            "training_samples": int,
            "epochs": int,
            "num_atoms": int,
            "model_path": str,
          }
        On error:
          {"success": False, "error": str}
    """
    if dataset_name != "tox21":
        return {"success": False, "error": "Only dataset_name='tox21' is currently supported."}

    payload: dict = {
        "dataset_name": dataset_name,
        "num_atoms": num_atoms,
        "epochs": epochs,
    }
    if atom_labels is not None:
        payload["atom_labels"] = atom_labels

    result = run_tool("deepchem", payload, action="molgan_train", timeout_mins=30)
    if result.get("error"):
        return {"success": False, "error": result["error"]}
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "MolGAN training failed")}

    summary = result.get("summary", {})
    results = result.get("results", {})
    return {
        "success": True,
        "mode": "molgan_train",
        "dataset": summary.get("dataset", dataset_name),
        "training_samples": summary.get("training_samples"),
        "epochs": summary.get("epochs", epochs),
        "num_atoms": summary.get("num_atoms", num_atoms),
        "model_path": results.get("model_path"),
    }


def deepchem_molgan_generate(
    num_samples: int = 100,
    num_atoms: int = 12,
    atom_labels: list | None = None,
) -> dict:
    """Generate molecules using a trained MolGAN model.

    Requires that `deepchem_molgan_train` has been run first.

    Args:
        num_samples:  Number of molecules to attempt generation (default 100).
        num_atoms:    Must match the trained model's num_atoms (default 12).
        atom_labels:  Must match the trained model's atom_labels.

    Returns:
        On success:
          {
            "success": True,
            "mode": "molgan_generate",
            "total_generated": int,
            "valid_molecules": int,
            "unique_molecules": int,
            "smiles": [str, ...],   # preview of valid SMILES
          }
        On error:
          {"success": False, "error": str}
    """
    payload: dict = {
        "num_samples": num_samples,
        "num_atoms": num_atoms,
    }
    if atom_labels is not None:
        payload["atom_labels"] = atom_labels

    result = run_tool("deepchem", payload, action="molgan_generate", timeout_mins=10)
    if result.get("error"):
        return {"success": False, "error": result["error"]}
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "MolGAN generation failed")}

    summary = result.get("summary", {})
    results = result.get("results", {})
    return {
        "success": True,
        "mode": "molgan_generate",
        "total_generated": summary.get("total_generated"),
        "valid_molecules": summary.get("valid_molecules"),
        "unique_molecules": summary.get("unique_molecules"),
        "smiles": results.get("smiles_preview", []),
    }


def generate_molecules_reinvent4_mol2mol(
    smiles: str,
    num_variants: int = 50,
    strategy: str = "beamsearch",
    temperature: float = 1.0,
) -> dict:
    """Generate structural analogs of a reference molecule while preserving
    stereochemistry using the REINVENT4 Mol2Mol model.

    Input should be a complete SMILES string (supports '@@' chirality).
    Does NOT support [*] wildcards — use libinvent for scaffold decoration.

    Args:
        smiles:       Complete reference-molecule SMILES.
        num_variants: Analogs to generate (default 50).
        strategy:     'beamsearch' or 'multinomial' (default beamsearch).
        temperature:  Sampling temperature (default 1.0).

    Returns:
        On success:
          {
            "success": True,
            "mode": "mol2mol",                     # generation mode
            "input_smiles": str,                   # echoed reference SMILES
            "strategy": str,                       # sampling strategy used
            "temperature": float,                  # sampling temperature used
            "requested_variants": int,             # requested analog count
            "generated_count": int,                # actual unique molecules generated
            "molecules_smiles": [str, ...],        # list of SMILES strings
          }
        On error:
          {"success": False, "error": str}
    """
    if err := valid_complete_molecule_smiles(smiles):
        return {"success": False, "error": err}

    payload = {
        "smiles_list": [smiles],
        "num_variants": num_variants,
        "strategy": strategy,
        "temperature": temperature,
    }
    result = run_tool("reinvent4", payload, action="mol2mol", timeout_mins=10)
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    summary = result.get("summary", {})
    molecules_data = result.get("results", {}).get("molecules", [])
    smiles_out = [mol["smiles"] for mol in molecules_data if mol.get("smiles")]

    return {
        "success": True,
        "mode": "mol2mol",
        "input_smiles": smiles,
        "strategy": strategy,
        "temperature": temperature,
        "requested_variants": num_variants,
        "generated_count": summary.get("generated_count", len(smiles_out)),
        "molecules_smiles": smiles_out,
    }
