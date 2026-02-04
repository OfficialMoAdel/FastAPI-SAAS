"""Performance regression detection script.

Compares baseline vs current metrics and alerts on threshold violations:
- 20% latency increase
- 15% throughput drop
- 10% memory growth

Usage:
    uv run python scripts/performance/detect_regressions.py
    uv run python scripts/performance/detect_regressions.py --baseline baseline.json
    uv run python scripts/performance/detect_regressions.py --baseline baseline.json --current current.json
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Regression thresholds
LATENCY_THRESHOLD = 0.20  # 20% increase
THROUGHPUT_THRESHOLD = 0.15  # 15% drop
MEMORY_THRESHOLD = 0.10  # 10% growth

# Exit codes
EXIT_SUCCESS = 0
EXIT_REGRESSION = 1
EXIT_ERROR = 2


class RegressionDetector:
    """Detects performance regressions by comparing baseline and current metrics."""

    def __init__(self, baseline: dict[str, Any], current: dict[str, Any]):
        self.baseline = baseline
        self.current = current
        self.regressions: list[dict[str, Any]] = []
        self.improvements: list[dict[str, Any]] = []

    def check_latency(self) -> None:
        """Check for latency regressions."""
        baseline_latency = self.baseline.get("latency_ms", {})
        current_latency = self.current.get("latency_ms", {})

        for endpoint, baseline_value in baseline_latency.items():
            current_value = current_latency.get(endpoint)

            if current_value is None:
                continue

            # Calculate percent increase
            if baseline_value > 0:
                change = (current_value - baseline_value) / baseline_value
            else:
                change = 0

            if change > LATENCY_THRESHOLD:
                self.regressions.append(
                    {
                        "type": "latency",
                        "endpoint": endpoint,
                        "baseline": baseline_value,
                        "current": current_value,
                        "change_pct": change * 100,
                        "threshold": LATENCY_THRESHOLD * 100,
                    }
                )
            elif change < -LATENCY_THRESHOLD:
                self.improvements.append(
                    {
                        "type": "latency",
                        "endpoint": endpoint,
                        "baseline": baseline_value,
                        "current": current_value,
                        "change_pct": change * 100,
                    }
                )

    def check_throughput(self) -> None:
        """Check for throughput regressions."""
        baseline_throughput = self.baseline.get("throughput_rps", {})
        current_throughput = self.current.get("throughput_rps", {})

        for operation, baseline_value in baseline_throughput.items():
            current_value = current_throughput.get(operation)

            if current_value is None:
                continue

            # Calculate percent drop
            if baseline_value > 0:
                change = (current_value - baseline_value) / baseline_value
            else:
                change = 0

            # Negative change means throughput dropped
            if change < -THROUGHPUT_THRESHOLD:
                self.regressions.append(
                    {
                        "type": "throughput",
                        "operation": operation,
                        "baseline": baseline_value,
                        "current": current_value,
                        "change_pct": change * 100,
                        "threshold": -THROUGHPUT_THRESHOLD * 100,
                    }
                )
            elif change > THROUGHPUT_THRESHOLD:
                self.improvements.append(
                    {
                        "type": "throughput",
                        "operation": operation,
                        "baseline": baseline_value,
                        "current": current_value,
                        "change_pct": change * 100,
                    }
                )

    def check_memory(self) -> None:
        """Check for memory growth."""
        baseline_memory = self.baseline.get("memory_mb", {})
        current_memory = self.current.get("memory_mb", {})

        for phase, baseline_value in baseline_memory.items():
            current_value = current_memory.get(phase)

            if current_value is None:
                continue

            # Calculate percent growth
            if baseline_value > 0:
                change = (current_value - baseline_value) / baseline_value
            else:
                change = 0

            if change > MEMORY_THRESHOLD:
                self.regressions.append(
                    {
                        "type": "memory",
                        "phase": phase,
                        "baseline": baseline_value,
                        "current": current_value,
                        "change_pct": change * 100,
                        "threshold": MEMORY_THRESHOLD * 100,
                    }
                )
            elif change < -MEMORY_THRESHOLD:
                self.improvements.append(
                    {
                        "type": "memory",
                        "phase": phase,
                        "baseline": baseline_value,
                        "current": current_value,
                        "change_pct": change * 100,
                    }
                )

    def run(self) -> bool:
        """Run all regression checks.

        Returns:
            True if no regressions detected, False otherwise
        """
        self.check_latency()
        self.check_throughput()
        self.check_memory()

        return len(self.regressions) == 0

    def print_report(self) -> None:
        """Print regression detection report."""
        print(f"\n{'=' * 60}")
        print("Performance Regression Detection Report")
        print(f"{'=' * 60}")
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Baseline: {self.baseline.get('timestamp', 'N/A')}")
        print(f"Current: {self.current.get('timestamp', 'N/A')}")
        print(f"{'=' * 60}\n")

        # Print regressions
        if self.regressions:
            print(f"⚠️  REGRESSIONS DETECTED ({len(self.regressions)})")
            print(f"{'-' * 60}")

            for regression in self.regressions:
                if regression["type"] == "latency":
                    print(f"\n  🚨 Latency Regression: {regression['endpoint']}")
                    print(f"     Baseline: {regression['baseline']:.2f}ms")
                    print(f"     Current:  {regression['current']:.2f}ms")
                    print(
                        f"     Change:   +{regression['change_pct']:.1f}% (threshold: +{regression['threshold']:.1f}%)"
                    )

                elif regression["type"] == "throughput":
                    print(f"\n  🚨 Throughput Regression: {regression['operation']}")
                    print(f"     Baseline: {regression['baseline']:.2f} rps")
                    print(f"     Current:  {regression['current']:.2f} rps")
                    print(f"     Change:   {regression['change_pct']:.1f}% (threshold: {regression['threshold']:.1f}%)")

                elif regression["type"] == "memory":
                    print(f"\n  🚨 Memory Regression: {regression['phase']}")
                    print(f"     Baseline: {regression['baseline']:.2f} MB")
                    print(f"     Current:  {regression['current']:.2f} MB")
                    print(
                        f"     Change:   +{regression['change_pct']:.1f}% (threshold: +{regression['threshold']:.1f}%)"
                    )

        # Print improvements
        if self.improvements:
            print(f"\n\n✅ IMPROVEMENTS DETECTED ({len(self.improvements)})")
            print(f"{'-' * 60}")

            for improvement in self.improvements:
                if improvement["type"] == "latency":
                    print(f"\n  ⬇️  Latency Improvement: {improvement['endpoint']}")
                    print(f"     Baseline: {improvement['baseline']:.2f}ms")
                    print(f"     Current:  {improvement['current']:.2f}ms")
                    print(f"     Change:   {improvement['change_pct']:.1f}%")

                elif improvement["type"] == "throughput":
                    print(f"\n  ⬆️  Throughput Improvement: {improvement['operation']}")
                    print(f"     Baseline: {improvement['baseline']:.2f} rps")
                    print(f"     Current:  {improvement['current']:.2f} rps")
                    print(f"     Change:   +{improvement['change_pct']:.1f}%")

                elif improvement["type"] == "memory":
                    print(f"\n  ⬇️  Memory Improvement: {improvement['phase']}")
                    print(f"     Baseline: {improvement['baseline']:.2f} MB")
                    print(f"     Current:  {improvement['current']:.2f} MB")
                    print(f"     Change:   {improvement['change_pct']:.1f}%")

        # Print summary
        print(f"\n\n{'=' * 60}")
        print("SUMMARY")
        print(f"{'=' * 60}")
        print(f"Regressions: {len(self.regressions)}")
        print(f"Improvements: {len(self.improvements)}")

        if self.regressions:
            print("\n❌ PERFORMANCE REGRESSION DETECTED")
        elif self.improvements:
            print("\n✅ ALL CHECKS PASSED (with improvements)")
        else:
            print("\n✅ ALL CHECKS PASSED (no significant changes)")

        print(f"{'=' * 60}\n")


def load_metrics(path: Path) -> dict[str, Any]:
    """Load metrics from JSON file.

    Args:
        path: Path to metrics JSON file

    Returns:
        Loaded metrics dictionary

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If file is not valid JSON
    """
    if not path.exists():
        raise FileNotFoundError(f"Metrics file not found: {path}")

    with path.open("r") as f:
        data = json.load(f)
        assert isinstance(data, dict), "Metrics must be a dictionary"
        return data


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Detect performance regressions")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("baseline_metrics.json"),
        help="Path to baseline metrics JSON file",
    )
    parser.add_argument(
        "--current",
        type=Path,
        default=Path("current_metrics.json"),
        help="Path to current metrics JSON file",
    )
    args = parser.parse_args()

    try:
        # Load metrics
        baseline = load_metrics(args.baseline)
        current = load_metrics(args.current)

        # Detect regressions
        detector = RegressionDetector(baseline, current)
        success = detector.run()

        # Print report
        detector.print_report()

        # Return appropriate exit code
        if success:
            return EXIT_SUCCESS
        else:
            return EXIT_REGRESSION

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in metrics file: {e}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
