#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
import csv
import traceback
from pathlib import Path
from argparse import Namespace


def load_default_paths():
    paths_file = Path(__file__).resolve().parent / "paths.json"
    if paths_file.exists():
        with open(paths_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def write_json(path: Path, payload: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def build_design_args(base_dir, generator, input_fragments, num_samples, batch_size, gpu, debug, voc_files,
                      keep_invalid=False, keep_duplicates=False, keep_undesired=False):
    """
    模拟 DrugEx generate.py 中 DesignArgParser() 的核心行为：
    1. 读取基础 generate 参数
    2. 自动加载 generators/<generator>.json 中保存的训练参数
    3. 组装出 Design(args) 所需的完整 Namespace
    """
    generator_json = os.path.join(base_dir, "generators", f"{generator}.json")
    if not os.path.exists(generator_json):
        raise FileNotFoundError(
            f"未找到生成模型配置文件: {generator_json}。\n"
            f"请先确认已经成功训练出模型 {generator}（通常先 finetune，再 rl）。"
        )

    with open(generator_json, "r", encoding="utf-8") as f:
        train_args = json.load(f)

    # 这是 DrugEx generate.py 中会从训练 json 里继承的字段
    inherited_keys = [
        "mol_type", "algorithm", "predictor", "scheme", "use_gru",
        "active_targets", "inactive_targets", "window_targets", "activity_threshold",
        "qed", "uniqueness", "sa_score", "ra_score", "molecular_weight",
        "mw_thresholds", "logP", "logP_thresholds", "tpsa", "tpsa_thresholds",
        "similarity_mol", "similarity_type", "similarity_threshold",
        "similarity_tversky_weights", "ligand_efficiency", "le_thresholds",
        "lipophilic_efficiency", "lipe_thresholds"
    ]

    args_dict = {
        "base_dir": base_dir,
        "debug": debug,
        "generator": generator,
        "input_file": input_fragments,
        "voc_files": voc_files,
        "num": num_samples,
        "keep_invalid": keep_invalid,
        "keep_duplicates": keep_duplicates,
        "keep_undesired": keep_undesired,
        "use_gpus": str(gpu),
        "batch_size": batch_size,
    }

    for k in inherited_keys:
        if k in train_args:
            args_dict[k] = train_args[k]

    # 对齐 generate.py 中 DesignArgParser 的后处理逻辑
    args_dict["targets"] = (
        args_dict.get("active_targets", [])
        + args_dict.get("inactive_targets", [])
        + args_dict.get("window_targets", [])
    )

    return Namespace(**args_dict), train_args


def main():
    cwd = Path.cwd()
    result_payload = {"success": False, "error": None}

    try:
        params_file = cwd / "params.json"
        if not params_file.exists():
            raise FileNotFoundError("当前沙盒目录下未找到 params.json")

        with open(params_file, "r", encoding="utf-8") as f:
            params = json.load(f)

        print(f"[INFO] generate action 收到参数: {list(params.keys())}", file=sys.stderr)

        defaults = load_default_paths()

        base_dir = params.get("base_dir", defaults.get("base_dir", ""))
        input_fragments = params.get("input_fragments", defaults.get("default_test_fragments", "arl_test_graph.txt"))
        generator = params.get("generator", "arl_graph_trans_RL")
        num_samples = params.get("num_samples", 100)
        batch_size = params.get("batch_size", 128)
        gpu = params.get("gpu", "0")
        debug = params.get("debug", False)
        voc_files = params.get("voc_files", ["smiles"])

        keep_invalid = params.get("keep_invalid", False)
        keep_duplicates = params.get("keep_duplicates", False)
        keep_undesired = params.get("keep_undesired", False)

        if not base_dir:
            raise ValueError("参数 base_dir 不能为空，请先运行 install.sh")

        import drugex.generate as generate_mod
        from drugex.generate import Design
        from drugex.logs.utils import backUpFiles, enable_file_logger

        args, train_args = build_design_args(
            base_dir=base_dir,
            generator=generator,
            input_fragments=input_fragments,
            num_samples=num_samples,
            batch_size=batch_size,
            gpu=gpu,
            debug=debug,
            voc_files=voc_files,
            keep_invalid=keep_invalid,
            keep_duplicates=keep_duplicates,
            keep_undesired=keep_undesired
        )

        os.makedirs(f"{base_dir}/new_molecules", exist_ok=True)

        backup_msg = backUpFiles(base_dir, "new_molecules", (generator,))
        log_settings = enable_file_logger(
            os.path.join(base_dir, "new_molecules"),
            "design.log",
            debug,
            generate_mod.__name__,
            vars(args)
        )
        generate_mod.logSettings = log_settings
        generate_mod.log = log_settings.log
        generate_mod.log.info(backup_msg)

        print(f"[INFO] 开始执行 DrugEx Design(args)", file=sys.stderr)
        print(f"[INFO] generator={generator}, input_file={input_fragments}", file=sys.stderr)
        print(f"[INFO] algorithm={getattr(args, 'algorithm', None)}, mol_type={getattr(args, 'mol_type', None)}", file=sys.stderr)

        Design(args)

        print(f"[INFO] DrugEx Design(args) 执行完成", file=sys.stderr)

        output_tsv = f"{base_dir}/new_molecules/{generator}.tsv"
        molecules = []
        total_generated = 0

        if os.path.exists(output_tsv):
            with open(output_tsv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f, delimiter="\t")
                for row in reader:
                    total_generated += 1
                    mol_entry = {"smiles": row.get("SMILES", "")}
                    for key in row:
                        if key != "SMILES" and row[key] not in (None, ""):
                            try:
                                mol_entry[key.lower()] = float(row[key])
                            except (ValueError, TypeError):
                                mol_entry[key.lower()] = row[key]
                    molecules.append(mol_entry)
        else:
            print(f"[WARN] 未找到输出文件: {output_tsv}", file=sys.stderr)

        result_payload = {
            "success": True,
            "summary": {
                "task": "DrugEx Molecule Generation",
                "mode": "generate",
                "base_dir": base_dir,
                "generator_model": generator,
                "input_fragments": input_fragments,
                "requested_samples": num_samples,
                "total_molecules_generated": total_generated,
                "output_tsv_path": output_tsv if os.path.exists(output_tsv) else None,
                "mol_type": getattr(args, "mol_type", None),
                "algorithm": getattr(args, "algorithm", None),
                "active_targets": getattr(args, "active_targets", []),
            },
            "results": {
                "molecules_preview": molecules[:100],
                "generator_config_json": os.path.join(base_dir, "generators", f"{generator}.json"),
            },
            "errors": None
        }

    except Exception as e:
        tb = traceback.format_exc()
        result_payload = {
            "success": False,
            "error": str(e),
            "traceback": tb
        }
        print(f"[ERROR] {e}", file=sys.stderr)
        print(tb, file=sys.stderr)

    write_json(cwd / "result.json", result_payload)


if __name__ == "__main__":
    main()