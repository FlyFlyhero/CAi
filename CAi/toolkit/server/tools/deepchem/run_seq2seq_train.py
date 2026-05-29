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

import deepchem as dc
from deepchem.models import SeqToSeq
from deepchem.models.optimizers import ExponentialDecay


def main():
    cwd = Path.cwd()
    result_file = cwd / "result.json"

    # 固定持久化目录
    tool_dir = Path(__file__).resolve().parent
    artifact_root = tool_dir / "artifacts" / "seq2seq"
    model_dir = artifact_root / "fingerprint"
    tokens_file = artifact_root / "tokens.json"

    result_payload = {"success": False, "error": None}

    try:
        # 读取参数
        params_file = cwd / "params.json"
        if not params_file.exists():
            raise FileNotFoundError("当前沙盒目录下未找到 params.json")
        with open(params_file, "r", encoding="utf-8") as f:
            params = json.load(f)

        epochs = int(params.get("epochs", 40))
        batch_size = int(params.get("batch_size", 100))
        embedding_dimension = int(params.get("embedding_dimension", 256))
        encoder_layers = int(params.get("encoder_layers", 2))
        decoder_layers = int(params.get("decoder_layers", 2))
        dataset_name = params.get("dataset_name", "muv")

        print(f"[INFO] 启动 Seq2Seq 训练: dataset={dataset_name}", file=sys.stderr)

        # 创建 artifacts 目录
        artifact_root.mkdir(parents=True, exist_ok=True)

        # 清理旧模型
        if model_dir.exists():
            shutil.rmtree(model_dir)
            print(f"[INFO] 已清理旧模型目录: {model_dir}", file=sys.stderr)
        if tokens_file.exists():
            tokens_file.unlink()
            print(f"[INFO] 已清理旧 tokens 文件: {tokens_file}", file=sys.stderr)

        # 当前仅支持 MUV（与你原始脚本一致）
        if dataset_name != "muv":
            raise ValueError("当前 seq2seq_train 仅支持 dataset_name='muv'")

        print("[INFO] 加载 MUV 数据集...", file=sys.stderr)
        tasks, datasets, transformers = dc.molnet.load_muv(split="stratified")
        train_dataset, valid_dataset, test_dataset = datasets
        train_smiles = train_dataset.ids
        valid_smiles = valid_dataset.ids

        print(f"[INFO] 训练集={len(train_smiles)} 验证集={len(valid_smiles)}", file=sys.stderr)

        print("[INFO] 构建 SMILES 字符集...", file=sys.stderr)
        tokens = set()
        for s in train_smiles:
            tokens = tokens.union(set(c for c in s))
        tokens = sorted(list(tokens))

        with open(tokens_file, "w", encoding="utf-8") as f:
            json.dump(tokens, f, ensure_ascii=False, indent=2)

        max_length = max(len(s) for s in train_smiles)
        batches_per_epoch = len(train_smiles) / batch_size

        print(
            f"[INFO] 配置: max_length={max_length}, embedding={embedding_dimension}, epochs={epochs}",
            file=sys.stderr
        )

        model = SeqToSeq(
            tokens,
            tokens,
            max_length,
            encoder_layers=encoder_layers,
            decoder_layers=decoder_layers,
            embedding_dimension=embedding_dimension,
            model_dir=str(model_dir),
            batch_size=batch_size,
            learning_rate=ExponentialDecay(0.001, 0.9, batches_per_epoch)
        )

        def generate_sequences(n_epochs):
            for _ in range(n_epochs):
                for s in train_smiles:
                    yield (s, s)

        print("[INFO] 开始训练 Seq2Seq 自编码器...", file=sys.stderr)
        model.fit_sequences(generate_sequences(epochs))
        print("[INFO] Seq2Seq 训练完成", file=sys.stderr)

        print("[INFO] 验证重建准确率...", file=sys.stderr)
        eval_count = min(500, len(valid_smiles))
        predicted = model.predict_from_sequences(valid_smiles[:eval_count])
        correct = sum(1 for s, p in zip(valid_smiles[:eval_count], predicted) if "".join(p) == s)
        accuracy = correct / float(eval_count)

        result_payload = {
            "success": True,
            "summary": {
                "task": "DeepChem Seq2Seq Training",
                "dataset": dataset_name,
                "train_samples": len(train_smiles),
                "valid_samples": len(valid_smiles),
                "epochs": epochs,
                "batch_size": batch_size,
                "embedding_dimension": embedding_dimension,
                "reconstruction_accuracy": accuracy
            },
            "results": {
                "artifact_root": str(artifact_root),
                "model_dir": str(model_dir),
                "tokens_file": str(tokens_file),
                "vocab_size": len(tokens),
                "max_length": max_length,
                "encoder_layers": encoder_layers,
                "decoder_layers": decoder_layers,
                "correct_reconstructions": correct,
                "evaluated_reconstructions": eval_count,
                "status": "模型已就绪，可用于 seq2seq_evaluate"
            },
            "errors": None
        }

    except Exception as e:
        result_payload = {"success": False, "error": str(e)}
        print(f"[ERROR] Seq2Seq 训练失败: {e}", file=sys.stderr)

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result_payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()