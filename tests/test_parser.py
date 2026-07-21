from __future__ import annotations

import json

import pandas as pd
import pytest

from log_analyzer.generator import GenerationConfig, generate_logs
from log_analyzer.parser import LogParseError, parse_event_line, parse_logs, resolve_log_paths


def _event(**overrides):
    payload = {
        "timestamp": "2026-04-01T00:00:01.000Z",
        "service": "api",
        "endpoint": "/v1/items",
        "status": 200,
        "latency_ms": 25.5,
        "bytes_sent": 512,
        "anomaly": None,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_parse_event_line_validates_and_converts_fields():
    event = parse_event_line(_event(status="503", latency_ms="41.2"))
    assert event.status == 503
    assert event.latency_ms == 41.2
    assert event.timestamp.utcoffset().total_seconds() == 0


@pytest.mark.parametrize(
    "line",
    [
        "not-json",
        _event(status=99),
        _event(latency_ms=-1),
        _event(endpoint="items"),
        _event(anomaly="mystery"),
        _event(timestamp="2026-04-01T00:00:01"),
    ],
)
def test_parse_event_line_rejects_invalid_rows(line):
    with pytest.raises(LogParseError):
        parse_event_line(line)


def test_parallel_byte_ranges_match_single_process(tmp_path):
    log_dir = tmp_path / "logs"
    summary = generate_logs(
        log_dir,
        GenerationConfig(minutes=30, base_requests_per_minute=300, shard_count=1, seed=12),
    )
    assert summary.total_bytes > 1024 * 1024

    single, single_stats = parse_logs((log_dir,), workers=1, chunk_size_mb=1)
    parallel, parallel_stats = parse_logs((log_dir,), workers=2, chunk_size_mb=1)

    pd.testing.assert_frame_equal(single, parallel)
    assert single_stats.lines_read == summary.total_events
    assert parallel_stats.lines_read == summary.total_events
    assert parallel_stats.partitions > 1
    assert parallel_stats.workers == 2


def test_malformed_lines_are_reported(tmp_path):
    path = tmp_path / "server.jsonl"
    path.write_text(_event() + "\n{bad json}\n", encoding="utf-8")
    frame, stats = parse_logs((path,), workers=1)

    assert len(frame) == 1
    assert stats.lines_read == 2
    assert stats.malformed_lines == 1
    assert stats.throughput_mb_s > 0


def test_input_resolution_errors_are_clear(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        resolve_log_paths((tmp_path / "missing",))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="no .jsonl"):
        resolve_log_paths((empty,))
