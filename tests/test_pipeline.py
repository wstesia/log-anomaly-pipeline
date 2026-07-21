from __future__ import annotations

import json

from log_analyzer.cli import main
from log_analyzer.generator import GenerationConfig, generate_logs
from log_analyzer.pipeline import AnalysisConfig, benchmark_parser, run_analysis


def test_end_to_end_analysis_writes_artifacts(tmp_path):
    logs = tmp_path / "logs"
    output = tmp_path / "analysis"
    generate_logs(logs, GenerationConfig(minutes=90, base_requests_per_minute=30, shard_count=2))

    summary = run_analysis(
        AnalysisConfig((logs,), output, workers=2, chunk_size_mb=1)
    )

    assert summary["time_buckets"] == 90
    assert summary["evaluation"]["recall"] > 0
    assert (output / "minute_metrics.csv").is_file()
    assert (output / "anomalies.csv").is_file()
    assert (output / "detector_comparison.csv").is_file()
    saved = json.loads((output / "run_summary.json").read_text())
    assert saved["selected_detector"] == "rolling_zscore"


def test_benchmark_and_cli_error_path(tmp_path):
    logs = tmp_path / "logs"
    generate_logs(logs, GenerationConfig(minutes=30, base_requests_per_minute=5, shard_count=1))
    result = benchmark_parser((logs,), (1,), repeats=1, chunk_size_mb=1)
    assert result.loc[0, "speedup_vs_first"] == 1.0
    assert main(["analyze", str(tmp_path / "missing")]) == 2
