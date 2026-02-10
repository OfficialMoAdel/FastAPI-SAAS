#!/usr/bin/env python3
"""
Performance Metrics Comparison Tool for Load Testing Results.

Compares baseline metrics against post-optimization results and generates
a detailed comparison report with percentage improvements.

Usage:
    python scripts/compare_metrics.py \
        --baseline tests/performance/baselines/baseline.csv \
        --optimized tests/performance/results/optimized.csv
    python scripts/compare_metrics.py --json tests/performance/comparison_report.json

Output:
    - Console summary table
    - JSON report with detailed metrics
    - Markdown report for documentation
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class MetricComparison:
    """Comparison result for a single metric."""

    name: str
    baseline: float
    optimized: float
    unit: str = "ms"

    @property
    def improvement_pct(self) -> float:
        """Calculate percentage improvement."""
        if self.baseline == 0:
            return 0.0
        return ((self.baseline - self.optimized) / self.baseline) * 100

    @property
    def improvement_factor(self) -> float:
        """Calculate speedup factor."""
        if self.optimized == 0:
            return 1.0
        return self.baseline / self.optimized

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "baseline": round(self.baseline, 4),
            "optimized": round(self.optimized, 4),
            "improvement_pct": round(self.improvement_pct, 2),
            "improvement_factor": round(self.improvement_factor, 2),
            "unit": self.unit,
        }


@dataclass
class ComparisonReport:
    """Full comparison report with all metrics."""

    baseline_file: str
    optimized_file: str
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metrics: list[MetricComparison] = field(default_factory=list)

    @property
    def overall_improvement(self) -> float:
        """Calculate overall improvement percentage."""
        if not self.metrics:
            return 0.0
        return sum(m.improvement_pct for m in self.metrics) / len(self.metrics)

    @property
    def best_improvement(self):
        """Get the metric with the best improvement."""
        if not self.metrics:
            return None
        return max(self.metrics, key=lambda m: m.improvement_pct)

    @property
    def worst_improvement(self):
        """Get the metric with the worst (or negative) improvement."""
        if not self.metrics:
            return None
        return min(self.metrics, key=lambda m: m.improvement_pct)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "metadata": {
                "baseline_file": self.baseline_file,
                "optimized_file": self.optimized_file,
                "generated_at": self.generated_at,
                "overall_improvement_pct": round(self.overall_improvement, 2),
            },
            "summary": {
                "best_improvement": self.best_improvement.to_dict() if self.best_improvement else None,
                "worst_improvement": self.worst_improvement.to_dict() if self.worst_improvement else None,
                "total_metrics": len(self.metrics),
            },
            "metrics": [m.to_dict() for m in self.metrics],
        }


def parse_csv_metrics(csv_path: Path) -> dict:
    """Parse metrics from Locust CSV output."""
    metrics = {}

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    stats_file = csv_path.parent / f"{csv_path.stem}_stats.csv"
    if stats_file.exists():
        csv_path = stats_file

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Name", row.get("name", "unknown"))
            if name in ("Total", "Aggregated"):
                continue

            try:
                median_ms = float(row.get("Median response time", row.get("Median", 0)))
                p95_ms = float(row.get("95%", row.get("95th percentile", 0)))

                metrics[f"{name}_median_ms"] = median_ms
                metrics[f"{name}_p95_ms"] = p95_ms
            except (ValueError, TypeError):
                continue

    return metrics


def parse_json_metrics(json_path: Path) -> dict:
    """Parse metrics from JSON benchmark output."""
    with open(json_path) as f:
        data = json.load(f)

    metrics = {}

    if "benchmarks" in data:
        for benchmark in data["benchmarks"]:
            name = benchmark.get("name", "unknown")
            stats = benchmark.get("stats", {})

            if "mean" in stats:
                metrics[f"{name}_mean_ms"] = stats["mean"] * 1000
            if "median" in stats:
                metrics[f"{name}_median_ms"] = stats["median"] * 1000

    elif isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict) and "mean_ms" in value:
                metrics[key] = value["mean_ms"]
            elif isinstance(value, int | float):
                metrics[key] = float(value)

    return metrics


def compare_metrics(baseline: dict, optimized: dict) -> list:
    """Compare baseline and optimized metrics."""
    comparisons = []
    common_keys = set(baseline.keys()) & set(optimized.keys())

    for key in sorted(common_keys):
        comparisons.append(
            MetricComparison(
                name=key,
                baseline=baseline[key],
                optimized=optimized[key],
            )
        )

    return comparisons


def print_comparison_table(report: ComparisonReport) -> None:
    """Print comparison table to console."""
    print("\n" + "=" * 80)
    print("PERFORMANCE COMPARISON REPORT")
    print("=" * 80)
    print(f"Baseline:  {report.baseline_file}")
    print(f"Optimized: {report.optimized_file}")
    print(f"Overall Improvement: {report.overall_improvement:+.1f}%")
    print("-" * 80)
    print(f"{'Metric':<40} {'Baseline':>12} {'Optimized':>12} {'Change':>10}")
    print("-" * 80)

    for metric in sorted(report.metrics, key=lambda m: m.improvement_pct, reverse=True):
        status = "✅" if metric.improvement_pct > 0 else "⚠️ " if metric.improvement_pct < 0 else "➖"
        print(
            f"{status} {metric.name:<37} "
            f"{metric.baseline:>10.2f}ms "
            f"{metric.optimized:>10.2f}ms "
            f"{metric.improvement_pct:>+8.1f}%"
        )

    print("=" * 80)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Compare performance metrics")
    parser.add_argument("--baseline", "-b", type=Path, required=True, help="Baseline metrics file")
    parser.add_argument("--optimized", "-o", type=Path, required=True, help="Optimized metrics file")
    parser.add_argument("--output", type=Path, help="Output JSON report path")
    parser.add_argument("--format", choices=["json", "console", "both"], default="both", help="Output format")

    args = parser.parse_args()

    # Parse input files
    if args.baseline.suffix == ".csv":
        baseline_metrics = parse_csv_metrics(args.baseline)
    elif args.baseline.suffix == ".json":
        baseline_metrics = parse_json_metrics(args.baseline)
    else:
        print(f"Unsupported file format: {args.baseline}")
        return 1

    if args.optimized.suffix == ".csv":
        optimized_metrics = parse_csv_metrics(args.optimized)
    elif args.optimized.suffix == ".json":
        optimized_metrics = parse_json_metrics(args.optimized)
    else:
        print(f"Unsupported file format: {args.optimized}")
        return 1

    # Compare metrics
    comparisons = compare_metrics(baseline_metrics, optimized_metrics)
    report = ComparisonReport(
        baseline_file=str(args.baseline),
        optimized_file=str(args.optimized),
        metrics=comparisons,
    )

    # Output results
    if args.format in ("console", "both"):
        print_comparison_table(report)

    if args.format in ("json", "both"):
        output_path = args.output or Path("comparison_report.json")
        with open(output_path, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"\nReport saved to: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())