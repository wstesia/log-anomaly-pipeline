"""Evaluation helpers for comparing predictions with injected labels."""

from __future__ import annotations

import pandas as pd

from log_analyzer.detectors import Detector
from log_analyzer.models import EvaluationMetrics


def evaluate_predictions(predictions: pd.DataFrame) -> EvaluationMetrics:
    """Calculate binary precision/recall metrics at one-minute resolution."""

    expected = predictions["is_labeled_anomaly"].astype(bool)
    actual = predictions["is_anomaly"].astype(bool)
    true_positives = int((expected & actual).sum())
    false_positives = int((~expected & actual).sum())
    false_negatives = int((expected & ~actual).sum())
    true_negatives = int((~expected & ~actual).sum())

    precision = (
        true_positives / (true_positives + false_positives)
        if true_positives + false_positives
        else 0.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if true_positives + false_negatives
        else 0.0
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    total = len(predictions)
    accuracy = (true_positives + true_negatives) / total if total else 0.0
    return EvaluationMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        accuracy=accuracy,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        true_negatives=true_negatives,
    )


def compare_detectors(metrics: pd.DataFrame, detectors: tuple[Detector, ...]) -> pd.DataFrame:
    """Run detectors against the same dataset and rank their results by F1."""

    rows = []
    for detector in detectors:
        evaluation = evaluate_predictions(detector.detect(metrics))
        rows.append({"detector": detector.name, **evaluation.to_dict()})
    return pd.DataFrame(rows).sort_values(
        ["f1", "precision", "recall"], ascending=False
    ).reset_index(drop=True)
