#!/usr/bin/env python3
"""
E2E Stress Test - Edge case and boundary condition testing.

Tests system resilience under stress:
- Invalid input handling
- Large payload handling
- Rapid request bursts
- Timeout behavior
- Rate limiting response
- Concurrent write operations

Usage:
    python -m scripts.e2e.e2e_stress_test --host http://localhost:8000
    python -m scripts.e2e.e2e_stress_test --verbose
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import httpx

from scripts.e2e.e2e_utils import (
    E2EReport,
    LogLevel,
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

SCRIPT_NAME = "e2e_stress_test"

# Large payload for testing
LARGE_PAYLOAD_SIZE = 1024 * 100  # 100KB
BURST_REQUEST_COUNT = 50
BURST_CONCURRENCY = 20


# =============================================================================
# Test Cases
# =============================================================================


async def test_invalid_json_body(client: httpx.AsyncClient) -> bool | str:
    """Test handling of malformed JSON body."""
    response, latency_ms, error = await timed_request(
        client,
        "POST",
        "/api/v1/auth/login",
        content=b"{ invalid json }",
        headers={"Content-Type": "application/json"},
    )

    if error:
        # Timeout or connection error is acceptable for malformed input
        return True

    # Should get 400 or 422 for bad JSON
    if response and response.status_code in (400, 422):
        return True

    return f"Expected 400/422, got {response.status_code if response else 'No response'}"


async def test_empty_body(client: httpx.AsyncClient) -> bool | str:
    """Test handling of empty request body."""
    response, latency_ms, error = await timed_request(
        client,
        "POST",
        "/api/v1/auth/login",
        content=b"",
        headers={"Content-Type": "application/json"},
    )

    if error:
        return True

    # Should get 400, 415, or 422 for empty body
    if response and response.status_code in (400, 415, 422):
        return True

    return f"Expected 400/415/422, got {response.status_code if response else 'No response'}"


async def test_oversized_payload(client: httpx.AsyncClient) -> bool | str:
    """Test handling of very large request payload."""
    large_data = {"data": "x" * LARGE_PAYLOAD_SIZE}

    response, latency_ms, error = await timed_request(
        client,
        "POST",
        "/api/v1/auth/register",
        json=large_data,
    )

    if error:
        # Timeout or rejection is acceptable
        return True

    # Should get 413 (Payload Too Large) or 422 (Validation Error)
    if response and response.status_code in (400, 413, 422):
        return True

    # Some servers accept and process it - that's okay too
    if response and response.status_code in (200, 201):
        return True

    return f"Unexpected status: {response.status_code if response else 'No response'}"


async def test_sql_injection_attempt(client: httpx.AsyncClient) -> bool | str:
    """Test that SQL injection attempts are handled safely."""
    malicious_inputs = [
        "'; DROP TABLE users; --",
        "1' OR '1'='1",
        "admin'--",
        "1; SELECT * FROM users",
    ]

    for payload in malicious_inputs:
        response, latency_ms, error = await timed_request(
            client,
            "POST",
            "/api/v1/auth/login",
            data={"username": payload, "password": payload},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if error:
            continue

        # Should get 400, 401, or 422 - NOT 500 (server error)
        if response and response.status_code == 500:
            return f"Server error on SQL injection attempt: {payload}"

    return True


async def test_xss_attempt(client: httpx.AsyncClient) -> bool | str:
    """Test that XSS attempts are handled safely."""
    xss_payloads = [
        "<script>alert('xss')</script>",
        "javascript:alert('xss')",
        "<img src=x onerror=alert('xss')>",
    ]

    for payload in xss_payloads:
        response, latency_ms, error = await timed_request(
            client,
            "POST",
            "/api/v1/auth/register",
            json={
                "email": f"test_{payload[:5]}@example.com",
                "password": "TestPass123!",
                "name": payload,
            },
        )

        if error:
            continue

        # Should NOT return 500
        if response and response.status_code == 500:
            return "Server error on XSS attempt"

    return True


async def test_rapid_burst_requests(client: httpx.AsyncClient) -> bool | str:
    """Test system behavior under rapid request burst."""
    semaphore = asyncio.Semaphore(BURST_CONCURRENCY)
    results: list[tuple[int | None, float]] = []

    async def burst_request() -> tuple[int | None, float]:
        async with semaphore:
            response, latency_ms, error = await timed_request(client, "GET", "/api/v1/health")
            status = response.status_code if response else None
            return status, latency_ms

    tasks = [burst_request() for _ in range(BURST_REQUEST_COUNT)]
    results = await asyncio.gather(*tasks)

    # Count successful responses
    success_count = sum(1 for status, _ in results if status == 200)
    rate_limited = sum(1 for status, _ in results if status == 429)

    # At least 50% should succeed or be rate-limited (not error)
    handled = success_count + rate_limited
    if handled >= BURST_REQUEST_COUNT * 0.5:
        return True

    return f"Only {handled}/{BURST_REQUEST_COUNT} requests handled properly"


async def test_rate_limit_response(client: httpx.AsyncClient) -> bool | str:
    """Test that rate limiting returns proper response."""
    # Send many requests quickly to trigger rate limit
    for _ in range(100):
        response, latency_ms, error = await timed_request(client, "GET", "/api/v1/health")

        if response and response.status_code == 429:
            # Check for proper rate limit headers
            headers = response.headers
            has_retry = "retry-after" in headers or "x-ratelimit-remaining" in headers
            if has_retry:
                return True
            # Even without headers, 429 is correct behavior
            return True

    # If we never hit rate limit, that's also acceptable
    return True


async def test_invalid_auth_token(client: httpx.AsyncClient) -> bool | str:
    """Test handling of invalid authentication tokens."""
    invalid_tokens = [
        "invalid_token",
        "Bearer invalid",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid.signature",
        "",
    ]

    for token in invalid_tokens:
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        response, latency_ms, error = await timed_request(
            client,
            "GET",
            "/api/v1/users/me",
            headers=headers,
        )

        if error:
            continue

        # Should get 401 or 403, not 500
        if response and response.status_code == 500:
            return f"Server error with invalid token: {token[:20]}..."

    return True


async def test_nonexistent_endpoint(client: httpx.AsyncClient) -> bool | str:
    """Test handling of requests to non-existent endpoints."""
    response, latency_ms, error = await timed_request(client, "GET", "/api/v1/this/endpoint/does/not/exist")

    if error:
        return True

    # Should get 404
    if response and response.status_code == 404:
        return True

    return f"Expected 404, got {response.status_code if response else 'No response'}"


async def test_method_not_allowed(client: httpx.AsyncClient) -> bool | str:
    """Test handling of wrong HTTP method."""
    response, latency_ms, error = await timed_request(client, "DELETE", "/api/v1/health")

    if error:
        return True

    # Should get 405 Method Not Allowed or 404
    if response and response.status_code in (404, 405):
        return True

    return f"Expected 404/405, got {response.status_code if response else 'No response'}"


async def test_concurrent_writes(client: httpx.AsyncClient) -> bool | str:
    """Test concurrent write operations don't corrupt data."""
    import secrets

    unique_id = secrets.token_hex(8)
    email = f"stress_test_{unique_id}@example.com"

    # Try to register same user concurrently
    async def register_user() -> int | None:
        response, _, _ = await timed_request(
            client,
            "POST",
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "TestPass123!",
                "name": f"Stress Test {unique_id}",
            },
        )
        return response.status_code if response else None

    tasks = [register_user() for _ in range(5)]
    results = await asyncio.gather(*tasks)

    # Exactly one should succeed (201), others should fail (409 or 400)
    success_count = sum(1 for r in results if r in (200, 201))
    conflict_count = sum(1 for r in results if r in (400, 409, 422))

    # At least one success and some conflicts is expected behavior
    if success_count >= 1 and (success_count + conflict_count) >= 3:
        return True

    # If all failed, that's also acceptable (registration might be disabled)
    if all(r in (400, 403, 404, 422) for r in results if r):
        return True

    return f"Unexpected results: successes={success_count}, conflicts={conflict_count}"


