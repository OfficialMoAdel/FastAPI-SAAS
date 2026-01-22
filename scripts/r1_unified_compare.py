#!/usr/bin/env python3
"""
R1-UNIFIED Performance Comparison Script.

Compares baseline metrics with post-optimization metrics to calculate
improvements and generate a summary report.

Usage:
    uv run python scripts/r1_unified_compare.py baseline.json optimized.json
    uv run python scripts/r1_unified_compare.py --baseline baseline.json --after optimized.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ComparisonResult:
    """Result of comparing a single metric."""

    metric_name: str
    baseline_value: float | None = None
    after_value: float | None = None
    improvement_pct: float | None = None
    improvement_abs: float | None = None
    unit: str = ""
    passed_threshold: bool = True
    notes: str = ""


@dataclass
class AreaComparison:
    """Comparison results for an optimization area."""

    area_name: str
    metrics: list[ComparisonResult] = field(default_factory=list)
    summary: str = ""
    overall_improvement: float | None = None


@dataclass
class FullComparison:
    """Complete comparison results."""

    areas: list[AreaComparison] = field(default_factory=list)
    summary_table: list[dict[str, Any]] = field(default_factory=list)
    overall_status: str = ""


# Expected improvement thresholds (min % improvement expected)
IMPROVEMENT_THRESHOLDS = {
    "event_idempotency": {"duplicates": 100},  # Should be 0 after fix
    "session_eviction": {"roundtrips": 30},  # ~3-4 fewer roundtrips
    "xfetch_cache": {"combined_latency_ms": 40},  # Single roundtrip vs 2
    "rbac_permissions": {"query_latency_ms": 50},  # Cached vs DB query
    "dlq_metrics": {},  # No improvement expected, monitoring only
    "passkey_query": {"query_latency_ms": 10},  # Minor improvement
    "circuit_breaker": {"state_check_ns": 5},  # Minor improvement
}


def load_json(path: Path) -> dict:
    """Load JSON file."""
    with open(path) as f:
        return json.load(f)


def extract_value(metric: dict | None) -> float | None:
    """Extract numeric value from metric dict."""
    if metric is None:
        return None
    if isinstance(metric, dict):
        return metric.get("value")
    return None


def calculate_improvement(baseline: float | None, after: float | None) -> tuple[float | None, float | None]:
    """Calculate improvement percentage and absolute difference.

    Returns (pct_improvement, abs_improvement).
    Positive values indicate improvement (lower is better).
    """
    if baseline is None or after is None:
        return None, None
    if baseline == 0:
        return None, after - baseline

    abs_improvement = baseline - after
    pct_improvement = (abs_improvement / baseline) * 100
    return pct_improvement, abs_improvement


def compare_area(
    area_name: str,
    baseline_data: dict,
    after_data: dict,
    thresholds: dict[str, float],
) -> AreaComparison:
    """Compare metrics for a single optimization area."""
    result = AreaComparison(area_name=area_name)

    # Get all metrics from both datasets
    all_metrics = set(baseline_data.keys()) | set(after_data.keys())

    improvements = []

    for metric_name in sorted(all_metrics):
        baseline_metric = baseline_data.get(metric_name)
        after_metric = after_data.get(metric_name)

        baseline_val = extract_value(baseline_metric)
        after_val = extract_value(after_metric)

        pct_imp, abs_imp = calculate_improvement(baseline_val, after_val)

        # Check threshold
        threshold = thresholds.get(metric_name)
        passed = True
        if threshold is not None and pct_imp is not None:
            passed = pct_imp >= threshold

        # Get unit
        unit = ""
        if baseline_metric and isinstance(baseline_metric, dict):
            unit = baseline_metric.get("unit", "")
        elif after_metric and isinstance(after_metric, dict):
            unit = after_metric.get("unit", "")

        comparison = ComparisonResult(
            metric_name=metric_name,
            baseline_value=baseline_val,
            after_value=after_val,
            improvement_pct=pct_imp,
            improvement_abs=abs_imp,
            unit=unit,
            passed_threshold=passed,
        )

        result.metrics.append(comparison)

        if pct_imp is not None:
            improvements.append(pct_imp)

    # Calculate overall improvement for area
    if improvements:
        result.overall_improvement = sum(improvements) / len(improvements)
        result.summary = f"Average improvement: {result.overall_improvement:.1f}%"

    return result


def generate_summary_table(areas: list[AreaComparison]) -> list[dict[str, Any]]:
    """Generate summary table for all areas."""
    table = []

    for area in areas:
        row = {
            "area": area.area_name,
            "overall_improvement_pct": area.overall_improvement,
            "metrics_improved": sum(1 for m in area.metrics if m.improvement_pct is not None and m.improvement_pct > 0),
            "metrics_total": len(area.metrics),
            "all_thresholds_passed": all(m.passed_threshold for m in area.metrics),
        }
        table.append(row)

    return table


def compare_results(baseline_path: Path, after_path: Path) -> FullComparison:
    """Compare baseline and after results."""
    baseline = load_json(baseline_path)
    after = load_json(after_path)

    baseline_opts = baseline.get("optimizations", {})
    after_opts = after.get("optimizations", {})

    comparison = FullComparison()

    # Compare each optimization area
    for area_name in IMPROVEMENT_THRESHOLDS:
        baseline_area = baseline_opts.get(area_name, {})
        after_area = after_opts.get(area_name, {})
        thresholds = IMPROVEMENT_THRESHOLDS.get(area_name, {})

        area_comparison = compare_area(area_name, baseline_area, after_area, thresholds)
        comparison.areas.append(area_comparison)

    # Generate summary
    comparison.summary_table = generate_summary_table(comparison.areas)

    # Overall status
    all_passed = all(m.passed_threshold for area in comparison.areas for m in area.metrics)
    comparison.overall_status = "PASS" if all_passed else "NEEDS REVIEW"

    return comparison


def print_comparison(comparison: FullComparison) -> None:
    """Print comparison results to console."""
    print("\n" + "=" * 70)
    print("R1-UNIFIED PERFORMANCE COMPARISON REPORT")
    print("=" * 70)

    for area in comparison.areas:
        print(f"\n{'─' * 70}")
        print(f"📊 {area.area_name.upper().replace('_', ' ')}")
        print(f"{'─' * 70}")

        if area.overall_improvement is not None:
            print(f"Overall: {area.overall_improvement:+.1f}% improvement")

        print(f"\n{'Metric':<30} {'Baseline':>12} {'After':>12} {'Improvement':>12}")
        print("-" * 70)

        for metric in area.metrics:
            baseline_str = f"{metric.baseline_value:.3f}" if metric.baseline_value else "N/A"
            after_str = f"{metric.after_value:.3f}" if metric.after_value else "N/A"

            if metric.improvement_pct is not None:
                imp_str = f"{metric.improvement_pct:+.1f}%"
                status = "✅" if metric.passed_threshold else "⚠️"
            else:
                imp_str = "N/A"
                status = "❓"

            print(f"{metric.metric_name:<30} {baseline_str:>12} {after_str:>12} {imp_str:>10} {status}")

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print("=" * 70)

    print(f"\n{'Area':<25} {'Improvement':>15} {'Thresholds':>15}")
    print("-" * 55)

    for row in comparison.summary_table:
        imp_str = f"{row['overall_improvement_pct']:.1f}%" if row["overall_improvement_pct"] else "N/A"
        status = "PASS" if row["all_thresholds_passed"] else "FAIL"
        print(f"{row['area']:<25} {imp_str:>15} {status:>15}")

    print(f"\n{'=' * 70}")
    print(f"OVERALL STATUS: {comparison.overall_status}")
    print("=" * 70)


def save_comparison(comparison: FullComparison, output_path: Path) -> None:
    """Save comparison to JSON file."""

    def serialize(obj: Any) -> Any:
        if hasattr(obj, "__dict__"):
            return {k: serialize(v) for k, v in obj.__dict__.items()}
        elif isinstance(obj, list):
            return [serialize(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: serialize(v) for k, v in obj.items()}
        else:
            return obj

    with open(output_path, "w") as f:
        json.dump(serialize(comparison), f, indent=2)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Compare R1-UNIFIED baseline vs optimized metrics")
    parser.add_argument(
        "baseline",
        nargs="?",
        type=str,
        help="Path to baseline metrics JSON",
    )
    parser.add_argument(
        "after",
        nargs="?",
        type=str,
        help="Path to after-optimization metrics JSON",
    )
    parser.add_argument(
        "--baseline",
        dest="baseline_opt",
        type=str,
        help="Path to baseline metrics JSON (alternative)",
    )
    parser.add_argument(
        "--after",
        dest="after_opt",
        type=str,
        help="Path to after-optimization metrics JSON (alternative)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output path for comparison JSON",
    )

    args = parser.parse_args()

    # Resolve paths
    baseline_path = Path(args.baseline_opt or args.baseline)
    after_path = Path(args.after_opt or args.after)

    if not baseline_path.exists():
        print(f"Error: Baseline file not found: {baseline_path}")
        sys.exit(1)

    if not after_path.exists():
        print(f"Error: After file not found: {after_path}")
        sys.exit(1)

    # Run comparison
    comparison = compare_results(baseline_path, after_path)

    # Print results
    print_comparison(comparison)

    # Save if requested
    if args.output:
        output_path = Path(args.output)
        save_comparison(comparison, output_path)
        print(f"\nComparison saved to: {output_path}")


if __name__ == "__main__":
    main()
