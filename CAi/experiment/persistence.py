"""Result persistence for experiment reports.

Supports JSON (full detail) and CSV (flat table) output formats.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .models import ExperimentReport


def save_json(report: ExperimentReport, path: str | Path) -> None:
    """Save the full experiment report as JSON.

    Includes summary statistics and all individual result details.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)


def save_csv(report: ExperimentReport, path: str | Path) -> None:
    """Save experiment results as a flat CSV table.

    Columns: item_id, prompt, final_response, status, error_message,
    wall_time_seconds, code_executions, match_score, plus metadata fields.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Collect all metadata keys across results
    meta_keys: list[str] = []
    seen = set()
    for r in report.results:
        for k in r.item_metadata:
            if k not in seen:
                meta_keys.append(k)
                seen.add(k)

    base_fields = [
        "item_id", "prompt", "final_response", "status",
        "error_message", "wall_time_seconds", "code_executions", "match_score",
    ]
    fieldnames = base_fields + [f"meta_{k}" for k in meta_keys]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in report.results:
            row: dict[str, Any] = {
                "item_id": r.item_id or "",
                "prompt": r.prompt,
                "final_response": r.final_response,
                "status": r.status,
                "error_message": r.error_message or "",
                "wall_time_seconds": r.wall_time_seconds,
                "code_executions": r.code_executions,
                "match_score": r.match_score if r.match_score is not None else "",
            }
            for k in meta_keys:
                row[f"meta_{k}"] = r.item_metadata.get(k, "")
            writer.writerow(row)
