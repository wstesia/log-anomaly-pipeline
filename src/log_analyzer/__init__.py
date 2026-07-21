"""Parallel log analysis and statistical anomaly detection."""

from log_analyzer.generator import GenerationConfig, generate_logs
from log_analyzer.pipeline import AnalysisConfig, run_analysis

__all__ = ["AnalysisConfig", "GenerationConfig", "generate_logs", "run_analysis"]
__version__ = "1.0.0"
