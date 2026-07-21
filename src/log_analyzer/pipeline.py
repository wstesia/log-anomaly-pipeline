"""End-to-end orchestration, artifact writing, and parser benchmarking."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

from log_analyzer.detectors import all_detectors, get_detector
from log_analyzer.evaluation import compare_detectors, evaluate_predictions
from log_analyzer.parser import parse_logs


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Runtime settings for one analysis run."""

    input_paths: tuple[str | Path, ...]
    output_dir: str | Path
    detector: str = "rolling_zscore"
    workers: int | None = None
    chunk_size_mb: int = 64


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_analysis(config: AnalysisConfig) -> dict[str, Any]:
    """Parse inputs, compare detectors, and write reviewable CSV/JSON artifacts."""

    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics, parse_stats = parse_logs(
        config.input_paths,
        workers=config.workers,
        chunk_size_mb=config.chunk_size_mb,
    )
    detector = get_detector(config.detector)
    predictions = detector.detect(metrics)
    evaluation = evaluate_predictions(predictions)
    comparison = compare_detectors(metrics, all_detectors())
    anomalies = predictions[predictions["is_anomaly"]].copy()

    predictions.to_csv(output_dir / "minute_metrics.csv", index=False, float_format="%.6f")
    anomalies.to_csv(output_dir / "anomalies.csv", index=False, float_format="%.6f")
    comparison.to_csv(output_dir / "detector_comparison.csv", index=False, float_format="%.6f")

    summary: dict[str, Any] = {
        "selected_detector": detector.name,
        "time_buckets": len(metrics),
        "detected_anomalies": int(predictions["is_anomaly"].sum()),
        "labeled_anomalies": int(predictions["is_labeled_anomaly"].sum()),
        "evaluation": evaluation.to_dict(),
        "parser": parse_stats.to_dict(),
        "artifacts": {
            "minute_metrics": str(output_dir / "minute_metrics.csv"),
            "anomalies": str(output_dir / "anomalies.csv"),
            "detector_comparison": str(output_dir / "detector_comparison.csv"),
        },
    }
    _write_json(output_dir / "run_summary.json", summary)
    return summary


def benchmark_parser(
    input_paths: tuple[str | Path, ...],
    worker_counts: tuple[int, ...],
    repeats: int = 3,
    chunk_size_mb: int = 64,
) -> pd.DataFrame:
    """Measure median parser throughput for a set of process counts."""

    if repeats < 1:
        raise ValueError("repeats must be positive")
    if not worker_counts or any(count < 1 for count in worker_counts):
        raise ValueError("worker counts must be positive")

    rows: list[dict[str, float | int]] = []
    for worker_count in worker_counts:
        elapsed_samples = []
        throughput_samples = []
        actual_workers = worker_count
        total_bytes = 0
        for _ in range(repeats):
            _, stats = parse_logs(input_paths, worker_count, chunk_size_mb)
            elapsed_samples.append(stats.elapsed_seconds)
            throughput_samples.append(stats.throughput_mb_s)
            actual_workers = stats.workers
            total_bytes = stats.bytes_read
        rows.append(
            {
                "requested_workers": worker_count,
                "actual_workers": actual_workers,
                "input_bytes": total_bytes,
                "median_seconds": median(elapsed_samples),
                "median_throughput_mb_s": median(throughput_samples),
            }
        )

    result = pd.DataFrame(rows)
    baseline = float(result.iloc[0]["median_seconds"])
    result["speedup_vs_first"] = baseline / result["median_seconds"]
    return result