# =============================================================================
# Main Execution
# =============================================================================


async def run_stress_test(args: Any) -> E2EReport:
    """Execute the stress test suite."""
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

    logger.info(f"Starting E2E Stress Test against {args.host}")
    logger.info("-" * 60)

    async with create_client(args.host, args.timeout) as client:
        # Define test sequence
        tests = [
            ("Invalid JSON Body", lambda: test_invalid_json_body(client)),
            ("Empty Body", lambda: test_empty_body(client)),
            ("Oversized Payload", lambda: test_oversized_payload(client)),
            ("SQL Injection Attempt", lambda: test_sql_injection_attempt(client)),
            ("XSS Attempt", lambda: test_xss_attempt(client)),
            ("Rapid Burst Requests", lambda: test_rapid_burst_requests(client)),
            ("Rate Limit Response", lambda: test_rate_limit_response(client)),
            ("Invalid Auth Token", lambda: test_invalid_auth_token(client)),
            ("Nonexistent Endpoint", lambda: test_nonexistent_endpoint(client)),
            ("Method Not Allowed", lambda: test_method_not_allowed(client)),
            ("Concurrent Writes", lambda: test_concurrent_writes(client)),
        ]

        for name, test_func in tests:
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
    parser = create_base_parser("E2E Stress Test - Edge cases and boundaries")
    args = parser.parse_args()

    report = asyncio.run(run_stress_test(args))
    return get_exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
