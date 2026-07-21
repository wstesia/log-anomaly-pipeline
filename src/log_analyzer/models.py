"""Shared data models used by the parser and pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class LogEvent:
    """A validated event extracted from one JSON log line."""

    timestamp: datetime
    service: str
    endpoint: str
    status: int
    latency_ms: float
    bytes_sent: int
    anomaly: str | None = None


@dataclass(frozen=True, slots=True)
class ParseStats:
    """Operational statistics from a parsing run."""

    files: int
    bytes_read: int
    lines_read: int
    malformed_lines: int
    workers: int
    partitions: int
    elapsed_seconds: float

    @property
    def throughput_mb_s(self) -> float:
        if self.elapsed_seconds == 0:
            return 0.0
        return self.bytes_read / (1024 * 1024) / self.elapsed_seconds

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["throughput_mb_s"] = self.throughput_mb_s
        return result


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    """Binary-classification metrics for one detector."""

    precision: float
    recall: float
    f1: float
    accuracy: float
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)
