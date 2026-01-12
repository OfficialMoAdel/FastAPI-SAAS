#!/usr/bin/env python3
"""
E2E Load Test - Concurrent user simulation with configurable load.

Tests system behavior under load:
- Configurable 1-100 concurrent users
- Configurable duration 1-300 seconds
- Ramp-up period support
- Metrics: RPS, latency percentiles, error rates

Usage:
    python -m scripts.e2e.e2e_load_test --users 10 --duration 30
    python -m scripts.e2e.e2e_load_test --users 50 --duration 60 --ramp-up 10
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

import httpx

from scripts.e2e.e2e_utils import (
    E2EReport,
    LogLevel,
    RequestMetrics,
    TestResult,
    TestStatus,
    add_load_test_args,
    create_base_parser,
    create_client,
    get_exit_code,
    now_utc,
    print_summary,
    setup_logging,
    timed_request,
)

# =============================================================================
# Constants
# =============================================================================

SCRIPT_NAME = "e2e_load_test"

# Endpoints to test under load
LOAD_TEST_ENDPOINTS = [
    ("GET", "/api/v1/health", None),
    ("GET", "/api/v1/users", None),
    ("GET", "/api/v1/posts", None),
]

# Thresholds
MAX_USERS = 100
MAX_DURATION = 300
MIN_SUCCESS_RATE = 95.0  # Minimum 95% success rate
MAX_P95_LATENCY_MS = 500.0  # Max p95 latency


# =============================================================================
# Load Test Implementation
# =============================================================================


async def simulate_user(
    client: httpx.AsyncClient,
    user_id: int,
    duration_s: float,
    metrics: RequestMetrics,
    stop_event: asyncio.Event,
) -> None:
    """
    Simulate a single user making requests.

    Each user cycles through endpoints until duration expires or stop event.
    """
    start_time = time.time()
    endpoint_idx = 0

    while not stop_event.is_set():
        elapsed = time.time() - start_time
        if elapsed >= duration_s:
            break

        method, url, body = LOAD_TEST_ENDPOINTS[endpoint_idx]
        kwargs: dict[str, Any] = {}
        if body:
            kwargs["json"] = body

        response, latency_ms, error = await timed_request(client, method, url, **kwargs)

        metrics.total += 1
        metrics.latencies_ms.append(latency_ms)

        if response and 200 <= response.status_code < 400:
            metrics.successful += 1
        else:
            metrics.failed += 1

        # Cycle to next endpoint
        endpoint_idx = (endpoint_idx + 1) % len(LOAD_TEST_ENDPOINTS)

        # Small delay to prevent overwhelming
        await asyncio.sleep(0.1)


async def ramp_up_users(
    client: httpx.AsyncClient,
    total_users: int,
    ramp_up_s: int,
    duration_s: float,
    metrics: RequestMetrics,
    logger: Any,
) -> list[asyncio.Task[None]]:
    """
    Gradually ramp up users over the ramp-up period.

    Returns list of user tasks.
    """
    tasks: list[asyncio.Task[None]] = []
    stop_event = asyncio.Event()

    if ramp_up_s <= 0 or total_users <= 1:
        # No ramp-up, start all users immediately
        for i in range(total_users):
            task = asyncio.create_task(simulate_user(client, i, duration_s, metrics, stop_event))
            tasks.append(task)
        logger.info(f"Started {total_users} users immediately")
        return tasks

    # Calculate users per second during ramp-up
    users_per_interval = max(1, total_users // ramp_up_s)
    interval = ramp_up_s / (total_users / users_per_interval)

    user_id = 0
    while user_id < total_users:
        batch_size = min(users_per_interval, total_users - user_id)

        for _ in range(batch_size):
            task = asyncio.create_task(simulate_user(client, user_id, duration_s, metrics, stop_event))
            tasks.append(task)
            user_id += 1

        logger.info(f"Ramped up to {user_id}/{total_users} users")
        await asyncio.sleep(interval)

    return tasks


async def run_load_test_phase(
    client: httpx.AsyncClient,
    users: int,
    duration_s: int,
    ramp_up_s: int,
    logger: Any,
) -> RequestMetrics:
    """Execute the main load test phase."""
    metrics = RequestMetrics()

    logger.info(f"Starting load test: {users} users, {duration_s}s duration")

    # Start users with ramp-up
    tasks = await ramp_up_users(client, users, ramp_up_s, duration_s, metrics, logger)

    # Wait for all users to complete
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    return metrics


def evaluate_results(metrics: RequestMetrics, logger: Any) -> list[TestResult]:
    """Evaluate load test results against thresholds."""
    results: list[TestResult] = []

    # Test 1: Success Rate
    success_rate = metrics.success_rate
    if success_rate >= MIN_SUCCESS_RATE:
        results.append(
            TestResult(
                name="Success Rate Check",
                status=TestStatus.PASSED,
                duration_ms=0,
                message=f"Success rate {success_rate:.1f}% >= {MIN_SUCCESS_RATE}%",
                details={"success_rate": success_rate},
            )
        )
    else:
        results.append(
            TestResult(
                name="Success Rate Check",
                status=TestStatus.FAILED,
                duration_ms=0,
                message=f"Success rate {success_rate:.1f}% < {MIN_SUCCESS_RATE}%",
                details={"success_rate": success_rate},
            )
        )

    # Test 2: P95 Latency
    p95 = metrics.p95_latency_ms
    if p95 <= MAX_P95_LATENCY_MS:
        results.append(
            TestResult(
                name="P95 Latency Check",
                status=TestStatus.PASSED,
                duration_ms=0,
                message=f"P95 latency {p95:.1f}ms <= {MAX_P95_LATENCY_MS}ms",
                details={"p95_latency_ms": p95},
            )
        )
    else:
        results.append(
            TestResult(
                name="P95 Latency Check",
                status=TestStatus.FAILED,
                duration_ms=0,
                message=f"P95 latency {p95:.1f}ms > {MAX_P95_LATENCY_MS}ms",
                details={"p95_latency_ms": p95},
            )
        )

    # Test 3: Error Rate
    error_rate = 100 - success_rate
    max_error_rate = 5.0
    if error_rate <= max_error_rate:
        results.append(
            TestResult(
                name="Error Rate Check",
                status=TestStatus.PASSED,
                duration_ms=0,
                message=f"Error rate {error_rate:.1f}% <= {max_error_rate}%",
                details={"error_rate": error_rate},
            )
        )
    else:
        results.append(
            TestResult(
                name="Error Rate Check",
                status=TestStatus.FAILED,
                duration_ms=0,
                message=f"Error rate {error_rate:.1f}% > {max_error_rate}%",
                details={"error_rate": error_rate},
            )
        )

    return results


# =============================================================================
# Main Execution
# =============================================================================


async def run_load_test(args: Any) -> E2EReport:
    """Execute the load test suite."""
    log_level = LogLevel.DEBUG if args.verbose else LogLevel.INFO
    logger = setup_logging(SCRIPT_NAME, log_level)

    # Validate arguments
    users = min(args.users, MAX_USERS)
    duration = min(args.duration, MAX_DURATION)

    report = E2EReport(
        script_name=SCRIPT_NAME,
        started_at=now_utc(),
        config={
            "host": args.host,
            "users": users,
            "duration_s": duration,
            "ramp_up_s": args.ramp_up,
            "timeout": args.timeout,
        },
    )

    logger.info(f"Starting E2E Load Test against {args.host}")
    logger.info(f"Configuration: {users} users, {duration}s duration, {args.ramp_up}s ramp-up")
    logger.info("-" * 60)

    async with create_client(args.host, args.timeout) as client:
        # Run load test
        metrics = await run_load_test_phase(client, users, duration, args.ramp_up, logger)

        report.metrics = metrics

        # Evaluate results
        logger.info("-" * 60)
        logger.info("Evaluating results...")
        results = evaluate_results(metrics, logger)
        report.results.extend(results)

    report.ended_at = now_utc()
    print_summary(report, logger)

    if args.output:
        report.save_json(args.output)
        logger.info(f"Report saved to: {args.output}")

    return report


def main() -> int:
    """CLI entry point."""
    parser = create_base_parser("E2E Load Test - Concurrent user simulation")
    add_load_test_args(parser)
    args = parser.parse_args()

    report = asyncio.run(run_load_test(args))
    return get_exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
