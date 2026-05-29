"""Data models for the experiment runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DatasetItem:
    """A single input item for an experiment run."""

    prompt: str
    id: str | None = None
    history: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    expected_output: str | None = None


@dataclass
class ExperimentResult:
    """Result of running one dataset item through the agent."""

    item_id: str | None
    prompt: str
    final_response: str
    status: str  # "success" | "error" | "timeout"
    error_message: str | None = None
    wall_time_seconds: float = 0.0
    steps: list[dict] = field(default_factory=list)
    code_executions: int = 0
    item_metadata: dict[str, Any] = field(default_factory=dict)
    expected_output: str | None = None
    match_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to plain dict for serialization."""
        return {
            "item_id": self.item_id,
            "prompt": self.prompt,
            "final_response": self.final_response,
            "status": self.status,
            "error_message": self.error_message,
            "wall_time_seconds": self.wall_time_seconds,
            "steps": self.steps,
            "code_executions": self.code_executions,
            "item_metadata": self.item_metadata,
            "expected_output": self.expected_output,
            "match_score": self.match_score,
        }


@dataclass
class ExperimentReport:
    """Aggregate report for a full experiment run."""

    total: int = 0
    successes: int = 0
    errors: int = 0
    timeouts: int = 0
    total_wall_time: float = 0.0
    avg_wall_time: float = 0.0
    results: list[ExperimentResult] = field(default_factory=list)
    output_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to plain dict for serialization."""
        return {
            "summary": {
                "total": self.total,
                "successes": self.successes,
                "errors": self.errors,
                "timeouts": self.timeouts,
                "total_wall_time": round(self.total_wall_time, 2),
                "avg_wall_time": round(self.avg_wall_time, 2),
            },
            "output_path": self.output_path,
            "results": [r.to_dict() for r in self.results],
        }
