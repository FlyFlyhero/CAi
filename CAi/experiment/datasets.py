"""Dataset loading for experiment runs.

Supports list[dict], CSV, JSON, and JSONL input formats.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import DatasetItem


# Known fields that map to DatasetItem attributes directly
_KNOWN_FIELDS = {"prompt", "id", "history", "metadata", "expected_output"}


def _dict_to_item(d: dict, *, prompt_field: str = "prompt", id_field: str = "id") -> DatasetItem:
    """Convert a raw dict to a DatasetItem.

    Any field not matching prompt_field, id_field, or known DatasetItem
    attributes is collected into the ``metadata`` dict.
    """
    prompt = d[prompt_field]
    item_id = d.get(id_field)
    history = d.get("history", [])
    expected_output = d.get("expected_output")

    # Collect unmapped fields as metadata
    metadata = {}
    for k, v in d.items():
        if k not in {prompt_field, id_field, "history", "expected_output", "metadata"}:
            metadata[k] = v
    # Merge explicit metadata if present
    if "metadata" in d and isinstance(d["metadata"], dict):
        metadata.update(d["metadata"])

    return DatasetItem(
        prompt=str(prompt),
        id=str(item_id) if item_id is not None else None,
        history=history,
        metadata=metadata,
        expected_output=expected_output,
    )


def load_dataset(
    source: str | Path | list[dict],
    *,
    prompt_field: str = "prompt",
    id_field: str = "id",
) -> list[DatasetItem]:
    """Load a dataset from a file or list of dicts.

    Args:
        source: A file path (CSV, JSON, JSONL) or a list of dicts.
        prompt_field: Dict key containing the prompt text.
        id_field: Dict key containing the item ID.

    Returns:
        List of DatasetItem ready for experiment execution.
    """
    if isinstance(source, list):
        return [_dict_to_item(d, prompt_field=prompt_field, id_field=id_field) for d in source]

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Dataset file not found: {path}")

    suffix = path.suffix.lower()

    if suffix == ".csv":
        return _load_csv(path, prompt_field, id_field)
    elif suffix in (".json",):
        return _load_json(path, prompt_field, id_field)
    elif suffix in (".jsonl", ".ndjson"):
        return _load_jsonl(path, prompt_field, id_field)
    else:
        raise ValueError(f"Unsupported file format: {suffix}. Use .csv, .json, or .jsonl")


def _load_csv(path: Path, prompt_field: str, id_field: str) -> list[DatasetItem]:
    """Load dataset from CSV file."""
    import csv

    items = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Convert empty strings to None for optional fields
            cleaned = {}
            for k, v in row.items():
                if v == "" or v is None:
                    cleaned[k] = None
                else:
                    # Try to parse JSON values (e.g., history as JSON array)
                    try:
                        cleaned[k] = json.loads(v)
                    except (json.JSONDecodeError, TypeError):
                        cleaned[k] = v
            items.append(_dict_to_item(cleaned, prompt_field=prompt_field, id_field=id_field))
    return items


def _load_json(path: Path, prompt_field: str, id_field: str) -> list[DatasetItem]:
    """Load dataset from JSON file (array of objects)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array in {path}, got {type(data).__name__}")
    return [_dict_to_item(d, prompt_field=prompt_field, id_field=id_field) for d in data]


def _load_jsonl(path: Path, prompt_field: str, id_field: str) -> list[DatasetItem]:
    """Load dataset from JSONL file (one JSON object per line)."""
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON on line {line_num} in {path}: {e}") from None
            items.append(_dict_to_item(d, prompt_field=prompt_field, id_field=id_field))
    return items
