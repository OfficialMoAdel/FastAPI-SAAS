"""Focused coverage for the fused token blacklist and token_version read.

The fused Lua path is deliberately optional: a caller must fall back to the
original sequential revocation and version-read seams when Redis cannot prove
either leg. These tests keep those two shapes separate.
"""

from __future__ import annotations

import secrets
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security import jwt_service
from src.app.core.security.token_blacklist_cache import (
    BLACKLIST_KEY,
    check_blacklist_and_token_version,
)
from src.app.core.utils.cache.keys import build_tenant_key

SUBJECT = "blacklist-version-probe"
AUD = "fastapi-saas-api"
ISS = "fastapi-saas"
TENANT_ID = "test-tenant-e1"


def _fresh_key() -> str:
    return secrets.token_hex(32)


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": SUBJECT,
        "aud": AUD,
        "iss": ISS,
        "token_type": "access",
        "tier_id": 1,
        "tenant_id": TENANT_ID,
        "jti": secrets.token_hex(16),
        "sid": secrets.token_hex(16),
        "token_version": 0,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return claims


def _version_key() -> str:
    return build_tenant_key(f"user:{SUBJECT.lower().strip()}:token_version", TENANT_ID)


def _db_double() -> MagicMock:
    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


@pytest.fixture
def lookups():
    saved_version = jwt_service._token_version_lookup
    saved_tenant = jwt_service._user_tenant_lookup
    try:
        yield
    finally:
        jwt_service._token_version_lookup = saved_version
        jwt_service._user_tenant_lookup = saved_tenant


@pytest.mark.asyncio
async def test_helper_returns_not_blacklisted_without_version(mock_redis) -> None:
    assert await check_blacklist_and_token_version("token", "jti-missing", _version_key()) == (False, None)


@pytest.mark.asyncio
async def test_helper_returns_cached_version_on_blacklist_miss(mock_redis) -> None:
    mock_redis.store[_version_key()] = "7"

    assert await check_blacklist_and_token_version("token", "jti-missing", _version_key()) == (False, "7")


@pytest.mark.asyncio
async def test_helper_checks_blacklist_before_version(mock_redis) -> None:
    mock_redis.store[BLACKLIST_KEY] = {"jti:jti-blacklisted": time.time() + 60}
    mock_redis.store[_version_key()] = "not-an-integer"

    result = await check_blacklist_and_token_version("token", "jti-blacklisted", _version_key())

    assert result == (True, None)


@pytest.mark.asyncio
async def test_helper_returns_none_when_redis_is_unavailable() -> None:
    with patch(
        "src.app.core.security.token_blacklist_cache._get_available_redis_client_for_blacklist",
        MagicMock(return_value=(None, False)),
    ):
        assert await check_blacklist_and_token_version("token", "jti", _version_key()) is None


@pytest.mark.asyncio
async def test_helper_returns_none_for_a_malformed_script_result(mock_redis) -> None:
    manager = MagicMock()
    manager.execute = AsyncMock(return_value=["maybe", "7"])

    with patch("src.app.core.security.token_blacklist_cache.get_lua_manager", return_value=manager):
        assert await check_blacklist_and_token_version("token", "jti", _version_key()) is None


