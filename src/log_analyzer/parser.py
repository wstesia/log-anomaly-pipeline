"""Parallel, memory-bounded parsing for newline-delimited JSON server logs."""

from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from log_analyzer.models import LogEvent, ParseStats

ALLOWED_ANOMALIES = {None, "traffic_spike", "error_rate_jump"}


class LogParseError(ValueError):
    """Raised when a line does not match the expected event schema."""


@dataclass(frozen=True, slots=True)
class _ByteRange:
    path: str
    start: int
    end: int


@dataclass(slots=True)
class _RangeResult:
    buckets: dict[str, dict[str, float | int]]
    lines_read: int
    malformed_lines: int


def parse_event_line(line: str | bytes) -> LogEvent:
    """Parse and validate one JSONL event."""

    try:
        payload = json.loads(line)
        timestamp_text = str(payload["timestamp"])
        timestamp = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise LogParseError("timestamp must include a timezone")
        timestamp = timestamp.astimezone(timezone.utc)
        status = int(payload["status"])
        latency_ms = float(payload["latency_ms"])
        bytes_sent = int(payload["bytes_sent"])
        service = str(payload["service"])
        endpoint = str(payload["endpoint"])
        anomaly = payload.get("anomaly")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, LogParseError):
            raise
        raise LogParseError(f"invalid log event: {exc}") from exc

    if not 100 <= status <= 599:
        raise LogParseError(f"status out of range: {status}")
    if latency_ms < 0 or bytes_sent < 0:
        raise LogParseError("latency_ms and bytes_sent must be non-negative")
    if not service or not endpoint.startswith("/"):
        raise LogParseError("service and endpoint must be non-empty")
    if anomaly not in ALLOWED_ANOMALIES:
        raise LogParseError(f"unsupported anomaly label: {anomaly}")

    return LogEvent(
        timestamp=timestamp,
        service=service,
        endpoint=endpoint,
        status=status,
        latency_ms=latency_ms,
        bytes_sent=bytes_sent,
        anomaly=anomaly,
    )


def _empty_bucket() -> dict[str, float | int]:
    return {
        "request_count": 0,
        "error_count": 0,
        "latency_sum": 0.0,
        "bytes_sent": 0,
        "labeled_event_count": 0,
        "traffic_spike_events": 0,
        "error_rate_jump_events": 0,
    }


def _add_event(buckets: dict[str, dict[str, float | int]], event: LogEvent) -> None:
    minute = event.timestamp.replace(second=0, microsecond=0)
    key = minute.isoformat()
    bucket = buckets.setdefault(key, _empty_bucket())
    bucket["request_count"] += 1
    bucket["error_count"] += int(event.status >= 500)
    bucket["latency_sum"] += event.latency_ms
    bucket["bytes_sent"] += event.bytes_sent
    if event.anomaly:
        bucket["labeled_event_count"] += 1
        label_key = f"{event.anomaly}_events"
        bucket[label_key] += 1


def _parse_byte_range(byte_range: _ByteRange) -> _RangeResult:
    buckets: dict[str, dict[str, float | int]] = {}
    lines_read = 0
    malformed_lines = 0
    file_size = Path(byte_range.path).stat().st_size

    with Path(byte_range.path).open("rb") as handle:
        if byte_range.start > 0:
            handle.seek(byte_range.start - 1)
            if handle.read(1) != b"\n":
                handle.readline()
        else:
            handle.seek(0)

        while True:
            line_start = handle.tell()
            if line_start >= byte_range.end and byte_range.end < file_size:
                break
            line = handle.readline()
            if not line:
                break
            lines_read += 1
            try:
                _add_event(buckets, parse_event_line(line))
            except (LogParseError, UnicodeDecodeError):
                malformed_lines += 1

    return _RangeResult(buckets, lines_read, malformed_lines)


