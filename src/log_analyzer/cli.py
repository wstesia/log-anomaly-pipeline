"""Command-line interface for generation, analysis, and benchmarks."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from log_analyzer.generator import GenerationConfig, generate_logs
from log_analyzer.pipeline import AnalysisConfig, benchmark_parser, run_analysis


def _json_print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2))


def _add_generation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--minutes", type=int, default=180, help="minutes of traffic (default: 180)"
    )
    parser.add_argument(
        "--base-rpm", type=int, default=120, help="baseline requests per minute (default: 120)"
    )
    parser.add_argument("--shards", type=int, default=4, help="number of output files (default: 4)")
    parser.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="log-analyzer",
        description="Generate, parse, and statistically analyze JSONL server logs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="generate labeled synthetic logs")
    generate.add_argument("--output-dir", type=Path, default=Path("data/generated"))
    _add_generation_arguments(generate)

    analyze = subparsers.add_parser("analyze", help="analyze one or more log files/directories")
    analyze.add_argument("inputs", nargs="+", type=Path)
    analyze.add_argument("--output-dir", type=Path, default=Path("artifacts/analysis"))
    analyze.add_argument(
        "--detector",
        choices=["fixed-threshold", "rolling-zscore", "spc"],
        default="rolling-zscore",
    )
    analyze.add_argument(
        "--workers", type=int, default=0, help="process count; 0 selects automatically"
    )
    analyze.add_argument("--chunk-size-mb", type=int, default=64)

    benchmark = subparsers.add_parser("benchmark", help="benchmark parallel parser throughput")
    benchmark.add_argument("inputs", nargs="+", type=Path)
    benchmark.add_argument("--workers", nargs="+", type=int, default=[1, 2, 4])
    benchmark.add_argument("--repeats", type=int, default=3)
    benchmark.add_argument("--chunk-size-mb", type=int, default=64)
    benchmark.add_argument("--output", type=Path, default=Path("benchmark_results/parser.csv"))

    demo = subparsers.add_parser("demo", help="generate data and run the full pipeline")
    demo.add_argument("--output-dir", type=Path, default=Path("artifacts/demo"))
    demo.add_argument(
        "--workers", type=int, default=0, help="process count; 0 selects automatically"
    )
    _add_generation_arguments(demo)
    return parser


def _generation_config(args: argparse.Namespace) -> GenerationConfig:
    return GenerationConfig(
        minutes=args.minutes,
        base_requests_per_minute=args.base_rpm,
        shard_count=args.shards,
        seed=args.seed,
    )


def _run_command(args: argparse.Namespace) -> None:
    if args.command == "generate":
        summary = generate_logs(args.output_dir, _generation_config(args))
        _json_print(summary.to_dict())
        return

    if args.command == "analyze":
        summary = run_analysis(
            AnalysisConfig(
                input_paths=tuple(args.inputs),
                output_dir=args.output_dir,
                detector=args.detector,
                workers=args.workers,
                chunk_size_mb=args.chunk_size_mb,
            )
        )
        _json_print(summary)
        return

    if args.command == "benchmark":
        result = benchmark_parser(
            tuple(args.inputs),
            tuple(args.workers),
            repeats=args.repeats,
            chunk_size_mb=args.chunk_size_mb,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(args.output, index=False, float_format="%.6f")
        print(result.to_string(index=False))
        print(f"\nSaved benchmark to {args.output}")
        return

    if args.command == "demo":
        log_dir = args.output_dir / "logs"
        analysis_dir = args.output_dir / "analysis"
        generated = generate_logs(log_dir, _generation_config(args))
        analyzed = run_analysis(
            AnalysisConfig(
                input_paths=(log_dir,),
                output_dir=analysis_dir,
                detector="rolling-zscore",
                workers=args.workers,
            )
        )
        _json_print({"generation": generated.to_dict(), "analysis": analyzed})
        return

    raise ValueError(f"unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _run_command(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
