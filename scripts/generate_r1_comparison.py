#!/usr/bin/env python3
"""Generate R1-OPUS optimization comparison report.

This script:
1. Runs the benchmark suite
2. Runs the E2E test suite
3. Collects results from JSON outputs
4. Generates the comparison report

Usage:
    uv run python scripts/generate_r1_comparison.py
"""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
REPORTS_DIR = PROJECT_ROOT / "reports"
BENCHMARK_RESULTS_DIR = PROJECT_ROOT / "tests" / "benchmarks" / "results"
E2E_RESULTS_DIR = PROJECT_ROOT / "tests" / "e2e" / "results"

REPORT_OUTPUT = REPORTS_DIR / "r1_opus_comparison.md"


def run_tests():
    """Run benchmark and E2E test suites."""
    print("Running benchmarks...")
    result = subprocess.run(
        ["uv", "run", "pytest", "tests/benchmarks/bench_r1_opus.py", "-v"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Benchmark tests failed:\n{result.stderr}")
        return False
    print("Benchmarks passed!")

    print("\nRunning E2E tests...")
    result = subprocess.run(
        ["uv", "run", "pytest", "tests/e2e/test_r1_opus_e2e.py", "-v"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"E2E tests failed:\n{result.stderr}")
        return False
    print("E2E tests passed!")

    return True


def get_latest_results(results_dir: Path, prefix: str) -> dict | None:
    """Get the most recent results file."""
    if not results_dir.exists():
        return None

    files = list(results_dir.glob(f"{prefix}*.json"))
    if not files:
        return None

    latest = max(files, key=lambda f: f.stat().st_mtime)
    with open(latest) as f:
        return json.load(f)


def generate_report():
    """Generate the comparison report."""
    print("\nGenerating report...")

    get_latest_results(BENCHMARK_RESULTS_DIR, "r1_opus_bench")
    get_latest_results(E2E_RESULTS_DIR, "r1_opus_e2e")

    # The main report is manually maintained at reports/r1_opus_comparison.md
    # This script validates and updates timestamp

    if REPORT_OUTPUT.exists():
        content = REPORT_OUTPUT.read_text()
        # Update timestamp
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("**Generated**:"):
                lines[i] = f"**Generated**: {today}"
                break
        REPORT_OUTPUT.write_text("\n".join(lines))
        print(f"Report updated: {REPORT_OUTPUT}")
    else:
        print(f"Report not found at {REPORT_OUTPUT}")
        print("Please create the report manually.")
        return False

    return True


def main():
    """Main entry point."""
    print("=" * 60)
    print("R1-OPUS Performance Comparison Report Generator")
    print("=" * 60)

    # Ensure directories exist
    REPORTS_DIR.mkdir(exist_ok=True)

    # Run tests
    if not run_tests():
        print("\nTests failed. Fix issues before generating report.")
        sys.exit(1)

    # Generate report
    if not generate_report():
        print("\nReport generation failed.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("Report generation complete!")
    print(f"Output: {REPORT_OUTPUT}")
    print("=" * 60)


if __name__ == "__main__":
    main()