def resolve_log_paths(inputs: Iterable[str | Path]) -> list[Path]:
    """Resolve files and directories into a sorted, de-duplicated JSONL file list."""

    resolved: set[Path] = set()
    for item in inputs:
        path = Path(item).expanduser().resolve()
        if path.is_dir():
            resolved.update(candidate for candidate in path.glob("*.jsonl") if candidate.is_file())
        elif path.is_file():
            resolved.add(path)
        else:
            raise FileNotFoundError(f"log input does not exist: {path}")
    paths = sorted(resolved)
    if not paths:
        raise FileNotFoundError("no .jsonl log files found")
    return paths


def _build_ranges(paths: list[Path], chunk_size_mb: int) -> list[_ByteRange]:
    chunk_bytes = chunk_size_mb * 1024 * 1024
    ranges: list[_ByteRange] = []
    for path in paths:
        size = path.stat().st_size
        if size == 0:
            continue
        range_count = max(1, math.ceil(size / chunk_bytes))
        for index in range(range_count):
            start = index * chunk_bytes
            ranges.append(_ByteRange(str(path), start, min(size, start + chunk_bytes)))
    if not ranges:
        raise ValueError("all input log files are empty")
    return ranges


def _merge_results(results: Iterable[_RangeResult]) -> tuple[dict[str, dict[str, Any]], int, int]:
    merged: dict[str, dict[str, Any]] = {}
    lines_read = 0
    malformed_lines = 0
    for result in results:
        lines_read += result.lines_read
        malformed_lines += result.malformed_lines
        for timestamp, partial in result.buckets.items():
            target = merged.setdefault(timestamp, _empty_bucket())
            for key, value in partial.items():
                target[key] += value
    return merged, lines_read, malformed_lines


def _to_dataframe(buckets: dict[str, dict[str, Any]]) -> pd.DataFrame:
    if not buckets:
        raise ValueError("no valid log events found")
    rows: list[dict[str, Any]] = []
    for timestamp, bucket in buckets.items():
        request_count = int(bucket["request_count"])
        label_types = []
        if bucket["traffic_spike_events"]:
            label_types.append("traffic_spike")
        if bucket["error_rate_jump_events"]:
            label_types.append("error_rate_jump")
        rows.append(
            {
                "timestamp": timestamp,
                "request_count": request_count,
                "error_count": int(bucket["error_count"]),
                "error_rate": float(bucket["error_count"]) / request_count,
                "avg_latency_ms": float(bucket["latency_sum"]) / request_count,
                "bytes_sent": int(bucket["bytes_sent"]),
                "labeled_event_count": int(bucket["labeled_event_count"]),
                "is_labeled_anomaly": bool(bucket["labeled_event_count"]),
                "labeled_anomaly_types": ",".join(label_types),
            }
        )

    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame.sort_values("timestamp").reset_index(drop=True)


def parse_logs(
    inputs: Iterable[str | Path],
    workers: int | None = None,
    chunk_size_mb: int = 64,
) -> tuple[pd.DataFrame, ParseStats]:
    """Parse logs concurrently and return minute-level aggregates plus run statistics.

    Workers aggregate their byte ranges locally. Only compact per-minute summaries
    cross process boundaries, keeping memory proportional to the number of time
    buckets rather than the number of raw events.
    """

    if chunk_size_mb < 1:
        raise ValueError("chunk_size_mb must be positive")
    paths = resolve_log_paths(inputs)
    ranges = _build_ranges(paths, chunk_size_mb)
    requested_workers = workers if workers is not None and workers > 0 else (os.cpu_count() or 1)
    worker_count = max(1, min(requested_workers, len(ranges)))
    started = time.perf_counter()

    if worker_count == 1:
        results = map(_parse_byte_range, ranges)
        merged, lines_read, malformed_lines = _merge_results(results)
    else:
        with mp.Pool(processes=worker_count) as pool:
            results = pool.imap_unordered(_parse_byte_range, ranges)
            merged, lines_read, malformed_lines = _merge_results(results)

    elapsed = time.perf_counter() - started
    stats = ParseStats(
        files=len(paths),
        bytes_read=sum(path.stat().st_size for path in paths),
        lines_read=lines_read,
        malformed_lines=malformed_lines,
        workers=worker_count,
        partitions=len(ranges),
        elapsed_seconds=elapsed,
    )
    return _to_dataframe(merged), stats
