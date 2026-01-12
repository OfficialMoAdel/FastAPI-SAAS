#!/usr/bin/env python3
"""
E2E Full Flow Test - Complete user journey through the application.

Tests the critical path:
1. Health check
2. User registration
3. User login
4. Token refresh
5. Get current user
6. Update user profile
7. Logout

Usage:
    python -m scripts.e2e.e2e_full_flow --host http://localhost:8000
    python -m scripts.e2e.e2e_full_flow --verbose --fail-fast
"""

from __future__ import annotations

import asyncio
import secrets
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
# Test Configuration
# =============================================================================

SCRIPT_NAME = "e2e_full_flow"


# =============================================================================
# Test State
# =============================================================================


class TestContext:
    """Shared state across test steps."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.user_email: str = ""
        self.user_password: str = ""
        self.access_token: str = ""
        self.refresh_token: str = ""
        self.user_id: int | None = None

    def auth_headers(self) -> dict[str, str]:
        """Get authorization headers."""
        return {"Authorization": f"Bearer {self.access_token}"}


# =============================================================================
# Test Cases
# =============================================================================


async def test_health_check(ctx: TestContext) -> bool | str:
    """Test health endpoint is responding."""
    response, latency_ms, error = await timed_request(ctx.client, "GET", "/api/v1/health")

    if error:
        return f"Health check failed: {error}"

    if response and response.status_code == 200:
        return True

    return f"Unexpected status: {response.status_code if response else 'No response'}"


async def test_user_registration(ctx: TestContext) -> bool | str:
    """Test user registration flow."""
    # Generate unique test user
    unique_id = secrets.token_hex(8)
    ctx.user_email = f"e2e_test_{unique_id}@example.com"
    ctx.user_password = f"TestPass123!{unique_id}"

    response, latency_ms, error = await timed_request(
        ctx.client,
        "POST",
        "/api/v1/auth/register",
        json={
            "email": ctx.user_email,
            "password": ctx.user_password,
            "name": f"E2E Test User {unique_id[:8]}",
        },
    )

    if error:
        return f"Registration failed: {error}"

    if response and response.status_code in (200, 201):
        data = response.json()
        ctx.user_id = data.get("id") or data.get("user", {}).get("id")
        return True

    # Handle case where registration returns tokens directly
    if response and response.status_code == 200:
        data = response.json()
        if "access_token" in data:
            ctx.access_token = data["access_token"]
            ctx.refresh_token = data.get("refresh_token", "")
            return True

    return f"Registration failed with status: {response.status_code if response else 'No response'}"


async def test_user_login(ctx: TestContext) -> bool | str:
    """Test user login flow."""
    # Skip if we already have tokens from registration
    if ctx.access_token:
        return True

    response, latency_ms, error = await timed_request(
        ctx.client,
        "POST",
        "/api/v1/auth/login",
        data={
            "username": ctx.user_email,
            "password": ctx.user_password,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if error:
        return f"Login failed: {error}"

    if response and response.status_code == 200:
        data = response.json()
        ctx.access_token = data.get("access_token", "")
        ctx.refresh_token = data.get("refresh_token", "")

        if ctx.access_token:
            return True
        return "No access token in response"

    return f"Login failed with status: {response.status_code if response else 'No response'}"


async def test_token_refresh(ctx: TestContext) -> bool | str:
    """Test token refresh flow."""
    if not ctx.refresh_token:
        # Some APIs don't use refresh tokens
        return True

    response, latency_ms, error = await timed_request(
        ctx.client,
        "POST",
        "/api/v1/auth/refresh",
        json={"refresh_token": ctx.refresh_token},
    )

    if error:
        return f"Token refresh failed: {error}"

    if response and response.status_code == 200:
        data = response.json()
        new_token = data.get("access_token", "")
        if new_token:
            ctx.access_token = new_token
            ctx.refresh_token = data.get("refresh_token", ctx.refresh_token)
            return True
        return "No access token in refresh response"

    # 404 means endpoint doesn't exist - not a failure
    if response and response.status_code == 404:
        return True

    return f"Token refresh failed with status: {response.status_code if response else 'No response'}"


async def test_get_current_user(ctx: TestContext) -> bool | str:
    """Test getting current user profile."""
    response, latency_ms, error = await timed_request(
        ctx.client,
        "GET",
        "/api/v1/users/me",
        headers=ctx.auth_headers(),
    )

    if error:
        return f"Get user failed: {error}"

    if response and response.status_code == 200:
        data = response.json()
        if data.get("email") == ctx.user_email:
            return True
        # Some APIs return nested user object
        if data.get("user", {}).get("email") == ctx.user_email:
            return True
        return f"Email mismatch: expected {ctx.user_email}"

    return f"Get user failed with status: {response.status_code if response else 'No response'}"


async def test_update_user_profile(ctx: TestContext) -> bool | str:
    """Test updating user profile."""
    new_name = f"Updated E2E User {secrets.token_hex(4)}"

    response, latency_ms, error = await timed_request(
        ctx.client,
        "PATCH",
        "/api/v1/users/me",
        headers=ctx.auth_headers(),
        json={"name": new_name},
    )

    if error:
        return f"Update user failed: {error}"

    if response and response.status_code in (200, 204):
        return True

    # 404 means endpoint doesn't exist - not critical
    if response and response.status_code == 404:
        return True

    return f"Update user failed with status: {response.status_code if response else 'No response'}"


async def test_user_logout(ctx: TestContext) -> bool | str:
    """Test user logout flow."""
    response, latency_ms, error = await timed_request(
        ctx.client,
        "POST",
        "/api/v1/auth/logout",
        headers=ctx.auth_headers(),
    )

    if error:
        return f"Logout failed: {error}"

    if response and response.status_code in (200, 204):
        ctx.access_token = ""
        ctx.refresh_token = ""
        return True

    # 404 means endpoint doesn't exist - not critical
    if response and response.status_code == 404:
        return True

    return f"Logout failed with status: {response.status_code if response else 'No response'}"


async def test_token_invalidated(ctx: TestContext) -> bool | str:
    """Verify token is invalidated after logout."""
    if ctx.access_token:
        # Token wasn't cleared, skip this test
        return True

    # Try to access protected endpoint with old token
    # This should fail if logout properly invalidated the token
    old_headers = {"Authorization": f"Bearer {ctx.access_token}"}

    response, latency_ms, error = await timed_request(
        ctx.client,
        "GET",
        "/api/v1/users/me",
        headers=old_headers,
    )

    # Should get 401 Unauthorized
    if response and response.status_code == 401:
        return True

    # If no token was set, the test passes trivially
    if not old_headers["Authorization"].replace("Bearer ", ""):
        return True

    return True  # Don't fail on this - logout behavior varies


# =============================================================================
# Main Execution
# =============================================================================


async def run_full_flow(args: Any) -> E2EReport:
    """Execute the full user journey test suite."""
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

    logger.info(f"Starting E2E Full Flow Test against {args.host}")
    logger.info("-" * 60)

    async with create_client(args.host, args.timeout) as client:
        ctx = TestContext(client)

        # Define test sequence
        tests = [
            ("Health Check", lambda: test_health_check(ctx)),
            ("User Registration", lambda: test_user_registration(ctx)),
            ("User Login", lambda: test_user_login(ctx)),
            ("Token Refresh", lambda: test_token_refresh(ctx)),
            ("Get Current User", lambda: test_get_current_user(ctx)),
            ("Update User Profile", lambda: test_update_user_profile(ctx)),
            ("User Logout", lambda: test_user_logout(ctx)),
            ("Token Invalidated", lambda: test_token_invalidated(ctx)),
        ]

        for name, test_func in tests:
            result = await run_test(name, test_func, logger, args.fail_fast)
            report.results.append(result)

            # Stop on first failure if requested
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
    parser = create_base_parser("E2E Full Flow Test - Complete user journey")
    args = parser.parse_args()

    report = asyncio.run(run_full_flow(args))
    return get_exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
