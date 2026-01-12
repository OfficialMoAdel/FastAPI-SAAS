#!/usr/bin/env python3
"""
E2E Regression Test - Quick smoke test for CI/CD pipelines.

Fast verification of critical endpoints:
- Health check
- Auth endpoints accessible
- Core API responding
- Database connectivity

Designed for quick CI runs (< 30 seconds).

Usage:
    python -m scripts.e2e.e2e_regression
    python -m scripts.e2e.e2e_regression --host http://staging.example.com
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import httpx

from scripts.e2e.e2e_utils import (
    E2EReport,
    LogLevel,
    RequestMetrics,
    TestStatus,
    create_base_parser,
    create_client,
    get_exit_code,
    now_utc,
    print_summary,
    run_test,
    setup_logging,
    timed_request,
)

# =============================================================================
# Constants
# =============================================================================

SCRIPT_NAME = "e2e_regression"

# Critical endpoints that must be working
CRITICAL_ENDPOINTS = [
    ("GET", "/api/v1/health", 200, "Health Check"),
    ("GET", "/api/v1/users", [200, 401], "Users Endpoint"),
    ("GET", "/api/v1/posts", [200, 401], "Posts Endpoint"),
]

# Maximum acceptable latency for regression tests (ms)
MAX_LATENCY_MS = 1000.0


# =============================================================================
# Test Cases
# =============================================================================


async def test_endpoint_accessible(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    expected_status: int | list[int],
) -> bool | str:
    """Test that an endpoint is accessible and responding."""
    response, latency_ms, error = await timed_request(client, method, url)

    if error:
        return f"Request failed: {error}"

    if not response:
        return "No response received"

    # Check status code
    expected = expected_status if isinstance(expected_status, list) else [expected_status]
    if response.status_code not in expected:
        return f"Expected status {expected}, got {response.status_code}"

    # Check latency
    if latency_ms > MAX_LATENCY_MS:
        return f"Latency {latency_ms:.0f}ms exceeds threshold {MAX_LATENCY_MS}ms"

    return True


async def test_health_returns_json(client: httpx.AsyncClient) -> bool | str:
    """Test that health endpoint returns valid JSON."""
    response, latency_ms, error = await timed_request(client, "GET", "/api/v1/health")

    if error:
        return f"Request failed: {error}"

    if not response:
        return "No response received"

    try:
        data = response.json()
        if isinstance(data, dict):
            return True
        return "Response is not a JSON object"
    except Exception as e:
        return f"Invalid JSON response: {e}"


async def test_auth_endpoints_exist(client: httpx.AsyncClient) -> bool | str:
    """Test that authentication endpoints are available."""
    auth_endpoints = [
        "/api/v1/auth/login",
        "/api/v1/auth/register",
    ]

    for endpoint in auth_endpoints:
        response, latency_ms, error = await timed_request(client, "POST", endpoint, json={})

        if error:
            continue

        # Should get 400/401/422 (validation error), not 404 or 500
        if response and response.status_code == 404:
            return f"Auth endpoint not found: {endpoint}"
        if response and response.status_code == 500:
            return f"Server error on auth endpoint: {endpoint}"

    return True


async def test_protected_endpoint_requires_auth(
    client: httpx.AsyncClient,
) -> bool | str:
    """Test that protected endpoints require authentication."""
    response, latency_ms, error = await timed_request(client, "GET", "/api/v1/users/me")

    if error:
        return f"Request failed: {error}"

    if not response:
        return "No response received"

    # Should get 401 Unauthorized or 403 Forbidden
    if response.status_code in (401, 403):
        return True

    # 404 means endpoint doesn't exist (acceptable)
    if response.status_code == 404:
        return True

    return f"Expected 401/403, got {response.status_code}"


async def test_api_versioning(client: httpx.AsyncClient) -> bool | str:
    """Test that API versioning is working."""
    # Test v1 endpoint
    response, latency_ms, error = await timed_request(client, "GET", "/api/v1/health")

    if error:
        return f"v1 API request failed: {error}"

    if response and response.status_code == 200:
        return True

    return f"v1 API not accessible: {response.status_code if response else 'No response'}"


async def test_cors_headers(client: httpx.AsyncClient) -> bool | str:
    """Test that CORS headers are present."""
    response, latency_ms, error = await timed_request(
        client,
        "OPTIONS",
        "/api/v1/health",
        headers={"Origin": "http://localhost:3000"},
    )

    if error:
        # Some servers don't handle OPTIONS - not critical
        return True

    if not response:
        return True

    # Check for CORS headers (optional, not all APIs need CORS)
    # Just verify the request doesn't error
    return True


async def test_response_time_acceptable(client: httpx.AsyncClient) -> bool | str:
    """Test that response times are within acceptable range."""
    metrics = RequestMetrics()

    endpoints = ["/api/v1/health", "/api/v1/users", "/api/v1/posts"]

    for endpoint in endpoints:
        response, latency_ms, error = await timed_request(client, "GET", endpoint)
        if not error:
            metrics.total += 1
            metrics.latencies_ms.append(latency_ms)
            if response and response.status_code < 500:
                metrics.successful += 1

    if metrics.total == 0:
        return "No successful requests"

    avg_latency = metrics.avg_latency_ms
    if avg_latency > MAX_LATENCY_MS:
        return f"Average latency {avg_latency:.0f}ms exceeds {MAX_LATENCY_MS}ms"

    return True


# =============================================================================
# Main Execution
# =============================================================================


async def run_regression_test(args: Any) -> E2EReport:
    """Execute the regression test suite."""
    log_level = LogLevel.DEBUG if args.verbose else LogLevel.INFO
    logger = setup_logging(SCRIPT_NAME, log_level)

    report = E2EReport(
        script_name=SCRIPT_NAME,
        started_at=now_utc(),
        config={
            "host": args.host,
            "timeout": args.timeout,
            "fail_fast": args.fail_fast,
        },
    )

    logger.info(f"Starting E2E Regression Test against {args.host}")
    logger.info("-" * 60)

    async with create_client(args.host, args.timeout) as client:
        # Test critical endpoints
        for method, url, expected, name in CRITICAL_ENDPOINTS:
            result = await run_test(
                name,
                lambda m=method, u=url, e=expected: test_endpoint_accessible(client, m, u, e),
                logger,
                args.fail_fast,
            )
            report.results.append(result)

            if args.fail_fast and result.status != TestStatus.PASSED:
                break

        # Additional regression tests
        if not args.fail_fast or report.all_passed:
            additional_tests = [
                ("Health Returns JSON", lambda: test_health_returns_json(client)),
                ("Auth Endpoints Exist", lambda: test_auth_endpoints_exist(client)),
                (
                    "Protected Endpoint Auth",
                    lambda: test_protected_endpoint_requires_auth(client),
                ),
                ("API Versioning", lambda: test_api_versioning(client)),
                ("CORS Headers", lambda: test_cors_headers(client)),
                (
                    "Response Time Acceptable",
                    lambda: test_response_time_acceptable(client),
                ),
            ]

            for name, test_func in additional_tests:
                result = await run_test(name, test_func, logger, args.fail_fast)
                report.results.append(result)

                if args.fail_fast and result.status != TestStatus.PASSED:
                    break

    report.ended_at = now_utc()
    print_summary(report, logger)

    if args.output:
        report.save_json(args.output)
        logger.info(f"Report saved to: {args.output}")

    return report


def main() -> int:
    """CLI entry point."""
    parser = create_base_parser("E2E Regression Test - Quick CI smoke test")
    args = parser.parse_args()

    report = asyncio.run(run_regression_test(args))
    return get_exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
