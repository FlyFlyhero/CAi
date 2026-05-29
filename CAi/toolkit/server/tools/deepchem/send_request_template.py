#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import time
import requests

WORKER_IP = "127.0.0.1"
PORT = 8001
TOOL_NAME = "deepchem"

TEST_CASES = {
    "seq2seq_train": {
        "dataset_name": "muv",
        "epochs": 40,
        "batch_size": 100,
        "embedding_dimension": 256,
        "encoder_layers": 2,
        "decoder_layers": 2
    },
    "seq2seq_evaluate": {
        "dataset_name": "muv",
        "classifier_epochs": 10,
        "batch_size": 100,
        "embedding_dimension": 256,
        "encoder_layers": 2,
        "decoder_layers": 2
    },
    "molgan_train": {
        "dataset_name": "tox21",
        "num_atoms": 12,
        "epochs": 50,
        "atom_labels": [0, 5, 6, 7, 8, 9, 11, 12, 13, 14]
    },
    "molgan_generate": {
        "generate_count": 1000
    }
}

TIMEOUT_SECS = 14400
POLL_INTERVAL = 10

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
        print(f"    ❌ 错误: {result.get('error')}")
        hint = result.get("hint")
        if hint:
            print(f"    提示: {hint}")
        return

    summary = result.get("summary", {})
    if summary:
        print("    --- Summary ---")
        for k, v in summary.items():
            print(f"    {k}: {v}")

    results = result.get("results", {})
    if results:
        print("    --- Results ---")
        for k, v in results.items():
            if isinstance(v, list):
                preview = v[:5]
                print(f"    {k} (前 5 条): {preview}")
            else:
                print(f"    {k}: {v}")

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

    if len(sys.argv) > 1:
        action = sys.argv[1]
        if action not in TEST_CASES:
            print(f"❌ 未知 action: {action}，可选: {list(TEST_CASES.keys())}")
            sys.exit(1)
        run_one_test(action, TEST_CASES[action])
    else:
        # 默认先跑一个最快的训练
        run_one_test("seq2seq_train", TEST_CASES["seq2seq_train"])

    print(f"\n{'=' * 60}")
    print("测试完成！")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()