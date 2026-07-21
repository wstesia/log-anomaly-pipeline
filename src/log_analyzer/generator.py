"""Deterministic synthetic server-log generation with labeled anomaly windows."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    """Controls the size and statistical properties of generated logs."""

    minutes: int = 180
    base_requests_per_minute: int = 120
    normal_error_rate: float = 0.015
    traffic_multiplier: float = 4.0
    anomalous_error_rate: float = 0.35
    shard_count: int = 4
    seed: int = 42
    start_time: datetime = datetime(2026, 4, 1, tzinfo=timezone.utc)

    def validate(self) -> None:
        if self.minutes < 30:
            raise ValueError("minutes must be at least 30 so baseline and anomaly windows fit")
        if self.base_requests_per_minute < 1:
            raise ValueError("base_requests_per_minute must be positive")
        if not 0 <= self.normal_error_rate < 1:
            raise ValueError("normal_error_rate must be in [0, 1)")
        if not 0 < self.anomalous_error_rate <= 1:
            raise ValueError("anomalous_error_rate must be in (0, 1]")
        if self.traffic_multiplier <= 1:
            raise ValueError("traffic_multiplier must be greater than 1")
        if self.shard_count < 1:
            raise ValueError("shard_count must be positive")


@dataclass(frozen=True, slots=True)
class AnomalyWindow:
    """A half-open interval containing one injected anomaly type."""

    anomaly_type: str
    start_minute: int
    end_minute: int

    def contains(self, minute: int) -> bool:
        return self.start_minute <= minute < self.end_minute


@dataclass(frozen=True, slots=True)
class GenerationSummary:
    output_files: tuple[str, ...]
    total_events: int
    total_bytes: int
    labeled_events: int
    windows: tuple[AnomalyWindow, ...]
    config: GenerationConfig

    def to_dict(self) -> dict[str, Any]:
        config = asdict(self.config)
        config["start_time"] = self.config.start_time.isoformat().replace("+00:00", "Z")
        return {
            "output_files": list(self.output_files),
            "total_events": self.total_events,
            "total_bytes": self.total_bytes,
            "labeled_events": self.labeled_events,
            "windows": [asdict(window) for window in self.windows],
            "config": config,
        }


def anomaly_schedule(minutes: int) -> tuple[AnomalyWindow, ...]:
    """Place two non-overlapping anomaly windows after a clean warm-up period."""

    duration = max(3, min(8, minutes // 30))
    traffic_start = minutes // 3
    error_start = (2 * minutes) // 3
    return (
        AnomalyWindow("traffic_spike", traffic_start, traffic_start + duration),
        AnomalyWindow("error_rate_jump", error_start, error_start + duration),
    )


def _window_for_minute(minute: int, windows: tuple[AnomalyWindow, ...]) -> str | None:
    for window in windows:
        if window.contains(minute):
            return window.anomaly_type
    return None


def _serialize_event(
    timestamp: datetime,
    service: str,
    endpoint: str,
    status: int,
    latency_ms: float,
    bytes_sent: int,
    anomaly: str | None,
) -> str:
    payload = {
        "timestamp": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "service": service,
        "endpoint": endpoint,
        "status": status,
        "latency_ms": round(latency_ms, 2),
        "bytes_sent": bytes_sent,
        "anomaly": anomaly,
    }
    return json.dumps(payload, separators=(",", ":")) + "\n"


def generate_logs(output_dir: str | Path, config: GenerationConfig) -> GenerationSummary:
    """Generate sharded JSONL logs and a manifest describing injected anomalies."""

    config.validate()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    windows = anomaly_schedule(config.minutes)
    rng = np.random.default_rng(config.seed)
    shard_paths = [output_path / f"server-{index:03d}.jsonl" for index in range(config.shard_count)]
    handles = [path.open("w", encoding="utf-8", newline="\n") for path in shard_paths]

    services = np.array(["api", "auth", "search", "billing"])
    service_weights = np.array([0.45, 0.20, 0.25, 0.10])
    endpoints = np.array(["/v1/items", "/v1/login", "/v1/search", "/v1/checkout"])
    success_statuses = np.array([200, 200, 200, 200, 201, 204, 404])
    error_statuses = np.array([500, 502, 503, 504])
    total_events = 0
    labeled_events = 0

    try:
        for minute in range(config.minutes):
            anomaly = _window_for_minute(minute, windows)
            # Slow baseline movement approximates an ordinary workload cycle and
            # distinguishes an adaptive detector from a static control limit.
            seasonal_factor = 1.0 + 0.18 * math.sin(2 * math.pi * minute / 90)
            rate = config.base_requests_per_minute * seasonal_factor
            if anomaly == "traffic_spike":
                rate *= config.traffic_multiplier
            request_count = int(rng.poisson(rate))
            error_rate = (
                config.anomalous_error_rate
                if anomaly == "error_rate_jump"
                else config.normal_error_rate
            )
            offsets = np.sort(rng.uniform(0, 60, size=request_count))
            shard = handles[minute % config.shard_count]

            for offset in offsets:
                is_error = bool(rng.random() < error_rate)
                status = int(rng.choice(error_statuses if is_error else success_statuses))
                service_index = int(rng.choice(len(services), p=service_weights))
                timestamp = config.start_time + timedelta(minutes=minute, seconds=float(offset))
                latency = float(rng.lognormal(mean=4.25 if is_error else 3.7, sigma=0.38))
                bytes_sent = int(max(64, rng.lognormal(mean=7.1, sigma=0.55)))
                shard.write(
                    _serialize_event(
                        timestamp=timestamp,
                        service=str(services[service_index]),
                        endpoint=str(endpoints[service_index]),
                        status=status,
                        latency_ms=latency,
                        bytes_sent=bytes_sent,
                        anomaly=anomaly,
                    )
                )
                total_events += 1
                labeled_events += anomaly is not None
    finally:
        for handle in handles:
            handle.close()

    total_bytes = sum(path.stat().st_size for path in shard_paths)
    summary = GenerationSummary(
        output_files=tuple(str(path) for path in shard_paths),
        total_events=total_events,
        total_bytes=total_bytes,
        labeled_events=labeled_events,
        windows=windows,
        config=config,
    )
    manifest_path = output_path / "generation_manifest.json"
    manifest_path.write_text(json.dumps(summary.to_dict(), indent=2) + "\n", encoding="utf-8")
    return summary
