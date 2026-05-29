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

import numpy as np
import deepchem as dc
from deepchem.models import SeqToSeq, MultitaskClassifier
from deepchem.metrics import Metric, roc_auc_score


def main():
    cwd = Path.cwd()
    result_file = cwd / "result.json"

    # 固定持久化目录
    tool_dir = Path(__file__).resolve().parent
    artifact_root = tool_dir / "artifacts" / "seq2seq"
    model_dir = artifact_root / "fingerprint"
    tokens_file = artifact_root / "tokens.json"

    # 当前 job 的临时输出目录
    classifier_eval_dir = cwd / "classifier_eval"
    train_embed_path = cwd / "train_embeddings.npy"
    valid_embed_path = cwd / "valid_embeddings.npy"

    result_payload = {"success": False, "error": None}

    try:
        # 读取参数
        params_file = cwd / "params.json"
        if not params_file.exists():
            raise FileNotFoundError("当前沙盒目录下未找到 params.json")
        with open(params_file, "r", encoding="utf-8") as f:
            params = json.load(f)

        classifier_epochs = int(params.get("classifier_epochs", 10))
        batch_size = int(params.get("batch_size", 100))
        embedding_dimension = int(params.get("embedding_dimension", 256))
        encoder_layers = int(params.get("encoder_layers", 2))
        decoder_layers = int(params.get("decoder_layers", 2))
        dataset_name = params.get("dataset_name", "muv")

        if not model_dir.exists():
            raise FileNotFoundError(
                f"未找到模型目录: {model_dir}。\n请先运行 seq2seq_train。"
            )
        if not tokens_file.exists():
            raise FileNotFoundError(
                f"未找到 tokens 文件: {tokens_file}。\n请先运行 seq2seq_train。"
            )

        if dataset_name != "muv":
            raise ValueError("当前 seq2seq_evaluate 仅支持 dataset_name='muv'")

        print(f"[INFO] 使用持久化模型目录: {model_dir}", file=sys.stderr)
        print(f"[INFO] 使用 tokens 文件: {tokens_file}", file=sys.stderr)

        with open(tokens_file, "r", encoding="utf-8") as f:
            tokens = json.load(f)

        print("[INFO] 加载 MUV 数据集...", file=sys.stderr)
        tasks, datasets, transformers = dc.molnet.load_muv(split="stratified")
        train_dataset, valid_dataset, test_dataset = datasets
        train_smiles = train_dataset.ids
        valid_smiles = valid_dataset.ids

        max_length = max(len(s) for s in train_smiles)

        print("[INFO] 重建 Seq2Seq 模型并恢复权重...", file=sys.stderr)
        model = SeqToSeq(
            tokens,
            tokens,
            max_length,
            encoder_layers=encoder_layers,
            decoder_layers=decoder_layers,
            embedding_dimension=embedding_dimension,
            model_dir=str(model_dir),
            batch_size=batch_size
        )
        model.restore()
        print("[INFO] 模型权重恢复完成", file=sys.stderr)

        print("[INFO] 生成嵌入向量...", file=sys.stderr)
        train_embeddings = model.predict_embeddings(train_smiles)
        valid_embeddings = model.predict_embeddings(valid_smiles)

        train_embed_dataset = dc.data.NumpyDataset(
            train_embeddings, train_dataset.y,
            train_dataset.w.astype(np.float32), train_dataset.ids
        )
        valid_embed_dataset = dc.data.NumpyDataset(
            valid_embeddings, valid_dataset.y,
            valid_dataset.w.astype(np.float32), valid_dataset.ids
        )

        # 每次评估都清理本 job 中旧的分类器目录
        if classifier_eval_dir.exists():
            shutil.rmtree(classifier_eval_dir)

        print("[INFO] 训练下游分类器...", file=sys.stderr)
        classifier = MultitaskClassifier(
            n_tasks=len(tasks),
            n_features=embedding_dimension,
            layer_sizes=[512],
            model_dir=str(classifier_eval_dir)
        )
        classifier.fit(train_embed_dataset, nb_epoch=classifier_epochs)

        print("[INFO] 评估分类性能...", file=sys.stderr)
        metric = Metric(roc_auc_score, np.mean, mode="classification")
        train_score = classifier.evaluate(train_embed_dataset, [metric], transformers)
        valid_score = classifier.evaluate(valid_embed_dataset, [metric], transformers)

        train_auc = float(train_score["mean-roc_auc_score"])
        valid_auc = float(valid_score["mean-roc_auc_score"])

        np.save(train_embed_path, train_embeddings)
        np.save(valid_embed_path, valid_embeddings)

        result_payload = {
            "success": True,
            "summary": {
                "task": "DeepChem Seq2Seq Evaluation",
                "dataset": dataset_name,
                "tasks": len(tasks),
                "classifier_epochs": classifier_epochs,
                "train_roc_auc": train_auc,
                "valid_roc_auc": valid_auc
            },
            "results": {
                "artifact_root": str(artifact_root),
                "model_loaded": str(model_dir),
                "tokens_file": str(tokens_file),
                "embeddings_shape": {
                    "train": list(train_embeddings.shape),
                    "valid": list(valid_embeddings.shape)
                },
                "files_generated": [
                    str(train_embed_path),
                    str(valid_embed_path),
                    str(classifier_eval_dir)
                ],
                "note": "验证集 AUC > 0.7 通常表示学到较有意义的分子表示"
            },
            "errors": None
        }

    except Exception as e:
        result_payload = {
            "success": False,
            "error": str(e),
            "hint": "请确保已先运行 seq2seq_train 完成模型训练"
        }
        print(f"[ERROR] Seq2Seq 验证失败: {e}", file=sys.stderr)

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result_payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()