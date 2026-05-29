#!/usr/bin/env python3
"""Experiment launcher — run experiments with organized output.

Each run creates a timestamped directory under ``experiments/``:

    experiments/
    └── 20250528_143022_smoke_test/
        ├── config.json          # All parameters used
        ├── results.json         # Full JSON report
        ├── results.csv          # Flat CSV table
        └── summary.txt          # Human-readable summary

Usage:
    # Run with defaults (uses experiment_test_tasks.json)
    python run_experiment_test.py

    # Custom dataset and name
    python run_experiment_test.py --dataset my_bench.json --name bench_v1

    # Parallel with 4 workers
    python run_experiment_test.py --workers 4

    # Adjust agent behavior
    python run_experiment_test.py --no-skills --timeout 120
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from CAi.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_SOURCE
from CAi.experiment import DatasetItem, ExperimentReport, load_dataset, run_experiment


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run CAi experiment")

    # Dataset & naming
    p.add_argument("--dataset", default="experiment_test_tasks.json",
                   help="Path to dataset file (JSON/JSONL/CSV)")
    p.add_argument("--name", default="",
                   help="Experiment name (appended to timestamp dir). Default: dataset stem")
    p.add_argument("--output-dir", default="experiments",
                   help="Root directory for experiment results (default: experiments/)")

    # Agent config
    p.add_argument("--model", default=LLM_MODEL, help="LLM model")
    p.add_argument("--source", default=LLM_SOURCE, help="LLM source")
    p.add_argument("--base-url", default=LLM_BASE_URL, help="LLM base URL")
    p.add_argument("--no-tools", action="store_true", help="Disable tool loading")
    p.add_argument("--no-skills", action="store_true", help="Disable skill loading")
    p.add_argument("--utilities", action="store_true", help="Enable utility loading (default: off)")

    # Execution
    p.add_argument("--workers", type=int, default=1,
                   help="Number of parallel workers (1=sequential, >1=multiprocessing)")
    p.add_argument("--timeout", type=int, default=300,
                   help="Per-item timeout in seconds")

    # Dataset field mapping
    p.add_argument("--prompt-field", default="prompt", help="Field name for prompt text")
    p.add_argument("--id-field", default="id", help="Field name for item ID")

    return p.parse_args()


def create_run_dir(base_dir: str | Path, name: str) -> Path:
    """Create a timestamped run directory: base_dir/YYYYMMDD_HHMMSS_name/"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{name}" if name else ""
    run_dir = Path(base_dir) / f"{ts}{suffix}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_config(run_dir: Path, args: argparse.Namespace, dataset_size: int,
                total_wall: float) -> None:
    """Save experiment configuration as JSON."""
    config = {
        "experiment_name": args.name or Path(args.dataset).stem,
        "timestamp": datetime.now().isoformat(),
        "dataset": {
            "file": str(args.dataset),
            "size": dataset_size,
            "prompt_field": args.prompt_field,
            "id_field": args.id_field,
        },
        "agent": {
            "model": args.model,
            "source": args.source,
            "base_url": args.base_url,
            "auto_load_tools": not args.no_tools,
            "auto_load_skills": not args.no_skills,
            "auto_load_utilities": args.utilities,
        },
        "execution": {
            "workers": args.workers,
            "per_item_timeout_seconds": args.timeout,
        },
        "result": {
            "total": dataset_size,
            "total_wall_time_seconds": round(total_wall, 2),
        },
    }
    with open(run_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def save_summary(run_dir: Path, report: ExperimentReport,
                 total_wall: float, args: argparse.Namespace) -> None:
    """Save human-readable summary."""
    lines = [
        f"Experiment: {args.name or Path(args.dataset).stem}",
        f"Timestamp:  {datetime.now().isoformat()}",
        f"Dataset:    {args.dataset} ({report.total} items)",
        f"LLM:        {args.model} (source={args.source})",
        f"Workers:    {args.workers}, Timeout: {args.timeout}s/item",
        "",
        f"{'='*50}",
        f"Results: {report.successes}/{report.total} success",
        f"  Errors:   {report.errors}",
        f"  Timeouts: {report.timeouts}",
        f"  Avg time: {report.avg_wall_time:.1f}s/item",
        f"  Total:    {total_wall:.1f}s",
        "",
        "Per-item breakdown:",
    ]
    for r in report.results:
        cat = r.item_metadata.get("category", "?")
        status_icon = {"success": "✓", "error": "✗", "timeout": "⏱"}.get(r.status, "?")
        lines.append(
            f"  {status_icon} {r.item_id} ({cat}): {r.status} "
            f"in {r.wall_time_seconds:.1f}s, {r.code_executions} code runs"
        )
        if r.error_message:
            lines.append(f"    Error: {r.error_message[:150]}")

    with open(run_dir / "summary.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    args = parse_args()

    # Load dataset
    dataset = load_dataset(
        args.dataset,
        prompt_field=args.prompt_field,
        id_field=args.id_field,
    )
    if not dataset:
        print(f"[ERROR] Dataset is empty: {args.dataset}", file=sys.stderr)
        sys.exit(1)

    exp_name = args.name or Path(args.dataset).stem
    run_dir = create_run_dir(args.output_dir, exp_name)
    print(f"[INFO] Run directory: {run_dir}")
    print(f"[INFO] Loaded {len(dataset)} tasks from {args.dataset}")
    print(f"[INFO] LLM: model={args.model}, source={args.source}")
    print(f"[INFO] Workers: {args.workers}, Timeout: {args.timeout}s/item\n")

    # Build agent args
    agent_args = {
        "llm": args.model,
        "source": args.source,
        "base_url": args.base_url,
        "api_key": LLM_API_KEY,
        "auto_load_tools": not args.no_tools,
        "auto_load_skills": not args.no_skills,
        "auto_load_utilities": args.utilities,
    }

    def on_progress(done: int, total: int, result) -> None:
        cat = result.item_metadata.get("category", "?")
        icon = {"success": "✓", "error": "✗", "timeout": "⏱"}.get(result.status, "?")
        print(
            f"[{done}/{total}] {icon} {result.item_id} ({cat}): "
            f"{result.status} in {result.wall_time_seconds:.1f}s"
        )
        if result.error_message:
            print(f"  Error: {result.error_message[:200]}")

    t0 = time.time()
    report = run_experiment(
        dataset,
        agent_args=agent_args,
        max_workers=args.workers,
        per_item_timeout_seconds=args.timeout,
        on_progress=on_progress,
    )
    total_wall = time.time() - t0

    # Save outputs to run directory
    from CAi.experiment.persistence import save_csv, save_json

    save_json(report, run_dir / "results.json")
    save_csv(report, run_dir / "results.csv")
    save_config(run_dir, args, len(dataset), total_wall)
    save_summary(run_dir, report, total_wall, args)

    # Print summary
    print(f"\n{'='*50}")
    print(f"Results: {report.successes}/{report.total} success")
    print(f"  Errors:   {report.errors}")
    print(f"  Timeouts: {report.timeouts}")
    print(f"  Avg time: {report.avg_wall_time:.1f}s/item")
    print(f"  Total:    {total_wall:.1f}s")
    print(f"\nSaved to: {run_dir}/")
    print(f"  ├── config.json")
    print(f"  ├── results.json")
    print(f"  ├── results.csv")
    print(f"  └── summary.txt")


if __name__ == "__main__":
    main()
