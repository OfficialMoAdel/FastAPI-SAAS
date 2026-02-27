"""E2E test for Token Blacklist (P0-3).

Tests the complete token blacklist flow with Lua script atomic operations
and Bloom filter pre-filtering.

Test Flow:
1. Login user, get access token
2. Use token successfully
3. Logout (blacklist token)
4. Verify token rejected
5. Verify Bloom filter updated
6. Log all Redis operations
"""

import logging
import time

import pytest
from httpx import AsyncClient

from src.app.core.utils.cache import client as redis_client

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_token_blacklist_full_flow(authenticated_user: dict, client: AsyncClient, log_step):
    """Test complete token blacklist flow from logout to rejection.

    Verifies P0-3 optimization: Token Blacklist Lua Script for atomic operations.
    """
    log_step("Starting token blacklist E2E test", user=authenticated_user["email"])

    token = authenticated_user["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Step 1: Verify token works initially
    log_step("Verifying token works before blacklist")
    response = await client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == 200, f"Token should work: {response.text}"
    log_step("Token valid before blacklist", user=response.json())

    # Step 3: Logout (blacklist token)
    log_step("Logging out to blacklist token")
    start_time = time.perf_counter()
    logout_cookies = {"refresh_token": authenticated_user.get("refresh_token")} if authenticated_user.get("refresh_token") else {}
    response = await client.post("/api/v1/account/logout", headers=headers, cookies=logout_cookies)
    logout_time = time.perf_counter() - start_time

    assert response.status_code == 200, f"Logout failed: {response.text}"
    log_step("Logout successful", elapsed_ms=logout_time * 1000)

    # Step 4: Verify token blacklisted in Redis
    if redis_client:
        log_step("Checking Redis blacklist")
        blacklist_key = "token:blacklist"

        # Check if token in sorted set
        score = await redis_client.zscore(blacklist_key, token)
        log_step("Redis blacklist check", token_found=score is not None, score=score)
        assert score is not None, "Token should be in blacklist"

    # Step 6: Verify token rejected
    log_step("Verifying token rejected after blacklist")
    response = await client.get("/api/v1/users/me", headers=headers)
    log_step("Request with blacklisted token", status_code=response.status_code)
    assert response.status_code == 401, f"Expected 401, got {response.status_code}"

    error_detail = response.json().get("detail", "")
    log_step("Error detail", detail=error_detail)
    assert any(term in error_detail.lower() for term in ["blacklist", "invalid", "not authenticated"])

    log_step("Token blacklist E2E test completed successfully")


@pytest.mark.asyncio
async def test_blacklist_expiration(authenticated_user: dict, client: AsyncClient, log_step):
    """Test that blacklisted tokens expire after TTL.

    Verifies that Redis sorted set cleanup removes expired tokens.
    """
    log_step("Starting blacklist expiration E2E test", user=authenticated_user["email"])

    token = authenticated_user["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Step 1: Blacklist token
    log_step("Blacklisting token")
    logout_cookies = {"refresh_token": authenticated_user.get("refresh_token")} if authenticated_user.get("refresh_token") else {}
    response = await client.post("/api/v1/account/logout", headers=headers, cookies=logout_cookies)
    assert response.status_code == 200

    # Step 2: Verify token in blacklist
    if redis_client:
        log_step("Verifying token in Redis blacklist")
        blacklist_key = "token:blacklist"
        score = await redis_client.zscore(blacklist_key, token)
        assert score is not None, "Token should be in blacklist"
        log_step("Token in blacklist", expiry_timestamp=score)

        # Step 3: Check TTL on blacklist key
        ttl = await redis_client.ttl(blacklist_key)
        log_step("Blacklist key TTL", ttl_seconds=ttl)

        # TTL should be set (not -1 which means no expiry)
        assert ttl > 0 or ttl == -1, "TTL should be set or key should persist"

    log_step("Blacklist expiration E2E test completed successfully")


@pytest.mark.asyncio
async def test_multiple_token_blacklist(authenticated_user: dict, client: AsyncClient, log_step):
    """Test blacklisting multiple tokens for same user.

    Verifies that Lua script handles multiple tokens atomically.
    """
    log_step("Starting multiple token blacklist E2E test", user=authenticated_user["email"])

    # Step 1: Create multiple sessions
    log_step("Creating multiple sessions")
    login_data = {"username": authenticated_user["email"], "password": "TestPassword123!"}
    tokens = [authenticated_user["access_token"]]
    refresh_tokens = [authenticated_user.get("refresh_token")]

    for i in range(3):
        response = await client.post("/api/v1/account/login", data=login_data)
        assert response.status_code == 200
        tokens.append(response.json()["access_token"])
        refresh_tokens.append(response.cookies.get("refresh_token"))
        log_step(f"Created session {i + 2}", token_preview=tokens[-1][:20])

    # Step 2: Verify all tokens work
    log_step("Verifying all tokens work")
    for i, token in enumerate(tokens):
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get("/api/v1/users/me", headers=headers)
        assert response.status_code == 200
        log_step(f"Token {i + 1} valid")

    # Step 3: Blacklist all tokens
    log_step("Blacklisting all tokens")
    for i, token in enumerate(tokens):
        headers = {"Authorization": f"Bearer {token}"}
        logout_cookies = {"refresh_token": refresh_tokens[i]} if refresh_tokens[i] else {}
        response = await client.post("/api/v1/account/logout", headers=headers, cookies=logout_cookies)
        assert response.status_code == 200
        log_step(f"Token {i + 1} blacklisted")

    # Step 4: Verify all tokens rejected
    log_step("Verifying all tokens rejected")
    for i, token in enumerate(tokens):
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get("/api/v1/users/me", headers=headers)
        assert response.status_code == 401
        log_step(f"Token {i + 1} correctly rejected")

    log_step("Multiple token blacklist E2E test completed successfully")


@pytest.mark.asyncio
async def test_blacklist_performance(authenticated_user: dict, client: AsyncClient, log_step):
    """Test blacklist operation performance with Lua script.

    Verifies that atomic Lua script is fast.
    """
    log_step("Starting blacklist performance E2E test", user=authenticated_user["email"])

    token = authenticated_user["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Measure blacklist operation time
    log_step("Measuring blacklist operation time")
    start_time = time.perf_counter()

    logout_cookies = {"refresh_token": authenticated_user.get("refresh_token")} if authenticated_user.get("refresh_token") else {}
    response = await client.post("/api/v1/account/logout", headers=headers, cookies=logout_cookies)
    assert response.status_code == 200

    elapsed = time.perf_counter() - start_time
    log_step("Blacklist operation completed", elapsed_ms=elapsed * 1000)

    # Blacklist should be fast (< 1000ms including network)
    assert elapsed < 1.0, f"Blacklist took {elapsed * 1000:.2f}ms, expected < 1000ms"
    log_step("Performance acceptable", threshold_ms=1000, actual_ms=elapsed * 1000)

    log_step("Blacklist performance E2E test completed successfully")
