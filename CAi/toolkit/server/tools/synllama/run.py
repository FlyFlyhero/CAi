#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
CAi tool wrapper for SynLlama.

This wrapper only supports raw LLM inference from user-provided target SMILES.

Expected params.json:

{
  "smiles": ["CCOc1ccc2nc(S(N)(=O)=O)sc2c1"],
  "sample_mode": "frozen_only",
  "gpus": 1
}

Optional fields:
- model: "91rxns" only by default
- sample_mode: frozen_only, frugal, greedy, low_only, medium_only, high_only
- max_molecules: positive integer, default 5
- gpus: positive integer, default 1

All paths are resolved relative to this run.py file.
"""

import json
import os
import pickle
import subprocess
import sys
import traceback
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


TOOL_DIR = Path(__file__).resolve().parent
SYNLlama_DIR = TOOL_DIR / "SynLlama"

MODEL_MAP = {
    "91rxns": "synllama-data/inference/model/SynLlama-1B-2M-91rxns",
}

DEFAULT_MODEL = "91rxns"
DEFAULT_SAMPLE_MODE = "frozen_only"
DEFAULT_GPUS = 1
DEFAULT_MAX_MOLECULES = 5

VALID_SAMPLE_MODES = {
    "frozen_only",
    "frugal",
    "greedy",
    "low_only",
    "medium_only",
    "high_only",
}


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def input_help(extra_suggestions: Optional[List[str]] = None) -> Dict[str, Any]:
    suggestions = [
        'params.json 顶层必须是 JSON object，不能直接传入纯文本、CSV 或 Python list。',
        '必须提供字段 "smiles"，且值必须是目标分子 SMILES 字符串列表。',
        '如果只有一个分子，也要写成列表，例如 {"smiles": ["CCOc1ccc2nc(S(N)(=O)=O)sc2c1"]}。',
        '不要包含表头，例如不要把 "SMILES" 或 "smiles" 作为列表第一项。',
        '每个 SMILES 必须能被 RDKit 解析。',
        '默认建议一次输入 1–5 个分子，避免 LLM 推理过慢或显存不足。',
        f'默认 sample_mode="{DEFAULT_SAMPLE_MODE}"；可选值包括 frozen_only、frugal、greedy、low_only、medium_only、high_only。',
    ]
    if extra_suggestions:
        suggestions = extra_suggestions + suggestions

    return {
        "expected_format": {
            "smiles": "required; list[str]; target molecule SMILES strings",
            "sample_mode": f"optional; default = {DEFAULT_SAMPLE_MODE}",
            "model": f"optional; default = {DEFAULT_MODEL}; currently supports: {list(MODEL_MAP.keys())}",
            "gpus": f"optional; positive integer; default = {DEFAULT_GPUS}",
            "max_molecules": f"optional; positive integer; default = {DEFAULT_MAX_MOLECULES}",
        },
        "minimal_example": {
            "smiles": ["CCOc1ccc2nc(S(N)(=O)=O)sc2c1"],
            "sample_mode": "frozen_only",
            "gpus": 1,
        },
        "common_issues": suggestions,
    }


def fail(
    message: str,
    errors: Optional[Any] = None,
    suggestions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "success": False,
        "summary": {
            "task": "SynLlama raw synthesis-pathway inference failed",
            "error": message,
        },
        "results": [],
        "errors": errors if errors is not None else message,
        "input_help": input_help(suggestions),
    }


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(
            'params.json must be a JSON object, for example {"smiles": ["CCOc1ccc2nc(S(N)(=O)=O)sc2c1"]}'
        )

    return data


def write_json(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def tail_text(text: str, n: int = 4000) -> str:
    if text is None:
        return ""
    return text[-n:]


def check_synllama_files(model_key: str) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    if model_key not in MODEL_MAP:
        return None, fail(
            f"Unsupported model: {model_key}",
            suggestions=[
                f'当前只集成了模型 "{DEFAULT_MODEL}"。请使用 {{"model": "{DEFAULT_MODEL}"}} 或省略 model 字段。'
            ],
        )

    model_dir = SYNLlama_DIR / MODEL_MAP[model_key]

    required = [
        SYNLlama_DIR / "synllama" / "__init__.py",
        SYNLlama_DIR / "synllama" / "llm" / "__init__.py",
        SYNLlama_DIR / "synllama" / "llm" / "parallel_inference.py",
        SYNLlama_DIR / "synllama" / "llm" / "vars.py",
        model_dir / "config.json",
        model_dir / "generation_config.json",
        model_dir / "pytorch_model.bin",
        model_dir / "special_tokens_map.json",
        model_dir / "tokenizer_config.json",
        model_dir / "tokenizer.json",
    ]

    missing = []
    for p in required:
        if not p.exists():
            try:
                missing.append(str(p.relative_to(TOOL_DIR)))
            except ValueError:
                missing.append(str(p))

    if missing:
        return None, fail(
            "Missing required SynLlama files",
            errors={"missing_files": missing},
            suggestions=[
                '请确认 SynLlama 最小源码和模型已复制到 tools/synllama/SynLlama/ 下。',
                '默认模型目录应为 SynLlama/synllama-data/inference/model/SynLlama-1B-2M-91rxns/。',
            ],
        )

    return model_dir, None


def normalize_smiles_input(params: Dict[str, Any]) -> Tuple[Optional[List[str]], Optional[Dict[str, Any]]]:
    smiles = params.get("smiles", params.get("target_smiles", params.get("smiles_list", None)))

    if smiles is None:
        return None, fail(
            "Missing required parameter: smiles",
            suggestions=[
                '请提供 JSON 字段 "smiles"，例如 {"smiles": ["CCOc1ccc2nc(S(N)(=O)=O)sc2c1"]}。'
            ],
        )

    if isinstance(smiles, str):
        return None, fail(
            "Parameter smiles must be a list of SMILES strings, not a single string",
            suggestions=[
                '如果只有一个分子，也需要写成列表：{"smiles": ["CCOc1ccc2nc(S(N)(=O)=O)sc2c1"]}。',
                '如果你现在传入的是文件内容字符串，请先按行读取并整理为列表。',
            ],
        )

    if not isinstance(smiles, list):
        return None, fail(
            "Parameter smiles must be a list of SMILES strings",
            suggestions=[
                '正确格式是 {"smiles": ["CCOc1ccc2nc(S(N)(=O)=O)sc2c1"]}，不是字典、数字或纯文本。'
            ],
        )

    if len(smiles) == 0:
        return None, fail(
            "Parameter smiles must not be empty",
            suggestions=[
                '请至少提供一个目标分子 SMILES。'
            ],
        )

    cleaned: List[str] = []
    invalid_items = []

    try:
        from rdkit import Chem
        from rdkit import RDLogger
        RDLogger.DisableLog("rdApp.*")
    except Exception as exc:
        return None, fail(
            "Failed to import RDKit for SMILES validation",
            errors=str(exc),
            suggestions=[
                '请确认 synllama conda 环境中已安装 rdkit。'
            ],
        )

    for idx, item in enumerate(smiles):
        if not isinstance(item, str):
            invalid_items.append({
                "index": idx,
                "value": repr(item),
                "problems": ["该项不是字符串；每个目标分子必须是 SMILES 字符串。"],
            })
            continue

        smi = item.strip()
        if not smi:
            invalid_items.append({
                "index": idx,
                "value": item,
                "problems": ["该项为空字符串。"],
            })
            continue

        if smi.lower() in {"smiles", "smile", "target", "target_smiles"}:
            invalid_items.append({
                "index": idx,
                "value": smi,
                "problems": ["该项看起来像表头，不是分子 SMILES。请去掉表头。"],
            })
            continue

        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            invalid_items.append({
                "index": idx,
                "value": smi,
                "problems": ["RDKit 无法解析该 SMILES；请检查环编号、括号、原子符号和键连接是否正确。"],
            })
            continue

        cleaned.append(smi)

    if invalid_items:
        return None, fail(
            "Invalid SMILES input",
            errors={
                "invalid_count": len(invalid_items),
                "invalid_items": invalid_items[:20],
                "truncated": len(invalid_items) > 20,
            },
            suggestions=[
                '请只提供有效目标分子的 SMILES 列表，不要包含表头、空行或非字符串项。',
                '如果是从 CSV/Excel 复制，请只保留 SMILES 那一列，并去掉表头。',
            ],
        )

    return cleaned, None


def validate_runtime_params(params: Dict[str, Any], n_smiles: int) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    model_key = str(params.get("model", DEFAULT_MODEL)).strip()
    sample_mode = str(params.get("sample_mode", DEFAULT_SAMPLE_MODE)).strip()

    if sample_mode not in VALID_SAMPLE_MODES:
        return None, fail(
            f"Invalid sample_mode: {sample_mode}",
            suggestions=[
                f"sample_mode 可选值为：{', '.join(sorted(VALID_SAMPLE_MODES))}。",
                f'推荐先使用最轻量模式："{DEFAULT_SAMPLE_MODE}"。'
            ],
        )

    try:
        gpus = int(params.get("gpus", DEFAULT_GPUS))
    except Exception:
        return None, fail(
            "gpus must be a positive integer",
            errors={"gpus": params.get("gpus")},
            suggestions=['请将 gpus 写成正整数，例如 "gpus": 1。'],
        )

    if gpus <= 0:
        return None, fail(
            "gpus must be positive",
            errors={"gpus": gpus},
            suggestions=['请将 gpus 设置为大于 0 的整数。'],
        )

    try:
        max_molecules = int(params.get("max_molecules", DEFAULT_MAX_MOLECULES))
    except Exception:
        return None, fail(
            "max_molecules must be a positive integer",
            errors={"max_molecules": params.get("max_molecules")},
            suggestions=['请将 max_molecules 写成正整数，例如 "max_molecules": 5。'],
        )

    if max_molecules <= 0:
        return None, fail(
            "max_molecules must be positive",
            errors={"max_molecules": max_molecules},
        )

    if n_smiles > max_molecules:
        return None, fail(
            f"Too many SMILES: got {n_smiles}, max_molecules={max_molecules}",
            suggestions=[
                f"为了避免 LLM 推理过慢或显存不足，默认最多一次处理 {DEFAULT_MAX_MOLECULES} 个分子。",
                '如果你确认要批量处理，可以显式提高 max_molecules，但建议先小批量测试。',
            ],
        )

    return {
        "model": model_key,
        "sample_mode": sample_mode,
        "gpus": gpus,
        "max_molecules": max_molecules,
    }, None


def run_subprocess(cmd: List[str], cwd: Path) -> subprocess.CompletedProcess:
    eprint("[SynLlama] Running command:")
    eprint(" ".join(cmd))

    env = os.environ.copy()

    # Make the bundled SynLlama package importable without relying on pip install -e.
    old_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SYNLlama_DIR) + (os.pathsep + old_pythonpath if old_pythonpath else "")

    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def decode_pickle_output(pkl_path: Path) -> Dict[str, Any]:
    with pkl_path.open("rb") as f:
        data = pickle.load(f)

    if not isinstance(data, dict):
        return {"_raw_output": repr(data)}

    return data


def run_synllama(params: Dict[str, Any], workdir: Path) -> Dict[str, Any]:
    smiles, err = normalize_smiles_input(params)
    if err is not None:
        return err
    assert smiles is not None

    runtime, err = validate_runtime_params(params, n_smiles=len(smiles))
    if err is not None:
        return err
    assert runtime is not None

    model_dir, err = check_synllama_files(runtime["model"])
    if err is not None:
        return err
    assert model_dir is not None

    input_smi = workdir / "input.smi"
    output_pkl = workdir / "synllama_output.pkl"

    with input_smi.open("w", encoding="utf-8") as f:
        for smi in smiles:
            f.write(smi + "\n")

    model_rel = str(model_dir.relative_to(SYNLlama_DIR))

    cmd = [
        sys.executable,
        "synllama/llm/parallel_inference.py",
        "--model_path", model_rel,
        "--smiles_path", str(input_smi),
        "--save_path", str(output_pkl),
        "--sample_mode", runtime["sample_mode"],
        "--gpus", str(runtime["gpus"]),
    ]

    proc = run_subprocess(cmd, SYNLlama_DIR)

    if proc.returncode != 0:
        return fail(
            "SynLlama parallel_inference.py returned non-zero exit code",
            errors={
                "returncode": proc.returncode,
                "stdout_tail": tail_text(proc.stdout),
                "stderr_tail": tail_text(proc.stderr),
            },
            suggestions=[
                '请先确认 GPU 空闲且显存足够。',
                '建议先使用 sample_mode=frozen_only 且一次只输入 1 个 SMILES。',
                '如果报模型缺失，请确认模型权重已放在 SynLlama/synllama-data/inference/model/SynLlama-1B-2M-91rxns/。'
            ],
        )

    if not output_pkl.exists():
        return fail(
            "SynLlama output pickle was not created",
            errors={
                "stdout_tail": tail_text(proc.stdout),
                "stderr_tail": tail_text(proc.stderr),
            },
        )

    raw_data = decode_pickle_output(output_pkl)

    results = []
    for idx, smi in enumerate(smiles):
        predictions = raw_data.get(smi, None)
        results.append({
            "index": idx,
            "smiles": smi,
            "predictions": predictions,
        })

    return {
        "success": True,
        "summary": {
            "task": "SynLlama raw synthesis-pathway inference",
            "model": runtime["model"],
            "sample_mode": runtime["sample_mode"],
            "num_input_smiles": len(smiles),
            "num_results_returned": len(results),
            "output_pkl": str(output_pkl),
            "note": (
                "These are raw SynLlama LLM outputs. "
                "Downstream reconstruction or route validation is not included in this lightweight CAi tool."
            ),
        },
        "results": results,
        "errors": None,
        "logs": {
            "stdout_tail": tail_text(proc.stdout),
            "stderr_tail": tail_text(proc.stderr),
        },
    }


def main() -> None:
    workdir = Path.cwd()
    params_path = workdir / "params.json"
    result_path = workdir / "result.json"

    try:
        if not params_path.exists():
            write_json(
                result_path,
                fail(
                    "params.json not found in job sandbox",
                    suggestions=['请确认 JobManager 已将输入参数写入当前沙盒目录下的 params.json。'],
                ),
            )
            return

        try:
            params = read_json(params_path)
        except JSONDecodeError as exc:
            write_json(
                result_path,
                fail(
                    "params.json is not valid JSON",
                    errors={
                        "json_error": str(exc),
                        "example": {"smiles": ["CCOc1ccc2nc(S(N)(=O)=O)sc2c1"]},
                    },
                    suggestions=[
                        '请使用标准 JSON 格式，字符串必须用双引号，不能用单引号。',
                        '不要在 JSON 末尾添加多余逗号。',
                    ],
                ),
            )
            return
        except ValueError as exc:
            write_json(
                result_path,
                fail(
                    str(exc),
                    suggestions=[
                        'params.json 顶层必须是 JSON object，例如 {"smiles": ["CCOc1ccc2nc(S(N)(=O)=O)sc2c1"]}。'
                    ],
                ),
            )
            return

        result = run_synllama(params, workdir)
        write_json(result_path, result)

    except Exception as exc:
        eprint("[SynLlama] Unhandled exception:")
        eprint(traceback.format_exc())
        write_json(
            result_path,
            fail(
                str(exc),
                errors={"traceback": traceback.format_exc()},
                suggestions=[
                    '这是未预期异常。请检查 params.json、SynLlama 模型目录、conda 环境和 GPU 状态。'
                ],
            ),
        )


if __name__ == "__main__":
    main()
