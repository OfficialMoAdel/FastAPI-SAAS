#!/usr/bin/env python3
"""Benchmark comparison script for Opus performance optimizations.

Compares post-optimization benchmarks against baseline to quantify improvements.

Usage:
    python scripts/compare_benchmarks.py [--baseline PATH] [--current PATH] [--output PATH]

Requirements:
    - Baseline JSON from `pytest --benchmark-json=baseline.json`
    - Current JSON from `pytest --benchmark-json=current.json`
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class BenchmarkResult:
    """Single benchmark result."""

    name: str
    mean: float
    stddev: float
    min_val: float
    max_val: float
    rounds: int
    ops: float  # ops per second

    @classmethod
    def from_benchmark(cls, bench: dict[str, Any]) -> "BenchmarkResult":
        """Create from pytest-benchmark JSON format."""
        stats = bench.get("stats", {})
        mean = stats.get("mean", 0)
        return cls(
            name=bench.get("name", "unknown"),
            mean=mean,
            stddev=stats.get("stddev", 0),
            min_val=stats.get("min", 0),
            max_val=stats.get("max", 0),
            rounds=stats.get("rounds", 0),
            ops=1 / mean if mean > 0 else 0,
        )


@dataclass
class Comparison:
    """Comparison between baseline and current benchmark."""

    name: str
    baseline: BenchmarkResult
    current: BenchmarkResult
    improvement_pct: float
    speedup: float

    @property
    def improved(self) -> bool:
        """True if current is faster than baseline."""
        return self.speedup > 1.0

    @property
    def status(self) -> str:
        """Human-readable status."""
        if self.speedup >= 2.0:
            return "🚀 MAJOR"
        elif self.speedup >= 1.2:
            return "✅ IMPROVED"
        elif self.speedup >= 0.95:
            return "➖ NEUTRAL"
        else:
            return "⚠️ REGRESSION"


def load_benchmarks(path: Path) -> dict[str, BenchmarkResult]:
    """Load benchmarks from JSON file."""
    with open(path) as f:
        data = json.load(f)

    results = {}
    for bench in data.get("benchmarks", []):
        result = BenchmarkResult.from_benchmark(bench)
        results[result.name] = result

    return results


def compare_benchmarks(
    baseline: dict[str, BenchmarkResult],
    current: dict[str, BenchmarkResult],
) -> list[Comparison]:
    """Compare baseline and current benchmarks."""
    comparisons = []

    # Find common benchmarks
    common_names = set(baseline.keys()) & set(current.keys())

    for name in sorted(common_names):
        base = baseline[name]
        curr = current[name]

        # Speedup = baseline_time / current_time (higher = faster)
        speedup = base.mean / curr.mean if curr.mean > 0 else 0
        improvement_pct = (1 - curr.mean / base.mean) * 100 if base.mean > 0 else 0

        comparisons.append(
            Comparison(
                name=name,
                baseline=base,
                current=curr,
                improvement_pct=improvement_pct,
                speedup=speedup,
            )
        )

    return comparisons


def format_time(seconds: float) -> str:
    """Format time in human-readable units."""
    if seconds >= 1:
        return f"{seconds:.3f}s"
    elif seconds >= 0.001:
        return f"{seconds * 1000:.2f}ms"
    elif seconds >= 0.000001:
        return f"{seconds * 1000000:.2f}μs"
    else:
        return f"{seconds * 1000000000:.2f}ns"


def generate_markdown_report(
    comparisons: list[Comparison],
    baseline_path: Path,
    current_path: Path,
) -> str:
    """Generate markdown comparison report."""
    lines = [
        "# Performance Optimization Results",
        "",
        f"**Generated:** {datetime.now().isoformat()}",
        f"**Baseline:** `{baseline_path}`",
        f"**Current:** `{current_path}`",
        "",
        "## Summary",
        "",
    ]

    # Count improvements
    major = sum(1 for c in comparisons if c.speedup >= 2.0)
    improved = sum(1 for c in comparisons if 1.2 <= c.speedup < 2.0)
    neutral = sum(1 for c in comparisons if 0.95 <= c.speedup < 1.2)
    regression = sum(1 for c in comparisons if c.speedup < 0.95)

    lines.extend(
        [
            f"- 🚀 Major improvements (2x+): **{major}**",
            f"- ✅ Improvements (1.2x+): **{improved}**",
            f"- ➖ Neutral: **{neutral}**",
            f"- ⚠️ Regressions: **{regression}**",
            "",
            "## Detailed Results",
            "",
            "| Benchmark | Status | Baseline | Current | Speedup | Improvement |",
            "|-----------|--------|----------|---------|---------|-------------|",
        ]
    )

    for comp in sorted(comparisons, key=lambda c: -c.speedup):
        lines.append(
            f"| `{comp.name}` | {comp.status} | {format_time(comp.baseline.mean)} | "
            f"{format_time(comp.current.mean)} | {comp.speedup:.2f}x | {comp.improvement_pct:+.1f}% |"
        )

    lines.extend(
        [
            "",
            "## Optimization Categories",
            "",
        ]
    )

    # Group by optimization type
    categories = {
        "JSON/Serialization": ["json", "orjson", "serializ"],
        "Database": ["query", "sql", "db", "engine"],
        "Auth/Security": ["auth", "token", "passkey", "jwt"],
        "Cache": ["cache", "redis"],
    }

    for category, keywords in categories.items():
        category_comps = [c for c in comparisons if any(kw in c.name.lower() for kw in keywords)]
        if category_comps:
            avg_speedup = sum(c.speedup for c in category_comps) / len(category_comps)
            lines.append(f"### {category}")
            lines.append(f"Average speedup: **{avg_speedup:.2f}x**")
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Compare benchmark results")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("tests/benchmarks/baselines/baseline.json"),
        help="Path to baseline benchmark JSON",
    )
    parser.add_argument(
        "--current",
        type=Path,
        default=Path("benchmark_current.json"),
        help="Path to current benchmark JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("OPTIMIZATION_RESULTS.md"),
        help="Output markdown file",
    )
    args = parser.parse_args()

    if not args.baseline.exists():
        print(f"Error: Baseline file not found: {args.baseline}")
        print("Run baseline capture first: pytest --benchmark-json=baseline.json")
        return 1

    if not args.current.exists():
        print(f"Error: Current benchmark file not found: {args.current}")
        print("Run benchmarks: pytest --benchmark-json=benchmark_current.json")
        return 1

    print(f"Loading baseline: {args.baseline}")
    baseline = load_benchmarks(args.baseline)
    print(f"  Found {len(baseline)} benchmarks")

    print(f"Loading current: {args.current}")
    current = load_benchmarks(args.current)
    print(f"  Found {len(current)} benchmarks")

    print("Comparing benchmarks...")
    comparisons = compare_benchmarks(baseline, current)

    print(f"Generating report: {args.output}")
    report = generate_markdown_report(comparisons, args.baseline, args.current)

    with open(args.output, "w") as f:
        f.write(report)

    # Print summary to console
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for comp in sorted(comparisons, key=lambda c: -c.speedup)[:10]:
        print(f"  {comp.status} {comp.name}: {comp.speedup:.2f}x ({comp.improvement_pct:+.1f}%)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
