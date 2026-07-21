from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from log_analyzer.detectors import (
    FixedThresholdDetector,
    RollingZScoreDetector,
    StatisticalProcessControlDetector,
    get_detector,
)


def _metrics() -> pd.DataFrame:
    rng = np.random.default_rng(4)
    requests = rng.normal(100, 4, 80)
    error_rates = rng.normal(0.015, 0.003, 80)
    requests[40:44] = 420
    error_rates[60:64] = 0.32
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-04-01", periods=80, freq="min", tz="UTC"),
            "request_count": requests,
            "error_rate": error_rates,
            "is_labeled_anomaly": [40 <= i < 44 or 60 <= i < 64 for i in range(80)],
        }
    )


def test_rolling_detector_catches_sustained_labeled_windows():
    result = RollingZScoreDetector().detect(_metrics())
    assert result.loc[40:43, "is_anomaly"].all()
    assert result.loc[60:63, "is_anomaly"].all()
    assert set(result.loc[40:43, "anomaly_reasons"]) == {"traffic_spike"}
    assert set(result.loc[60:63, "anomaly_reasons"]) == {"error_rate_jump"}


def test_fixed_threshold_reports_both_reasons():
    frame = _metrics()
    frame.loc[50, ["request_count", "error_rate"]] = [700, 0.5]
    result = FixedThresholdDetector().detect(frame)
    assert result.loc[50, "anomaly_reasons"] == "traffic_spike,error_rate_jump"
    assert result.loc[0, "is_anomaly"] == np.False_


def test_spc_requires_enough_calibration_data():
    with pytest.raises(ValueError, match="longer"):
        StatisticalProcessControlDetector(calibration_window=100).detect(_metrics())


def test_detector_factory_accepts_cli_names():
    assert get_detector("rolling-zscore").name == "rolling_zscore"
    assert get_detector("spc").name == "statistical_process_control"
    with pytest.raises(ValueError, match="unknown detector"):
        get_detector("neural-net")
