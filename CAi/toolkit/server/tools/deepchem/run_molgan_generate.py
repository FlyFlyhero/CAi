#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
import warnings
from pathlib import Path
from collections import OrderedDict

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
os.environ["DEEPCHEM_DATA_DIR"] = "/mnt/shared-storage-gpfs2/chenjiangyu-gpfs02/CAi_copilot/CAi/toolkit/server/tools/deepchem/deepchem_data"
warnings.filterwarnings("ignore")

import deepchem as dc
from deepchem.models import BasicMolGANModel as MolGAN
from deepchem.models.optimizers import ExponentialDecay
from rdkit import Chem
from rdkit.Chem import Draw


def main():
    cwd = Path.cwd()
    result_file = cwd / "result.json"

    # 固定持久化目录
    tool_dir = Path(__file__).resolve().parent
    artifact_root = tool_dir / "artifacts" / "molgan"
    model_dir = artifact_root / "molgan_model"

    image_file = cwd / "generated_molecules.png"
    result_payload = {"success": False, "error": None}

    try:
        params_file = cwd / "params.json"
        if not params_file.exists():
            raise FileNotFoundError("当前沙盒目录下未找到 params.json")
        with open(params_file, "r", encoding="utf-8") as f:
            params = json.load(f)

        generate_count = int(params.get("generate_count", 1000))

        if image_file.exists():
            image_file.unlink()

        if not model_dir.exists():
            raise FileNotFoundError(
                f"未找到训练好的 MolGAN 模型目录: {model_dir}。\n"
                f"请先运行 molgan_train。"
            )

        config_path = model_dir / "config.json"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            num_atoms = config["num_atoms"]
            atom_labels = config["atom_labels"]
        else:
            num_atoms = 12
            atom_labels = [0, 5, 6, 7, 8, 9, 11, 12, 13, 14]

        print(f"[INFO] 加载 MolGAN 模型: {model_dir}", file=sys.stderr)
        print(f"[INFO] num_atoms={num_atoms}", file=sys.stderr)

        learning_rate = ExponentialDecay(0.001, 0.9, 5000)
        gan = MolGAN(learning_rate=learning_rate, vertices=num_atoms)

        print("[INFO] 恢复模型权重...", file=sys.stderr)
        gan.restore(model_dir=str(model_dir))

        print(f"[INFO] 生成分子数量={generate_count}", file=sys.stderr)
        generated_data = gan.predict_gan_generator(generate_count)

        feat = dc.feat.MolGanFeaturizer(
            max_atom_count=num_atoms,
            atom_labels=atom_labels
        )
        nmols = feat.defeaturize(generated_data)
        total_generated = len(nmols)

        nmols_valid = [m for m in nmols if m is not None]
        valid_count = len(nmols_valid)

        if valid_count > 0:
            nmols_smiles = [Chem.MolToSmiles(m) for m in nmols_valid]
            nmols_smiles_unique = list(OrderedDict.fromkeys(nmols_smiles))
        else:
            nmols_smiles_unique = []

        unique_count = len(nmols_smiles_unique)

        result_payload = {
            "success": True,
            "summary": {
                "task": "DeepChem MolGAN Generation",
                "generate_count_requested": generate_count,
                "total_generated": total_generated,
                "valid_molecules": valid_count,
                "unique_molecules": unique_count
            },
            "results": {
                "smiles_preview": nmols_smiles_unique[:100],
                "model_source": str(model_dir),
                "note": "MolGAN 训练不稳定，可能生成 0 个有效分子"
            },
            "errors": None
        }

        if unique_count > 0:
            try:
                viz_mols = []
                for s in nmols_smiles_unique[:25]:
                    m = Chem.MolFromSmiles(s)
                    if m:
                        viz_mols.append(m)

                if viz_mols:
                    img = Draw.MolsToGridImage(viz_mols, molsPerRow=5, subImgSize=(200, 200))
                    img.save(str(image_file))
                    result_payload["results"]["visualization"] = str(image_file)
            except Exception as e:
                print(f"[WARN] 生成分子图片失败: {e}", file=sys.stderr)

    except Exception as e:
        result_payload = {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        }
        print(f"[ERROR] MolGAN 生成失败: {e}", file=sys.stderr)

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result_payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()