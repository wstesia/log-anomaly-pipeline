from __future__ import annotations

import json
from pathlib import Path

import pytest

from log_analyzer.generator import GenerationConfig, anomaly_schedule, generate_logs


def test_generation_is_reproducible(tmp_path):
    config = GenerationConfig(minutes=30, base_requests_per_minute=10, shard_count=2, seed=7)
    first = generate_logs(tmp_path / "first", config)
    second = generate_logs(tmp_path / "second", config)

    assert first.total_events == second.total_events
    for first_path, second_path in zip(first.output_files, second.output_files, strict=True):
        assert Path(first_path).read_bytes() == Path(second_path).read_bytes()
    manifest = json.loads((tmp_path / "first" / "generation_manifest.json").read_text())
    assert manifest["labeled_events"] > 0
    assert {window["anomaly_type"] for window in manifest["windows"]} == {
        "traffic_spike",
        "error_rate_jump",
    }


def test_schedule_has_separate_windows():
    traffic, errors = anomaly_schedule(180)
    assert traffic.end_minute < errors.start_minute
    assert traffic.contains(traffic.start_minute)
    assert not traffic.contains(traffic.end_minute)


@pytest.mark.parametrize(
    "config",
    [
        GenerationConfig(minutes=20),
        GenerationConfig(base_requests_per_minute=0),
        GenerationConfig(normal_error_rate=1.0),
        GenerationConfig(traffic_multiplier=1.0),
        GenerationConfig(shard_count=0),
    ],
)
def test_invalid_generation_config_is_rejected(config):
    with pytest.raises(ValueError):
        config.validate()
