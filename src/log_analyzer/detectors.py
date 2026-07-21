"""Explainable anomaly detectors for minute-level traffic and error metrics."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd


class Detector(Protocol):
    """Structural interface implemented by all detectors."""

    name: str

    def detect(self, metrics: pd.DataFrame) -> pd.DataFrame: ...


def _with_predictions(
    metrics: pd.DataFrame,
    detector_name: str,
    traffic_scores: np.ndarray,
    error_scores: np.ndarray,
    traffic_flags: np.ndarray,
    error_flags: np.ndarray,
) -> pd.DataFrame:
    result = metrics.copy()
    result["detector"] = detector_name
    result["traffic_score"] = traffic_scores
    result["error_score"] = error_scores
    result["is_anomaly"] = traffic_flags | error_flags
    result["anomaly_reasons"] = [
        ",".join(
            reason
            for reason, active in (
                ("traffic_spike", bool(traffic)),
                ("error_rate_jump", bool(error)),
            )
            if active
        )
        for traffic, error in zip(traffic_flags, error_flags, strict=True)
    ]
    return result


@dataclass(frozen=True, slots=True)
class FixedThresholdDetector:
    """Flag metrics that exceed explicitly configured operational limits."""

    request_threshold: int = 500
    error_rate_threshold: float = 0.25
    name: str = "fixed_threshold"

    def detect(self, metrics: pd.DataFrame) -> pd.DataFrame:
        traffic = metrics["request_count"].to_numpy(dtype=float)
        errors = metrics["error_rate"].to_numpy(dtype=float)
        traffic_scores = traffic / self.request_threshold
        error_scores = errors / self.error_rate_threshold
        return _with_predictions(
            metrics,
            self.name,
            traffic_scores,
            error_scores,
            traffic >= self.request_threshold,
            errors >= self.error_rate_threshold,
        )


def _guarded_rolling_zscore(
    values: np.ndarray,
    window: int,
    min_periods: int,
    threshold: float,
    scale_floor: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute past-only z-scores while excluding already-flagged contamination."""

    history: deque[float] = deque(maxlen=window)
    scores = np.zeros(len(values), dtype=float)
    flags = np.zeros(len(values), dtype=bool)

    for index, value in enumerate(values):
        if len(history) < min_periods:
            history.append(float(value))
            continue
        baseline = np.fromiter(history, dtype=float)
        center = float(baseline.mean())
        scale = max(float(baseline.std(ddof=1)), scale_floor)
        score = (float(value) - center) / scale
        scores[index] = score
        flags[index] = score >= threshold
        if not flags[index]:
            history.append(float(value))
    return scores, flags


@dataclass(frozen=True, slots=True)
class RollingZScoreDetector:
    """Use adaptive, past-only rolling baselines for both monitored signals."""

    window: int = 30
    min_periods: int = 12
    z_threshold: float = 3.5
    request_scale_floor: float = 3.0
    error_scale_floor: float = 0.005
    name: str = "rolling_zscore"

    def detect(self, metrics: pd.DataFrame) -> pd.DataFrame:
        if self.window < 2 or not 2 <= self.min_periods <= self.window:
            raise ValueError("rolling window must be >= 2 and include min_periods")
        traffic_scores, traffic_flags = _guarded_rolling_zscore(
            metrics["request_count"].to_numpy(dtype=float),
            self.window,
            self.min_periods,
            self.z_threshold,
            self.request_scale_floor,
        )
        error_scores, error_flags = _guarded_rolling_zscore(
            metrics["error_rate"].to_numpy(dtype=float),
            self.window,
            self.min_periods,
            self.z_threshold,
            self.error_scale_floor,
        )
        return _with_predictions(
            metrics,
            self.name,
            traffic_scores,
            error_scores,
            traffic_flags,
            error_flags,
        )


@dataclass(frozen=True, slots=True)
class StatisticalProcessControlDetector:
    """Apply one-sided Shewhart control limits learned from a calibration period."""

    calibration_window: int = 30
    sigma_limit: float = 2.0
    request_scale_floor: float = 3.0
    error_scale_floor: float = 0.005
    name: str = "statistical_process_control"

    def _scores(self, values: np.ndarray, scale_floor: float) -> tuple[np.ndarray, np.ndarray]:
        if len(values) <= self.calibration_window:
            raise ValueError("input must be longer than the SPC calibration window")
        calibration = values[: self.calibration_window]
        center = float(calibration.mean())
        scale = max(float(calibration.std(ddof=1)), scale_floor)
        scores = (values - center) / scale
        flags = scores >= self.sigma_limit
        # The calibration interval establishes the process limits; it is not scored.
        scores[: self.calibration_window] = 0.0
        flags[: self.calibration_window] = False
        return scores, flags

    def detect(self, metrics: pd.DataFrame) -> pd.DataFrame:
        if self.calibration_window < 2:
            raise ValueError("calibration_window must be at least 2")
        traffic_scores, traffic_flags = self._scores(
            metrics["request_count"].to_numpy(dtype=float), self.request_scale_floor
        )
        error_scores, error_flags = self._scores(
            metrics["error_rate"].to_numpy(dtype=float), self.error_scale_floor
        )
        return _with_predictions(
            metrics,
            self.name,
            traffic_scores,
            error_scores,
            traffic_flags,
            error_flags,
        )


def get_detector(name: str) -> Detector:
    """Build a detector from its CLI-friendly name."""

    normalized = name.lower().replace("-", "_")
    detectors: dict[str, Detector] = {
        "fixed_threshold": FixedThresholdDetector(),
        "rolling_zscore": RollingZScoreDetector(),
        "statistical_process_control": StatisticalProcessControlDetector(),
        "spc": StatisticalProcessControlDetector(),
    }
    try:
        return detectors[normalized]
    except KeyError as exc:
        choices = ", ".join(sorted(detectors))
        raise ValueError(f"unknown detector '{name}'; choose one of: {choices}") from exc


def all_detectors() -> tuple[Detector, ...]:
    """Return the three detector configurations used by the comparison report."""

    return (
        FixedThresholdDetector(),
        RollingZScoreDetector(),
        StatisticalProcessControlDetector(),
    )
