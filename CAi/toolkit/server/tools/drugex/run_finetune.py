#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
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


def main():
    cwd = Path.cwd()
    result_payload = {"success": False, "error": None}

    try:
        params_file = cwd / "params.json"
        if not params_file.exists():
            raise FileNotFoundError("当前沙盒目录下未找到 params.json")

        with open(params_file, "r", encoding="utf-8") as f:
            params = json.load(f)

        print(f"[INFO] finetune action 收到参数: {list(params.keys())}", file=sys.stderr)

        defaults = load_default_paths()

        base_dir = params.get("base_dir", defaults.get("base_dir", ""))
        input_prefix = params.get("input", defaults.get("default_input_prefix", "arl"))
        output_prefix = params.get("output", input_prefix)
        agent_path = params.get("agent_path", defaults.get("pretrained_model_graph", ""))
        mol_type = params.get("mol_type", "graph")
        algorithm = params.get("algorithm", "trans")
        epochs = params.get("epochs", 2)
        batch_size = params.get("batch_size", 32)
        gpu = params.get("gpu", "0")
        patience = params.get("patience", 50)
        voc_files = params.get("voc_files", None)
        debug = params.get("debug", False)

        if not base_dir:
            raise ValueError("参数 base_dir 不能为空，请先运行 install.sh")
        if not agent_path:
            raise ValueError("参数 agent_path 不能为空，需要指定预训练模型路径")

        if voc_files is None:
            voc_files = [input_prefix.split("_")[0]]

        use_gpus = [int(x) for x in str(gpu).split(",") if str(x).strip()]

        output_long = "_".join([output_prefix, mol_type, algorithm, "FT"])
        output_file_base = f"{base_dir}/generators/{output_long}"

        os.makedirs(f"{base_dir}/generators", exist_ok=True)

        import drugex.train as train_mod
        from drugex.train import Finetune
        from drugex.logs.utils import backUpFiles, enable_file_logger

        args = Namespace(
            base_dir=base_dir,
            input=input_prefix,
            output=output_prefix,
            voc_files=voc_files,
            agent_path=agent_path,
            prior_path=None,
            training_mode="FT",
            mol_type=mol_type,
            algorithm=algorithm,
            use_gru=False,
            epochs=epochs,
            batch_size=batch_size,
            use_gpus=use_gpus,
            patience=patience,
            n_samples=-1,
            epsilon=0.1,
            beta=0.0,
            scheme="PRCD",
            predictor=[],
            activity_threshold=6.5,
            active_targets=[],
            inactive_targets=[],
            window_targets=[],
            ligand_efficiency=False,
            le_thresholds=[0.0, 0.5],
            lipophilic_efficiency=False,
            lipe_thresholds=[4.0, 6.0],
            qed=False,
            uniqueness=False,
            sa_score=False,
            ra_score=False,
            molecular_weight=False,
            mw_thresholds=[200, 600],
            logP=False,
            logP_thresholds=[-5, 5],
            tpsa=False,
            tpsa_thresholds=[0, 140],
            similarity_mol=None,
            similarity_type="fraggle",
            similarity_threshold=0.5,
            similarity_tversky_weights=[0.7, 0.3],
            debug=debug,
            targets=[],
            output_long=output_long,
            output_file_base=output_file_base,
        )

        backup_msg = backUpFiles(base_dir, "generators", (output_long,))
        log_settings = enable_file_logger(
            os.path.join(base_dir, "generators"),
            output_long + ".log",
            debug,
            train_mod.__name__,
            vars(args)
        )
        train_mod.logSettings = log_settings
        train_mod.log = log_settings.log
        train_mod.log.info(backup_msg)

        write_json(Path(f"{output_file_base}.json"), vars(args))

        print(f"[INFO] 开始执行 DrugEx Finetune(args)()", file=sys.stderr)
        Finetune(args)()
        print(f"[INFO] DrugEx Finetune(args)() 执行完成", file=sys.stderr)

        model_path = f"{base_dir}/generators/{output_long}.pkg"
        model_exists = os.path.exists(model_path)

        result_payload = {
            "success": True,
            "summary": {
                "task": "DrugEx Fine-tuning",
                "mode": "finetune",
                "base_dir": base_dir,
                "input_prefix": input_prefix,
                "agent_model": agent_path,
                "mol_type": mol_type,
                "algorithm": algorithm,
                "epochs": epochs,
                "batch_size": batch_size,
                "output_model": model_path if model_exists else "未找到输出模型",
                "output_model_found": model_exists
            },
            "results": {
                "model_path": model_path,
                "output_name": output_long
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