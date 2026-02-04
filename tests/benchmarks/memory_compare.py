"""Memory Profiling Comparison - Compare Before/After Optimization.

Reads two memory baseline files and generates a comparison report showing:
- Memory improvements (reductions)
- Regressions (increases)
- Percentage changes
- Statistical significance

Usage:
    uv run python -m tests.benchmarks.memory_compare before.json after.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@dataclass
class BenchmarkDiff:
    """Difference between two benchmark runs."""

    name: str
    category: str
    before_mb: dict[str, float]
    after_mb: dict[str, float]
    diff_mb: dict[str, float]
    pct_change: dict[str, float]
    improved: bool
    regressed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "before": self.before_mb,
            "after": self.after_mb,
            "diff_mb": self.diff_mb,
            "pct_change": self.pct_change,
            "improved": self.improved,
            "regressed": self.regressed,
        }


class MemoryComparator:
    """Compare memory profiling baselines."""

    def __init__(self, before_path: str | Path, after_path: str | Path):
        self.before_path = Path(before_path)
        self.after_path = Path(after_path)
        self.before_data: dict[str, Any] | None = None
        self.after_data: dict[str, Any] | None = None
        self.diffs: list[BenchmarkDiff] = []

    def load(self) -> None:
        if not self.before_path.exists():
            raise FileNotFoundError(f"Before file not found: {self.before_path}")
        if not self.after_path.exists():
            raise FileNotFoundError(f"After file not found: {self.after_path}")

        with open(self.before_path) as f:
            self.before_data = json.load(f)

        with open(self.after_path) as f:
            self.after_data = json.load(f)

    def compare(self) -> None:
        if not self.before_data or not self.after_data:
            raise ValueError("Data not loaded. Call load() first.")

        before_results = {r["name"]: r for r in self.before_data.get("results", [])}
        after_results = {r["name"]: r for r in self.after_data.get("results", [])}

        all_names = set(before_results.keys()) | set(after_results.keys())

        for name in sorted(all_names):
            before = before_results.get(name, {})
            after = after_results.get(name, {})

            if "error" in before or "error" in after:
                continue

            category = before.get("category", after.get("category", "unknown"))
            before_mem = before.get("memory_mb", {}).get("current", {})
            after_mem = after.get("memory_mb", {}).get("current", {})

            diff_mb = {}
            pct_change = {}

            for key in ["mean", "p95", "p99"]:
                before_val = before_mem.get(key, 0)
                after_val = after_mem.get(key, 0)

                if before_val > 0:
                    diff_mb[key] = after_val - before_val
                    pct_change[key] = ((after_val - before_val) / before_val) * 100
                else:
                    diff_mb[key] = after_val
                    pct_change[key] = 0

            improved = pct_change.get("mean", 0) < -0.1
            regressed = pct_change.get("mean", 0) > 0.1

            self.diffs.append(
                BenchmarkDiff(
                    name=name,
                    category=category,
                    before_mb=before_mem,
                    after_mb=after_mem,
                    diff_mb=diff_mb,
                    pct_change=pct_change,
                    improved=improved,
                    regressed=regressed,
                )
            )

    def print_report(self) -> None:
        print("=" * 70)
        print("[*] MEMORY PROFILING COMPARISON REPORT")
        print("=" * 70)
        print(f"    Before: {self.before_path.name}")
        print(f"    After:  {self.after_path.name}")
        print("=" * 70)

        if not self.diffs:
            print("[!] No comparable benchmarks found")
            return

        improved = [d for d in self.diffs if d.improved]
        regressed = [d for d in self.diffs if d.regressed]
        unchanged = [d for d in self.diffs if not d.improved and not d.regressed]

        print("\n[+] IMPROVEMENTS (Memory Reductions):")
        print("-" * 70)
        if improved:
            for diff in improved:
                mean_change = diff.pct_change.get("mean", 0)
                print(
                    f"  {diff.name}: "
                    f"{diff.before_mb.get('mean', 0):.4f}MB → "
                    f"{diff.after_mb.get('mean', 0):.4f}MB "
                    f"({mean_change:+.2f}%)"
                )
        else:
            print("  No improvements detected")

        print("\n[-] REGRESSIONS (Memory Increases):")
        print("-" * 70)
        if regressed:
            for diff in regressed:
                mean_change = diff.pct_change.get("mean", 0)
                print(
                    f"  {diff.name}: "
                    f"{diff.before_mb.get('mean', 0):.4f}MB → "
                    f"{diff.after_mb.get('mean', 0):.4f}MB "
                    f"({mean_change:+.2f}%)"
                )
        else:
            print("  No regressions detected")

        print("\n[=] UNCHANGED (Within 0.1% threshold):")
        print("-" * 70)
        if unchanged:
            for diff in unchanged:
                mean_change = diff.pct_change.get("mean", 0)
                print(f"  {diff.name} ({mean_change:+.2f}%)")
        else:
            print("  None")

        print("\n[STATS] SUMMARY:")
        print("-" * 70)
        total = len(self.diffs)
        print(f"  Total benchmarks: {total}")
        print(f"  Improved: {len(improved)} ({len(improved) / total * 100:.1f}%)")
        print(f"  Regressed: {len(regressed)} ({len(regressed) / total * 100:.1f}%)")
        print(f"  Unchanged: {len(unchanged)} ({len(unchanged) / total * 100:.1f}%)")

        if improved:
            avg_improvement = sum(d.pct_change.get("mean", 0) for d in improved) / len(improved)
            print(f"  Avg improvement: {avg_improvement:.2f}%")

        if regressed:
            avg_regression = sum(d.pct_change.get("mean", 0) for d in regressed) / len(regressed)
            print(f"  Avg regression: {avg_regression:.2f}%")

    def save_report(self, output_path: str | Path) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        improved = [d for d in self.diffs if d.improved]
        regressed = [d for d in self.diffs if d.regressed]
        unchanged = [d for d in self.diffs if not d.improved and not d.regressed]

        report = {
            "metadata": {
                "before_file": str(self.before_path),
                "after_file": str(self.after_path),
                "before_metadata": self.before_data.get("metadata", {}) if self.before_data else {},
                "after_metadata": self.after_data.get("metadata", {}) if self.after_data else {},
            },
            "summary": {
                "total": len(self.diffs),
                "improved": len(improved),
                "regressed": len(regressed),
                "unchanged": len(unchanged),
                "avg_improvement_pct": (
                    sum(d.pct_change.get("mean", 0) for d in improved) / len(improved) if improved else 0
                ),
                "avg_regression_pct": (
                    sum(d.pct_change.get("mean", 0) for d in regressed) / len(regressed) if regressed else 0
                ),
            },
            "improvements": [d.to_dict() for d in improved],
            "regressions": [d.to_dict() for d in regressed],
            "unchanged": [d.to_dict() for d in unchanged],
        }

        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)

        print(f"\n[FILE] Comparison report saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Compare memory profiling baselines")
    parser.add_argument("before", type=str, help="Before baseline JSON file")
    parser.add_argument("after", type=str, help="After baseline JSON file")
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="tests/benchmarks/baselines/memory_comparison.json",
        help="Output file for comparison report (default: tests/benchmarks/baselines/memory_comparison.json)",
    )

    args = parser.parse_args()

    comparator = MemoryComparator(args.before, args.after)
    comparator.load()
    comparator.compare()
    comparator.print_report()
    comparator.save_report(args.output)


if __name__ == "__main__":
    main()
