#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调试脚本：向 DrugEx 工具后端发送测试请求
=========================================
用法：
    python send_request_template.py                # 默认测试 generate
    python send_request_template.py finetune
    python send_request_template.py rl
    python send_request_template.py generate
"""

import json
import sys
import time
from pathlib import Path
import requests

# ============================================================
# 配置区
# ============================================================
WORKER_IP = "127.0.0.1"
PORT = 8001
TOOL_NAME = "drugex"

SCRIPT_DIR = Path(__file__).resolve().parent
PATHS_JSON = SCRIPT_DIR / "paths.json"

if PATHS_JSON.exists():
    with open(PATHS_JSON, "r", encoding="utf-8") as f:
        PATHS = json.load(f)
else:
    PATHS = {}

BASE_DIR = PATHS.get("base_dir", str(SCRIPT_DIR / "DrugEx"))
PRETRAINED_GRAPH = PATHS.get(
    "pretrained_model_graph",
    str(Path(BASE_DIR) / "models/pretrained/graph-trans/Papyrus05.5_graph_trans_PT/Papyrus05.5_graph_trans_PT.pkg")
)
QSAR_A2AR = PATHS.get(
    "qsar_model_a2ar",
    str(Path(BASE_DIR) / "models/qsar/A2AR_RandomForestClassifier/A2AR_RandomForestClassifier_meta.json")
)

# DrugEx RL 里 predictor 需要相对于 base_dir 的路径
if QSAR_A2AR.startswith(BASE_DIR):
    QSAR_A2AR_REL = QSAR_A2AR[len(BASE_DIR):].lstrip("/")
else:
    QSAR_A2AR_REL = QSAR_A2AR

TEST_CASES = {
    "finetune": {
        "base_dir": BASE_DIR,
        "input": "arl",
        "output": "arl",
        "agent_path": PRETRAINED_GRAPH,
        "mol_type": "graph",
        "algorithm": "trans",
        "epochs": 2,
        "batch_size": 32,
        "gpu": "0"
    },
    "rl": {
        "base_dir": BASE_DIR,
        "input": "arl",
        "output": "arl",
        "agent_path": "arl_graph_trans_FT",
        "prior_path": PRETRAINED_GRAPH,
        "predictor": [QSAR_A2AR_REL],
        "active_targets": ["A2AR_RandomForestClassifier"],
        "sa_score": True,
        "mol_type": "graph",
        "algorithm": "trans",
        "epochs": 2,
        "batch_size": 32,
        "gpu": "0"
    },
    "generate": {
        "base_dir": BASE_DIR,
        "input_fragments": "arl_test_graph.txt",
        "generator": "arl_graph_trans_RL",
        "num_samples": 50,
        "batch_size": 128,
        "gpu": "0"
    }
}

TIMEOUT_SECS = 60 * 60 * 4
POLL_INTERVAL = 10
# ============================================================

BASE_URL = f"http://{WORKER_IP}:{PORT}"
JOB_URL = f"{BASE_URL}/job"
NO_PROXY = {"http": None, "https": None}


def submit_job(action, payload):
    run_url = f"{BASE_URL}/run/{TOOL_NAME}/{action}"
    print(f"  [1/3] 提交任务到 {run_url}")
    r = requests.post(run_url, json=payload, timeout=20, proxies=NO_PROXY)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"任务提交失败: {data['error']}")
    job_id = data["job_id"]
    print(f"        Job ID: {job_id}")
    return job_id


def poll_job(job_id):
    print(f"  [2/3] 等待任务完成（最长 {TIMEOUT_SECS}s）…")
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > TIMEOUT_SECS:
            raise TimeoutError(f"超时：任务 {job_id} 在 {TIMEOUT_SECS}s 内未完成")

        try:
            r = requests.get(f"{JOB_URL}/{job_id}", timeout=20, proxies=NO_PROXY)
            status = r.json()
        except Exception as e:
            print(f"        [WARN] 查询失败: {e}", flush=True)
            time.sleep(POLL_INTERVAL)
            continue

        state = status.get("status")
        print(f"        [{elapsed:6.1f}s] 状态: {state}", flush=True)

        if state == "running":
            time.sleep(POLL_INTERVAL)
        elif state == "failed":
            raise RuntimeError(f"服务器崩溃: {status.get('data')}")
        elif state == "finished":
            return status.get("data") or {}
        else:
            time.sleep(POLL_INTERVAL)


def print_result(result):
    print("  [3/3] 结果:")

    if not result.get("success"):
        top_error = result.get("error")
        if top_error:
            print(f"    ❌ 错误: {top_error}")

        tb = result.get("traceback")
        if tb:
            print("\n    --- Traceback ---")
            for line in tb.splitlines():
                print(f"    {line}")

        errors_list = result.get("errors")
        if errors_list and isinstance(errors_list, list):
            print("\n    --- Errors ---")
            for err in errors_list:
                print(f"    {err}")
        return

    summary = result.get("summary", {})
    if summary:
        print("    --- Summary ---")
        for k, v in summary.items():
            print(f"    {k}: {v}")

    results = result.get("results", {})
    model_path = results.get("model_path")
    if model_path:
        print(f"\n    输出模型: {model_path}")

    molecules = results.get("molecules_preview", [])
    if molecules:
        print(f"\n    --- Molecules (前 5 条 / 共 {len(molecules)} 条预览) ---")
        for mol in molecules[:5]:
            print(f"    {mol}")

    print("\n    ✅ 测试成功！")


def run_one_test(action, payload):
    print(f"\n{'=' * 60}")
    print(f"  测试 action: {action}")
    print(f"{'=' * 60}")
    try:
        job_id = submit_job(action, payload)
        result = poll_job(job_id)
        print_result(result)
    except Exception as e:
        print(f"\n    ❌ 测试失败: {e}")


def main():
    print(f"工具: {TOOL_NAME} | 后端: {BASE_URL}")
    print(f"BASE_DIR: {BASE_DIR}")

    if len(sys.argv) > 1:
        action = sys.argv[1]
        if action not in TEST_CASES:
            print(f"❌ 未知 action: {action}，可选: {list(TEST_CASES.keys())}")
            sys.exit(1)
        run_one_test(action, TEST_CASES[action])
    else:
        # 默认只跑 generate，最快
        run_one_test("generate", TEST_CASES["generate"])

    print(f"\n{'=' * 60}")
    print("测试完成！")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()