#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
import shutil
import warnings
from pathlib import Path

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
os.environ["DEEPCHEM_DATA_DIR"] = "/mnt/shared-storage-gpfs2/chenjiangyu-gpfs02/CAi_copilot/CAi/toolkit/server/tools/deepchem/deepchem_data"
warnings.filterwarnings("ignore")

import pandas as pd
import deepchem as dc
from deepchem.models import BasicMolGANModel as MolGAN
from deepchem.models.optimizers import ExponentialDecay
from tensorflow import one_hot
from rdkit import Chem


def main():
    cwd = Path.cwd()
    result_file = cwd / "result.json"

    # 固定持久化目录（不再使用 job 临时目录保存模型）
    tool_dir = Path(__file__).resolve().parent
    artifact_root = tool_dir / "artifacts" / "molgan"
    model_dir = artifact_root / "molgan_model"

    result_payload = {"success": False, "error": None}

    try:
        params_file = cwd / "params.json"
        if not params_file.exists():
            raise FileNotFoundError("当前沙盒目录下未找到 params.json")
        with open(params_file, "r", encoding="utf-8") as f:
            params = json.load(f)

        num_atoms = int(params.get("num_atoms", 12))
        epochs = int(params.get("epochs", 50))
        dataset_name = params.get("dataset_name", "tox21")
        atom_labels = params.get("atom_labels", [0, 5, 6, 7, 8, 9, 11, 12, 13, 14])

        if dataset_name != "tox21":
            raise ValueError("当前 molgan_train 仅支持 dataset_name='tox21'")

        artifact_root.mkdir(parents=True, exist_ok=True)

        # 清理旧模型，保证结果一致
        if model_dir.exists():
            shutil.rmtree(model_dir)
            print(f"[INFO] 已清理旧模型目录: {model_dir}", file=sys.stderr)

        print(f"[INFO] 启动 MolGAN 训练: dataset={dataset_name}", file=sys.stderr)
        print(f"[INFO] 配置: max_atoms={num_atoms}, epochs={epochs}", file=sys.stderr)

        print("[INFO] 加载 tox21 数据集...", file=sys.stderr)
        tasks, datasets, transformers = dc.molnet.load_tox21()
        df = pd.DataFrame(data={"smiles": datasets[0].ids})

        feat = dc.feat.MolGanFeaturizer(
            max_atom_count=num_atoms,
            atom_labels=atom_labels
        )

        smiles = df["smiles"].values
        filtered_smiles = []
        for s in smiles:
            try:
                mol = Chem.MolFromSmiles(s)
                if mol and mol.GetNumAtoms() <= num_atoms:
                    filtered_smiles.append(s)
            except Exception:
                continue

        print(f"[INFO] 过滤后可用分子数: {len(filtered_smiles)}", file=sys.stderr)
        if len(filtered_smiles) == 0:
            raise ValueError("没有符合条件的训练分子")

        print("[INFO] 开始特征化分子...", file=sys.stderr)
        features = feat.featurize(filtered_smiles)

        valid_indices = [
            i for i, data in enumerate(features)
            if isinstance(data, dc.feat.molecule_featurizers.molgan_featurizer.GraphMatrix)
        ]
        features = [features[i] for i in valid_indices]

        if len(features) == 0:
            raise ValueError("特征化后没有有效分子")

        print(f"[INFO] 有效特征数: {len(features)}", file=sys.stderr)

        print("[INFO] 初始化 MolGAN 模型...", file=sys.stderr)
        learning_rate = ExponentialDecay(0.001, 0.9, 5000)
        gan = MolGAN(learning_rate=learning_rate, vertices=num_atoms)

        dataset = dc.data.NumpyDataset(
            [x.adjacency_matrix for x in features],
            [x.node_features for x in features]
        )

        def iterbatches(n_epochs):
            for _ in range(n_epochs):
                for batch in dataset.iterbatches(batch_size=gan.batch_size, pad_batches=True):
                    adjacency_tensor = one_hot(batch[0], gan.edges)
                    node_tensor = one_hot(batch[1], gan.nodes)
                    yield {
                        gan.data_inputs[0]: adjacency_tensor,
                        gan.data_inputs[1]: node_tensor
                    }

        print("[INFO] 开始训练 MolGAN...", file=sys.stderr)
        gan.fit_gan(iterbatches(epochs), generator_steps=0.2, checkpoint_interval=5000)
        print("[INFO] MolGAN 训练完成", file=sys.stderr)

        model_dir.mkdir(parents=True, exist_ok=True)
        gan.save_checkpoint(model_dir=str(model_dir), max_checkpoints_to_keep=1)

        config = {
            "num_atoms": num_atoms,
            "atom_labels": atom_labels,
            "training_samples": len(features),
            "epochs": epochs,
            "dataset_name": dataset_name
        }
        with open(model_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        result_payload = {
            "success": True,
            "summary": {
                "task": "DeepChem MolGAN Training",
                "dataset": dataset_name,
                "training_samples": len(features),
                "epochs": epochs,
                "num_atoms": num_atoms
            },
            "results": {
                "model_path": str(model_dir),
                "config_path": str(model_dir / "config.json"),
                "atom_labels": atom_labels,
                "message": "模型训练完成，可用于 molgan_generate"
            },
            "errors": None
        }

    except Exception as e:
        result_payload = {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        }
        print(f"[ERROR] MolGAN 训练失败: {e}", file=sys.stderr)

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result_payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()