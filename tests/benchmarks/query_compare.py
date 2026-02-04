"""Query Counting Comparison - Compare Before/After Optimization.

Reads two query baseline files and generates a comparison report showing:
- Query count reductions
- Regressions (increases)
- Percentage changes
- Query type breakdowns (SELECT/INSERT/UPDATE/DELETE)

Usage:
    uv run python -m tests.benchmarks.query_compare before.json after.json
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
class QueryDiff:
    """Difference between two query count benchmark runs."""

    name: str
    category: str
    before: dict[str, Any]
    after: dict[str, Any]
    diff_queries: dict[str, float]
    pct_change: dict[str, float]
    improved: bool
    regressed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "before": self.before,
            "after": self.after,
            "diff_queries": self.diff_queries,
            "pct_change": self.pct_change,
            "improved": self.improved,
            "regressed": self.regressed,
        }


class QueryComparator:
    """Compare query counting baselines."""

    def __init__(self, before_path: str | Path, after_path: str | Path):
        self.before_path = Path(before_path)
        self.after_path = Path(after_path)
        self.before_data: dict[str, Any] | None = None
        self.after_data: dict[str, Any] | None = None
        self.diffs: list[QueryDiff] = []

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
            before_queries = before.get("queries", {})
            after_queries = after.get("queries", {})

            diff_queries = {}
            pct_change = {}

            # Compare total queries
            before_total = before_queries.get("total", {}).get("mean", 0)
            after_total = after_queries.get("total", {}).get("mean", 0)

            if before_total > 0:
                diff_queries["total"] = after_total - before_total
                pct_change["total"] = ((after_total - before_total) / before_total) * 100
            else:
                diff_queries["total"] = after_total
                pct_change["total"] = 0

            # Compare by type
            for query_type in ["select", "insert", "update", "delete"]:
                before_type = before_queries.get("by_type", {}).get(query_type, {}).get("mean", 0)
                after_type = after_queries.get("by_type", {}).get(query_type, {}).get("mean", 0)

                if before_type > 0:
                    diff_queries[query_type] = after_type - before_type
                    pct_change[query_type] = ((after_type - before_type) / before_type) * 100
                else:
                    diff_queries[query_type] = after_type
                    pct_change[query_type] = 0

            improved = pct_change.get("total", 0) < -0.1
            regressed = pct_change.get("total", 0) > 0.1

            self.diffs.append(
                QueryDiff(
                    name=name,
                    category=category,
                    before=before_queries,
                    after=after_queries,
                    diff_queries=diff_queries,
                    pct_change=pct_change,
                    improved=improved,
                    regressed=regressed,
                )
            )

    def print_report(self) -> None:
        print("=" * 70)
        print("[*] QUERY COUNTING COMPARISON REPORT")
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

        print("\n[+] IMPROVEMENTS (Query Reductions):")
        print("-" * 70)
        if improved:
            for diff in improved:
                total_change = diff.pct_change.get("total", 0)
                print(
                    f"  {diff.name}: "
                    f"{diff.before.get('total', {}).get('mean', 0):.1f} → "
                    f"{diff.after.get('total', {}).get('mean', 0):.1f} queries "
                    f"({total_change:+.2f}%)"
                )
                # Show breakdown by type
                if diff.pct_change.get("select", 0) != 0:
                    print(f"    SELECT: {diff.pct_change.get('select', 0):+.2f}%")
        else:
            print("  No improvements detected")

        print("\n[-] REGRESSIONS (Query Increases):")
        print("-" * 70)
        if regressed:
            for diff in regressed:
                total_change = diff.pct_change.get("total", 0)
                print(
                    f"  {diff.name}: "
                    f"{diff.before.get('total', {}).get('mean', 0):.1f} → "
                    f"{diff.after.get('total', {}).get('mean', 0):.1f} queries "
                    f"({total_change:+.2f}%)"
                )
                # Show breakdown by type
                if diff.pct_change.get("select", 0) != 0:
                    print(f"    SELECT: {diff.pct_change.get('select', 0):+.2f}%")
        else:
            print("  No regressions detected")

        print("\n[=] UNCHANGED (Within 0.1% threshold):")
        print("-" * 70)
        if unchanged:
            for diff in unchanged:
                total_change = diff.pct_change.get("total", 0)
                print(f"  {diff.name} ({total_change:+.2f}%)")
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
            avg_improvement = sum(d.pct_change.get("total", 0) for d in improved) / len(improved)
            print(f"  Avg improvement: {avg_improvement:.2f}%")

        if regressed:
            avg_regression = sum(d.pct_change.get("total", 0) for d in regressed) / len(regressed)
            print(f"  Avg regression: {avg_regression:.2f}%")

        # Show query type breakdown
        print("\n[BY TYPE] QUERY TYPE CHANGES:")
        print("-" * 70)
        for query_type in ["select", "insert", "update", "delete"]:
            type_changes = [d.pct_change.get(query_type, 0) for d in self.diffs if d.pct_change.get(query_type, 0) != 0]
            if type_changes:
                avg_change = sum(type_changes) / len(type_changes)
                print(f"  {query_type.upper()}: {avg_change:+.2f}% avg ({len(type_changes)} benchmarks)")

    def save_report(self, output_path: str | Path) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        improved = [d for d in self.diffs if d.improved]
        regressed = [d for d in self.diffs if d.regressed]
        unchanged = [d for d in self.diffs if not d.improved and not d.regressed]

        # Calculate type-level aggregates
        type_aggregates: dict[str, dict] = {}
        for query_type in ["select", "insert", "update", "delete"]:
            type_changes = [d.pct_change.get(query_type, 0) for d in self.diffs if d.pct_change.get(query_type, 0) != 0]
            if type_changes:
                type_aggregates[query_type] = {
                    "avg_change_pct": sum(type_changes) / len(type_changes),
                    "affected_benchmarks": len(type_changes),
                }

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
                    sum(d.pct_change.get("total", 0) for d in improved) / len(improved) if improved else 0
                ),
                "avg_regression_pct": (
                    sum(d.pct_change.get("total", 0) for d in regressed) / len(regressed) if regressed else 0
                ),
                "by_query_type": type_aggregates,
            },
            "improvements": [d.to_dict() for d in improved],
            "regressions": [d.to_dict() for d in regressed],
            "unchanged": [d.to_dict() for d in unchanged],
        }

        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)

        print(f"\n[FILE] Comparison report saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Compare query counting baselines")
    parser.add_argument("before", type=str, help="Before baseline JSON file")
    parser.add_argument("after", type=str, help="After baseline JSON file")
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="tests/benchmarks/baselines/query_comparison.json",
        help="Output file for comparison report (default: tests/benchmarks/baselines/query_comparison.json)",
    )

    args = parser.parse_args()

    comparator = QueryComparator(args.before, args.after)
    comparator.load()
    comparator.compare()
    comparator.print_report()
    comparator.save_report(args.output)


if __name__ == "__main__":
    main()
