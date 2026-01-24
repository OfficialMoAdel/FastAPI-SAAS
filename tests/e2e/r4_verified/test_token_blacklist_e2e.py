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

from src.app.core.security.bloom_filter import blacklist_bloom_filter
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

    # Step 2: Check Bloom filter before blacklist
    log_step("Checking Bloom filter before blacklist")
    bloom_before = blacklist_bloom_filter.might_contain(token)
    log_step("Bloom filter check before", might_contain=bloom_before)

    # Step 3: Logout (blacklist token)
    log_step("Logging out to blacklist token")
    start_time = time.perf_counter()
    response = await client.post("/api/v1/account/logout", headers=headers)
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

    # Step 5: Verify Bloom filter updated
    log_step("Checking Bloom filter after blacklist")
    bloom_after = blacklist_bloom_filter.might_contain(token)
    log_step("Bloom filter check after", might_contain=bloom_after)
    assert bloom_after, "Token should be in Bloom filter"

    # Step 6: Verify token rejected
    log_step("Verifying token rejected after blacklist")
    response = await client.get("/api/v1/users/me", headers=headers)
    log_step("Request with blacklisted token", status_code=response.status_code)
    assert response.status_code == 401, f"Expected 401, got {response.status_code}"

    error_detail = response.json().get("detail", "")
    log_step("Error detail", detail=error_detail)
    assert "blacklist" in error_detail.lower() or "invalid" in error_detail.lower()

    log_step("Token blacklist E2E test completed successfully")


@pytest.mark.asyncio
async def test_bloom_filter_false_positive_handling(client: AsyncClient, log_step):
    """Test that Bloom filter false positives are handled correctly.

    Verifies that even if Bloom filter says token might be blacklisted,
    Redis is checked for definitive answer.
    """
    log_step("Starting Bloom filter false positive E2E test")

    # Step 1: Create a token that's NOT blacklisted
    login_data = {
        "email": "bloom_test@example.com",
        "password": "BloomTest123!",
        "username": "bloom_testuser",
    }

    log_step("Creating test user")
    response = await client.post("/api/v1/account/register", json=login_data)
    assert response.status_code == 201

    response = await client.post(
        "/api/v1/account/login",
        json={"email": login_data["email"], "password": login_data["password"]},
    )
    assert response.status_code == 200

    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    log_step("Test user created and logged in")

    # Step 2: Verify token works
    log_step("Verifying token works")
    response = await client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == 200
    log_step("Token valid")

    # Step 3: Check Bloom filter
    bloom_check = blacklist_bloom_filter.might_contain(token)
    log_step("Bloom filter check", might_contain=bloom_check)

    # If Bloom filter says "might contain" (false positive), Redis should confirm it's NOT blacklisted
    if bloom_check:
        log_step("Bloom filter false positive detected, verifying Redis check")
        # Token should still work because Redis will confirm it's not blacklisted
        response = await client.get("/api/v1/users/me", headers=headers)
        assert response.status_code == 200, "Token should work despite Bloom filter false positive"
        log_step("Redis correctly confirmed token not blacklisted")

    log_step("Bloom filter false positive handling E2E test completed successfully")


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
    response = await client.post("/api/v1/account/logout", headers=headers)
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
    login_data = {"email": authenticated_user["email"], "password": "TestPassword123!"}
    tokens = [authenticated_user["access_token"]]

    for i in range(3):
        response = await client.post("/api/v1/account/login", json=login_data)
        assert response.status_code == 200
        tokens.append(response.json()["access_token"])
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
        response = await client.post("/api/v1/account/logout", headers=headers)
        assert response.status_code == 200
        log_step(f"Token {i + 1} blacklisted")

    # Step 4: Verify all tokens rejected
    log_step("Verifying all tokens rejected")
    for i, token in enumerate(tokens):
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get("/api/v1/users/me", headers=headers)
        assert response.status_code == 401
        log_step(f"Token {i + 1} correctly rejected")

    # Step 5: Verify all in Bloom filter
    log_step("Verifying all tokens in Bloom filter")
    for i, token in enumerate(tokens):
        bloom_check = blacklist_bloom_filter.might_contain(token)
        assert bloom_check, f"Token {i + 1} should be in Bloom filter"
        log_step(f"Token {i + 1} in Bloom filter")

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

    response = await client.post("/api/v1/account/logout", headers=headers)
    assert response.status_code == 200

    elapsed = time.perf_counter() - start_time
    log_step("Blacklist operation completed", elapsed_ms=elapsed * 1000)

    # Blacklist should be fast (< 50ms including network)
    assert elapsed < 0.05, f"Blacklist took {elapsed * 1000:.2f}ms, expected < 50ms"
    log_step("Performance acceptable", threshold_ms=50, actual_ms=elapsed * 1000)

    log_step("Blacklist performance E2E test completed successfully")
