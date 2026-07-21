import pandas as pd

from log_analyzer.detectors import all_detectors
from log_analyzer.evaluation import compare_detectors, evaluate_predictions


def test_evaluation_confusion_matrix_and_scores():
    frame = pd.DataFrame(
        {
            "is_labeled_anomaly": [True, True, False, False],
            "is_anomaly": [True, False, True, False],
        }
    )
    metrics = evaluate_predictions(frame)
    assert metrics.precision == 0.5
    assert metrics.recall == 0.5
    assert metrics.f1 == 0.5
    assert metrics.accuracy == 0.5
    assert metrics.to_dict()["true_positives"] == 1


def test_comparison_is_sorted_by_f1():
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-04-01", periods=40, freq="min", tz="UTC"),
            "request_count": [100] * 35 + [700] * 5,
            "error_rate": [0.01] * 40,
            "is_labeled_anomaly": [False] * 35 + [True] * 5,
        }
    )
    comparison = compare_detectors(frame, all_detectors())
    assert len(comparison) == 3
    assert comparison["f1"].is_monotonic_decreasing