@pytest.mark.asyncio
async def test_verify_token_rejects_blacklisted_token_before_version_lookup(mock_redis, lookups) -> None:
    import jwt

    key = _fresh_key()
    token = jwt.encode(
        _claims(jti="jti-blacklisted", token_version=7),
        key,
        algorithm="HS256",
    )
    mock_redis.store[BLACKLIST_KEY] = {"jti:jti-blacklisted": time.time() + 60}
    version_lookup = AsyncMock(return_value=7)
    jwt_service.register_token_version_lookup(version_lookup)
    jwt_service.register_user_tenant_lookup(AsyncMock(return_value=TENANT_ID))

    with patch(
        "src.app.core.security.jwt_utils._get_parsed_verification_key",
        return_value=key,
    ):
        assert await jwt_service.verify_token(token, jwt_service.TokenType.ACCESS, _db_double()) is None

    version_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_token_accepts_a_matching_fused_cached_version(mock_redis, lookups) -> None:
    import jwt

    key = _fresh_key()
    token = jwt.encode(_claims(token_version=7), key, algorithm="HS256")
    mock_redis.store[_version_key()] = "7"
    version_lookup = AsyncMock(return_value=8)
    jwt_service.register_token_version_lookup(version_lookup)
    jwt_service.register_user_tenant_lookup(AsyncMock(return_value=TENANT_ID))

    with patch(
        "src.app.core.security.jwt_utils._get_parsed_verification_key",
        return_value=key,
    ):
        data = await jwt_service.verify_token(token, jwt_service.TokenType.ACCESS, _db_double())

    assert data is not None
    assert data.token_version == 7
    version_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_token_rejects_a_mismatched_fused_cached_version(mock_redis, lookups) -> None:
    import jwt

    key = _fresh_key()
    token = jwt.encode(_claims(token_version=7), key, algorithm="HS256")
    mock_redis.store[_version_key()] = "8"
    version_lookup = AsyncMock(return_value=7)
    jwt_service.register_token_version_lookup(version_lookup)
    jwt_service.register_user_tenant_lookup(AsyncMock(return_value=TENANT_ID))

    with patch(
        "src.app.core.security.jwt_utils._get_parsed_verification_key",
        return_value=key,
    ):
        assert await jwt_service.verify_token(token, jwt_service.TokenType.ACCESS, _db_double()) is None

    version_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_token_falls_back_to_singleflight_for_missing_fused_version(mock_redis, lookups) -> None:
    import jwt

    key = _fresh_key()
    token = jwt.encode(_claims(token_version=7), key, algorithm="HS256")
    version_lookup = AsyncMock(return_value=7)
    jwt_service.register_token_version_lookup(version_lookup)
    jwt_service.register_user_tenant_lookup(AsyncMock(return_value=TENANT_ID))

    with patch(
        "src.app.core.security.jwt_utils._get_parsed_verification_key",
        return_value=key,
    ):
        data = await jwt_service.verify_token(token, jwt_service.TokenType.ACCESS, _db_double())

    assert data is not None
    version_lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_token_falls_back_to_sequential_revocation_and_version_reads(mock_redis, lookups) -> None:
    import jwt

    key = _fresh_key()
    token = jwt.encode(_claims(token_version=7), key, algorithm="HS256")
    revoked = AsyncMock(return_value=False)
    cache_client = MagicMock()
    cache_client.get_strict = AsyncMock(return_value="7")
    jwt_service.register_token_version_lookup(AsyncMock(return_value=7))
    jwt_service.register_user_tenant_lookup(AsyncMock(return_value=TENANT_ID))

    fused = AsyncMock(side_effect=RuntimeError("redis down"))
    with (
        patch("src.app.core.security.jwt_service.check_blacklist_and_token_version", fused),
        patch("src.app.core.security.jwt_service.is_token_revoked", revoked),
        patch("src.app.core.cache.client.cache_client", cache_client),
        patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=key),
    ):
        data = await jwt_service.verify_token(token, jwt_service.TokenType.ACCESS, _db_double())

    assert data is not None
    revoked.assert_awaited_once()
    cache_client.get_strict.assert_awaited_once_with(_version_key())


@pytest.mark.asyncio
async def test_verify_token_fallback_still_fails_closed_for_revocation(mock_redis, lookups) -> None:
    import jwt

    key = _fresh_key()
    token = jwt.encode(_claims(token_version=7), key, algorithm="HS256")
    revoked = AsyncMock(return_value=True)
    cache_client = MagicMock()
    cache_client.get_strict = AsyncMock(return_value="7")
    jwt_service.register_token_version_lookup(AsyncMock(return_value=7))
    jwt_service.register_user_tenant_lookup(AsyncMock(return_value=TENANT_ID))

    with (
        patch("src.app.core.security.jwt_service.check_blacklist_and_token_version", AsyncMock(return_value=None)),
        patch("src.app.core.security.jwt_service.is_token_revoked", revoked),
        patch("src.app.core.cache.client.cache_client", cache_client),
        patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=key),
    ):
        assert await jwt_service.verify_token(token, jwt_service.TokenType.ACCESS, _db_double()) is None

    revoked.assert_awaited_once()
    cache_client.get_strict.assert_not_awaited()
