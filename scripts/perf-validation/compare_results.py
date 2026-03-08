#!/usr/bin/env python3
"""Compare before/after performance metrics with detailed analysis."""

import json
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

TARGETS = {
    "p95_latency_ms": {"direction": "decrease", "target_pct": 35},
    "p99_latency_ms": {"direction": "decrease", "target_pct": 50},
    "redis_roundtrips": {"direction": "decrease", "target_pct": 66},
    "db_queries_per_request": {"direction": "decrease", "target_pct": 80},
    "redis_memory_mb": {"direction": "decrease", "target_pct": 30},
    "gc_cpu_pct": {"direction": "decrease", "target_pct": 66},
    "connection_pool_utilization": {"direction": "decrease", "target_pct": 30},
}


def compare_with_baseline(baseline_path: str, current_results: dict[str, Any]) -> dict[str, Any]:
    """Compare current results with baseline."""
    try:
        baseline = json.loads(Path(baseline_path).read_text())
    except FileNotFoundError:
        logger.warning("baseline_not_found", path=baseline_path)
        return {"error": f"Baseline not found: {baseline_path}"}

    improvements = {}
    all_targets_met = True

    # Compare latency metrics
    for endpoint in ["auth_flow", "tenant_operations", "file_upload", "user_profile"]:
        baseline_latency = baseline.get("latency", {}).get(endpoint, {})
        current_latency = current_results.get("latency", {}).get(endpoint, {})

        for percentile in ["p50", "p95", "p99"]:
            before = baseline_latency.get(percentile)
            after = current_latency.get(percentile)

            if before and after:
                change_pct = ((before - after) / before) * 100
                metric_key = f"{endpoint}_{percentile}"

                target_pct = TARGETS.get(f"{percentile}_latency_ms", {}).get("target_pct", 0)
                meets_target = change_pct >= target_pct

                improvements[metric_key] = {
                    "before": before,
                    "after": after,
                    "change_pct": round(change_pct, 1),
                    "target_pct": target_pct,
                    "meets_target": meets_target,
                }

                if not meets_target and percentile in ["p95", "p99"]:
                    all_targets_met = False

                logger.info(
                    "metric_comparison",
                    metric=metric_key,
                    before=before,
                    after=after,
                    change_pct=round(change_pct, 1),
                    meets_target=meets_target,
                )

    # Compare Redis metrics
    for metric in ["roundtrips_per_user_cache", "memory_per_session_bytes"]:
        before = baseline.get("redis", {}).get(metric)
        after = current_results.get("redis", {}).get(metric)

        if before and after:
            change_pct = ((before - after) / before) * 100
            improvements[f"redis_{metric}"] = {
                "before": before,
                "after": after,
                "change_pct": round(change_pct, 1),
            }

    return {"improvements": improvements, "all_targets_met": all_targets_met}


def compare_files(baseline_path: str, current_path: str) -> dict[str, Any]:
    """Compare two result files."""
    try:
        json.loads(Path(baseline_path).read_text())
        current = json.loads(Path(current_path).read_text())
    except FileNotFoundError as e:
        return {"error": str(e)}

    return compare_with_baseline(baseline_path, current)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", help="Path to baseline JSON")
    parser.add_argument("current", help="Path to current results JSON")
    args = parser.parse_args()

    result = compare_files(args.baseline, args.current)
    print(json.dumps(result, indent=2))
