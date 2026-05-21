#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
CAi tool wrapper for Sc2Mol.

Only supports scaffold-conditioned molecule generation from user-provided
scaffold SMILES.

Expected params.json:

{
  "scaffolds": ["c1ccccc1", "C1CCCCC1", "c1ccncc1"],
  "num_sample": 3,
  "ckpt": "sc2mol_smoke/ckpt-9",
  "max_len": 64
}

Notes:
- "scaffolds" is required.
- One molecule is generated for each scaffold used.
- If num_sample is omitted, all provided scaffolds are used.
- All Sc2Mol source/checkpoint paths are resolved relative to this run.py file.
"""

import csv
import json
import os
import subprocess
import sys
import traceback
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


TOOL_DIR = Path(__file__).resolve().parent
SC2MOL_DIR = TOOL_DIR / "Sc2Mol"

DEFAULT_CKPT = "sc2mol_smoke/ckpt-9"
DEFAULT_MAX_LEN = 64
DEFAULT_VOCAB = "vocab.txt"


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def input_help(extra_suggestions: Optional[List[str]] = None) -> Dict[str, Any]:
    suggestions = [
        'params.json 必须是一个 JSON object，不要直接传入纯文本、CSV 或 Python list。',
        '必须提供字段 "scaffolds"，且值必须是 SMILES 字符串列表。',
        '推荐每个 scaffold 使用 RDKit 可解析的标准 SMILES，例如 "c1ccccc1"、"C1CCCCC1"、"c1ccncc1"。',
        '不要包含表头，例如不要写 "SMILES"、"scaffold" 作为第一行。',
        '如果 scaffold 来自文件，请先在 Agent 侧读取文件内容，并整理成 {"scaffolds": [...]} 的 JSON 格式后再调用本工具。',
        '当前 Sc2Mol vocab 支持的字符有限；Cl、Br、[nH]、[H] 会自动转换，但含有 @、+、\\、/、I、P、B、Si 等字符的 SMILES 可能不被支持。',
        '默认 max_len=64，因此转换后的 SMILES token 长度不能超过 62。'
    ]

    if extra_suggestions:
        suggestions = extra_suggestions + suggestions

    return {
        "expected_format": {
            "scaffolds": "required; list[str]; scaffold SMILES strings",
            "num_sample": "optional; positive integer; default = len(scaffolds)",
            "ckpt": f"optional; default = {DEFAULT_CKPT}",
            "max_len": f"optional; default = {DEFAULT_MAX_LEN}"
        },
        "minimal_example": {
            "scaffolds": ["c1ccccc1", "C1CCCCC1"],
            "num_sample": 2
        },
        "common_issues": suggestions
    }


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(
            "params.json must be a JSON object, for example "
            '{"scaffolds": ["c1ccccc1"], "num_sample": 1}'
        )

    return data


def write_json(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fail(
    message: str,
    errors: Optional[Any] = None,
    suggestions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "success": False,
        "summary": {
            "task": "Sc2Mol scaffold-conditioned molecule generation failed",
            "error": message,
        },
        "results": [],
        "errors": errors if errors is not None else message,
        "input_help": input_help(suggestions),
    }


def tail_text(text: str, n: int = 4000) -> str:
    if text is None:
        return ""
    return text[-n:]


def check_sc2mol_files(ckpt: str) -> None:
    required = [
        SC2MOL_DIR / "eval_from_scaffold.py",
        SC2MOL_DIR / "vocab.txt",
        SC2MOL_DIR / "token_utils.py",
        SC2MOL_DIR / "transformer.py",
        SC2MOL_DIR / "utils.py",
        SC2MOL_DIR / "vae.py",
        SC2MOL_DIR / "vaetransformer.py",
        SC2MOL_DIR / "checkpoints" / f"{ckpt}.index",
        SC2MOL_DIR / "checkpoints" / f"{ckpt}.data-00000-of-00001",
    ]

    missing = []
    for p in required:
        if not p.exists():
            try:
                missing.append(str(p.relative_to(TOOL_DIR)))
            except ValueError:
                missing.append(str(p))

    if missing:
        raise FileNotFoundError(
            "Missing required Sc2Mol files: " + "; ".join(missing)
        )


def run_subprocess(cmd: List[str], cwd: Path) -> subprocess.CompletedProcess:
    eprint("[Sc2Mol] Running command:")
    eprint(" ".join(cmd))

    env = os.environ.copy()
    env.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def load_sc2mol_tokenizer(max_len: int):
    sys.path.insert(0, str(SC2MOL_DIR))
    import token_utils

    tokenizer = token_utils.Tokenizer(
        max_len=max_len,
        init_vocab_txt=str(SC2MOL_DIR / "vocab.txt"),
    )
    return tokenizer, token_utils


def validate_basic_params(params: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    scaffolds = params.get("scaffolds", None)

    if scaffolds is None:
        return None, fail(
            "Missing required parameter: scaffolds",
            suggestions=[
                '请提供 JSON 字段 "scaffolds"，例如 {"scaffolds": ["c1ccccc1"], "num_sample": 1}。'
            ],
        )

    if isinstance(scaffolds, str):
        return None, fail(
            "Parameter scaffolds must be a list of SMILES strings, not a single string",
            suggestions=[
                '如果只有一个 scaffold，也需要写成列表：{"scaffolds": ["c1ccccc1"]}。',
                '如果你现在传入的是文件内容字符串，请先按行读取并整理为列表，例如 {"scaffolds": ["c1ccccc1", "C1CCCCC1"]}。'
            ],
        )

    if not isinstance(scaffolds, list):
        return None, fail(
            "Parameter scaffolds must be a list of SMILES strings",
            suggestions=[
                '正确格式是 {"scaffolds": ["c1ccccc1", "C1CCCCC1"]}，不是字典、数字或纯文本。'
            ],
        )

    if len(scaffolds) == 0:
        return None, fail(
            "Parameter scaffolds must not be empty",
            suggestions=[
                '请至少提供一个 scaffold SMILES，例如 {"scaffolds": ["c1ccccc1"]}。'
            ],
        )

    max_len_raw = params.get("max_len", DEFAULT_MAX_LEN)
    try:
        max_len = int(max_len_raw)
    except Exception:
        return None, fail(
            "max_len must be an integer",
            errors={"max_len": max_len_raw},
            suggestions=['请将 max_len 写成整数，例如 "max_len": 64。'],
        )

    if max_len <= 2:
        return None, fail(
            "max_len must be greater than 2",
            errors={"max_len": max_len},
            suggestions=['推荐保持默认值 "max_len": 64。'],
        )

    ckpt = str(params.get("ckpt", DEFAULT_CKPT)).strip()
    if not ckpt:
        return None, fail(
            "ckpt must not be empty",
            suggestions=[f'推荐使用默认 checkpoint："{DEFAULT_CKPT}"。'],
        )

    num_sample_raw = params.get("num_sample", params.get("num_samples", len(scaffolds)))
    try:
        num_sample = int(num_sample_raw)
    except Exception:
        return None, fail(
            "num_sample must be a positive integer",
            errors={"num_sample": num_sample_raw},
            suggestions=['请将 num_sample 写成正整数，例如 "num_sample": 3。'],
        )

    if num_sample <= 0:
        return None, fail(
            "num_sample must be positive",
            errors={"num_sample": num_sample},
            suggestions=['请将 num_sample 设置为大于 0 的整数。'],
        )

    return {
        "scaffolds": scaffolds,
        "max_len": max_len,
        "ckpt": ckpt,
        "num_sample": num_sample,
    }, None


def validate_and_encode_scaffolds(
    scaffolds: List[Any],
    workdir: Path,
    max_len: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    import numpy as np
    from rdkit import Chem
    from rdkit import RDLogger

    # Suppress RDKit parser warnings in stderr; validation details are returned in result.json.
    RDLogger.DisableLog("rdApp.*")

    tokenizer, token_utils = load_sc2mol_tokenizer(max_len=max_len)
    vocab = tokenizer.vocab

    cleaned_scaffolds: List[str] = []
    encoded = []
    invalid_items = []

    for idx, item in enumerate(scaffolds):
        item_errors = []

        if not isinstance(item, str):
            invalid_items.append({
                "index": idx,
                "value": repr(item),
                "problems": ["该项不是字符串；每个 scaffold 必须是 SMILES 字符串。"]
            })
            continue

        smi = item.strip()
        if not smi:
            invalid_items.append({
                "index": idx,
                "value": item,
                "problems": ["该项为空字符串。"]
            })
            continue

        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            item_errors.append(
                "RDKit 无法解析该 SMILES；请检查环编号、括号、原子符号和键连接是否正确。"
            )

        single = token_utils.multi_to_single(smi)

        unsupported_chars = sorted(set(ch for ch in single if ch not in vocab))
        if unsupported_chars:
            item_errors.append(
                "包含 Sc2Mol vocab 不支持的字符: " + "".join(unsupported_chars)
            )

        if len(single) > max_len - 2:
            item_errors.append(
                f"转换后的 SMILES 长度为 {len(single)}，超过 max_len-2={max_len - 2}。"
            )

        if item_errors:
            invalid_items.append({
                "index": idx,
                "value": smi,
                "converted_for_sc2mol": single,
                "problems": item_errors,
            })
            continue

        encoded.append(tokenizer.chars_to_ids(single))
        cleaned_scaffolds.append(smi)

    if invalid_items:
        return None, fail(
            "Invalid scaffold input format or unsupported scaffold SMILES",
            errors={
                "invalid_count": len(invalid_items),
                "invalid_items": invalid_items[:20],
                "truncated": len(invalid_items) > 20,
            },
            suggestions=[
                '请优先使用简单、标准、无手性标记的 scaffold SMILES，例如 "c1ccccc1"、"C1CCCCC1"。',
                '避免使用含有 @、+、- 以外复杂电荷、/、\\、I、P、B、Si 等当前 vocab 不支持字符的 SMILES。',
                '如果是从 CSV/Excel 复制，请只保留 SMILES 那一列，并去掉表头和空行。',
            ],
        )

    if not encoded:
        return None, fail(
            "No valid scaffolds after validation",
            suggestions=['请至少提供一个可被 RDKit 解析且符合 Sc2Mol vocab 的 scaffold SMILES。'],
        )

    arr = np.asarray(encoded, dtype=np.int64)

    input_path = workdir / "input_scaffolds.npy"
    target_path = workdir / "target_dummy.npy"

    np.save(input_path, arr)
    np.save(target_path, arr)

    return {
        "input": str(input_path),
        "target": str(target_path),
        "num_scaffolds": len(cleaned_scaffolds),
        "cleaned_scaffolds": cleaned_scaffolds,
    }, None


def parse_scaffold_csv(
    csv_path: Path,
    input_scaffolds: List[str],
    max_return: Optional[int] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for i, row in enumerate(reader):
            if max_return is not None and len(rows) >= max_return:
                break

            rows.append({
                "index": i,
                "input_scaffold": input_scaffolds[i] if i < len(input_scaffolds) else None,
                "encoded_scaffold": row.get("scaffold", "").strip(),
                "target_dummy": row.get("target", "").strip(),
                "smiles": row.get("output", "").strip(),
            })

    return rows


def run_scaffold_generation(params: Dict[str, Any], workdir: Path) -> Dict[str, Any]:
    basic, err = validate_basic_params(params)
    if err is not None:
        return err

    assert basic is not None

    scaffolds = basic["scaffolds"]
    max_len = basic["max_len"]
    ckpt = basic["ckpt"]
    requested_num_sample = basic["num_sample"]

    try:
        check_sc2mol_files(ckpt)
    except FileNotFoundError as exc:
        return fail(
            str(exc),
            suggestions=[
                '请确认 Sc2Mol 源码、vocab.txt 和 checkpoint 文件已经放在 tools/sc2mol/Sc2Mol/ 下。',
                f'默认 checkpoint 需要包含 checkpoints/{DEFAULT_CKPT}.index 和 checkpoints/{DEFAULT_CKPT}.data-00000-of-00001。'
            ],
        )

    scaffold_data, err = validate_and_encode_scaffolds(
        scaffolds=scaffolds,
        workdir=workdir,
        max_len=max_len,
    )
    if err is not None:
        return err

    assert scaffold_data is not None

    num_sample = min(requested_num_sample, scaffold_data["num_scaffolds"])
    max_return = int(params.get("max_return", num_sample))

    output_csv = workdir / "sc2mol_scaffold_output.csv"

    cmd = [
        sys.executable,
        "eval_from_scaffold.py",
        "--num_sample", str(num_sample),
        "--input", scaffold_data["input"],
        "--target", scaffold_data["target"],
        "--ckpt", ckpt,
        "--output", str(output_csv),
        "--max_len", str(max_len),
        "--vocab", DEFAULT_VOCAB,
    ]

    proc = run_subprocess(cmd, SC2MOL_DIR)

    if proc.returncode != 0:
        return fail(
            "eval_from_scaffold.py returned non-zero exit code",
            {
                "returncode": proc.returncode,
                "stdout_tail": tail_text(proc.stdout),
                "stderr_tail": tail_text(proc.stderr),
            },
            suggestions=[
                '如果输入 scaffold 已通过校验但模型仍报错，请先减少 num_sample 到 1 做最小测试。',
                '请确认 checkpoint 与当前 Sc2Mol 源码结构匹配。',
            ],
        )

    if not output_csv.exists():
        return fail(
            "Sc2Mol scaffold output CSV was not created",
            {
                "stdout_tail": tail_text(proc.stdout),
                "stderr_tail": tail_text(proc.stderr),
            },
        )

    results = parse_scaffold_csv(
        output_csv,
        input_scaffolds=scaffold_data["cleaned_scaffolds"],
        max_return=max_return,
    )

    summary_note = (
        "Generated molecules are raw Sc2Mol outputs. "
        "Validity and scaffold similarity should be checked downstream if needed."
    )

    if requested_num_sample > scaffold_data["num_scaffolds"]:
        summary_note += (
            f" num_sample={requested_num_sample} is larger than the number of valid scaffolds; "
            f"only {num_sample} scaffolds were used."
        )

    return {
        "success": True,
        "summary": {
            "task": "Sc2Mol scaffold-conditioned molecule generation",
            "mode": "scaffold",
            "checkpoint": ckpt,
            "num_scaffolds": scaffold_data["num_scaffolds"],
            "num_sample_requested": requested_num_sample,
            "num_sample_used": num_sample,
            "num_results_returned": len(results),
            "note": summary_note,
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
                        "example": {"scaffolds": ["c1ccccc1"], "num_sample": 1},
                    },
                    suggestions=[
                        '请使用标准 JSON 格式，字符串必须用双引号，不能用单引号。',
                        '不要在 JSON 末尾添加多余逗号。',
                        '正确示例：{"scaffolds": ["c1ccccc1"], "num_sample": 1}',
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
                        'params.json 顶层必须是 JSON object，例如 {"scaffolds": ["c1ccccc1"]}，不能直接是 ["c1ccccc1"]。'
                    ],
                ),
            )
            return

        result = run_scaffold_generation(params, workdir)
        write_json(result_path, result)

    except Exception as exc:
        eprint("[Sc2Mol] Unhandled exception:")
        eprint(traceback.format_exc())

        write_json(
            result_path,
            fail(
                str(exc),
                {
                    "traceback": traceback.format_exc(),
                },
                suggestions=[
                    '这是未预期异常。请检查 params.json、Sc2Mol checkpoint、conda 环境和依赖是否完整。'
                ],
            ),
        )


if __name__ == "__main__":
    main()
