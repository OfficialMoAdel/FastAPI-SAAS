"""Tests for src.app.core.security.__init__ covering password hashing/verification,
JWT creation, token verification (all branches), DB fallback, token blacklisting,
and the MFA pending token manager factory.

External dependencies (bcrypt, jwt, DB session, Redis cache) are mocked.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from sqlalchemy.exc import IntegrityError

from src.app.core.schemas import TokenData
from src.app.core.security import (
    TokenType,
    _check_db_fallback,
    _resolve_tenant_id_for_token,
    blacklist_token,
    blacklist_tokens,
    create_access_token,
    create_refresh_token,
    get_mfa_pending_token_manager,
    get_password_hash,
    is_token_revoked,
    verify_password,
    verify_token,
)


@pytest.fixture(autouse=True)
def _clear_security_caches() -> None:
    """Clear module-level caches that persist across tests."""
    from src.app.core.security.jwt_service import (
        _db_fallback_cache,
        _in_flight_db_checks,
    )

    _db_fallback_cache.clear()
    _in_flight_db_checks.clear()
    yield
    _db_fallback_cache.clear()
    _in_flight_db_checks.clear()


def _make_db() -> MagicMock:
    db = MagicMock()
    db.begin_nested = MagicMock()
    db.begin_nested.return_value.__aenter__ = AsyncMock()
    db.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Password hashing / verification
# ---------------------------------------------------------------------------


async def test_get_password_hash() -> None:
    with patch("src.app.core.security.password.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=b"$2b$hashed")
        result = await get_password_hash("secret")
    assert result == "$2b$hashed"


async def test_verify_password_true() -> None:
    with (
        patch("src.app.core.security.password.bcrypt") as mock_bcrypt,
        patch("src.app.core.security.password.asyncio.get_running_loop") as mock_loop,
    ):
        mock_bcrypt.checkpw.return_value = True
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=True)
        assert await verify_password("secret", "hashed") is True


async def test_verify_password_false() -> None:
    with (
        patch("src.app.core.security.password.bcrypt") as mock_bcrypt,
        patch("src.app.core.security.password.asyncio.get_running_loop") as mock_loop,
    ):
        mock_bcrypt.checkpw.return_value = False
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=False)
        assert await verify_password("secret", "hashed") is False


async def test_verify_password_malformed() -> None:
    with (
        patch("src.app.core.security.password.bcrypt") as mock_bcrypt,
        patch("src.app.core.security.password.asyncio.get_running_loop") as mock_loop,
    ):
        mock_bcrypt.checkpw.side_effect = ValueError("malformed hash")
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=ValueError("x"))
        assert await verify_password("secret", "bad") is False


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------


async def test_create_access_token_default_exp() -> None:
    with patch("src.app.core.security.jwt_service.jwt.encode", return_value="encoded.jwt.token"):
        token = await create_access_token({"sub": "user1"})
    assert token == "encoded.jwt.token"


async def test_create_access_token_custom_exp() -> None:
    with patch("src.app.core.security.jwt_service.jwt.encode", return_value="tok"):
        token = await create_access_token({"sub": "user1", "token_version": 3}, expires_delta=timedelta(minutes=5))
    assert token == "tok"


async def test_create_refresh_token_default() -> None:
    with patch("src.app.core.security.jwt_service.jwt.encode", return_value="rtok"):
        token = await create_refresh_token({"sub": "user1"})
    assert token == "rtok"


async def test_create_refresh_token_custom() -> None:
    with patch("src.app.core.security.jwt_service.jwt.encode", return_value="rtok2"):
        token = await create_refresh_token({"sub": "user1", "token_version": 1}, expires_delta=timedelta(days=2))
    assert token == "rtok2"


# ---------------------------------------------------------------------------
# _check_db_fallback
# ---------------------------------------------------------------------------


async def test_check_db_fallback_cache_hit() -> None:
    from src.app.core.security.jwt_service import _db_fallback_cache

    _db_fallback_cache["jti:abc"] = True
    try:
        result = await _check_db_fallback("abc", "tok", _make_db())
        assert result is True
    finally:
        _db_fallback_cache.pop("jti:abc", None)


async def test_check_db_fallback_in_flight() -> None:
    from src.app.core.security.jwt_service import _in_flight_db_checks

    task = asyncio.create_task(asyncio.sleep(0))  # placeholder
    _in_flight_db_checks["tok"] = task
    try:
        result = await _check_db_fallback(None, "tok", _make_db())
        # function returns await task (the completed sleep task's result = None)
        assert result is None
    finally:
        _in_flight_db_checks.pop("tok", None)


async def test_check_db_fallback_db_check() -> None:
    db = _make_db()
    with patch("src.app.core.security.jwt_service.crud_token_blacklist.exists", new=AsyncMock(return_value=True)):
        result = await _check_db_fallback("abc", "tok", db)
    assert result is True


async def test_check_db_fallback_exception() -> None:
    db = _make_db()
    with patch(
        "src.app.core.security.jwt_service.crud_token_blacklist.exists",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        result = await _check_db_fallback("abc", "tok", db)
    assert result is None


# ---------------------------------------------------------------------------
# is_token_revoked — the single revocation oracle
# ---------------------------------------------------------------------------


async def test_is_token_revoked_cache_true() -> None:
    with patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=True)):
        assert await is_token_revoked("tok", "abc", _make_db()) is True


async def test_is_token_revoked_cache_false() -> None:
    with patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=False)):
        assert await is_token_revoked("tok", "abc", _make_db()) is False


async def test_is_token_revoked_redis_down_db_hit() -> None:
    with (
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=None)),
        patch("src.app.core.security.jwt_service.crud_token_blacklist.exists", new=AsyncMock(return_value=True)),
    ):
        assert await is_token_revoked("tok", "abc", _make_db()) is True


async def test_is_token_revoked_redis_down_db_miss() -> None:
    with (
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=None)),
        patch("src.app.core.security.jwt_service.crud_token_blacklist.exists", new=AsyncMock(return_value=False)),
    ):
        assert await is_token_revoked("tok", "abc", _make_db()) is False


async def test_is_token_revoked_redis_down_db_error_fails_closed() -> None:
    """DB error during outage -> fail CLOSED (revoked), never accept a
    possibly-revoked token."""
    with (
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=None)),
        patch(
            "src.app.core.security.jwt_service.crud_token_blacklist.exists",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ),
    ):
        assert await is_token_revoked("tok", "abc", _make_db()) is True


# ---------------------------------------------------------------------------
# verify_token
# ---------------------------------------------------------------------------


async def test_verify_token_jwt_error() -> None:
    with patch(
        "src.app.core.security.jwt_service.jwt.decode",
        side_effect=jwt.exceptions.PyJWTError("bad"),
    ):
        assert await verify_token("tok", TokenType.ACCESS, _make_db()) is None


async def test_verify_token_missing_sub() -> None:
    payload = {"token_type": "access"}
    with patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload):
        assert await verify_token("tok", TokenType.ACCESS, _make_db()) is None


async def test_verify_token_wrong_type() -> None:
    payload = {"sub": "u", "token_type": "refresh"}
    with patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload):
        assert await verify_token("tok", TokenType.ACCESS, _make_db()) is None


async def test_verify_token_blacklisted_jti() -> None:
    payload = {"sub": "u", "token_type": "access", "jti": "abc"}
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=True)),
    ):
        assert await verify_token("tok", TokenType.ACCESS, _make_db()) is None


async def test_verify_token_blacklisted_full_token() -> None:
    payload = {"sub": "u", "token_type": "access"}  # no jti
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=True)),
    ):
        assert await verify_token("tok", TokenType.ACCESS, _make_db()) is None


async def test_verify_token_redis_unavailable_db_none() -> None:
    payload = {"sub": "u", "token_type": "access", "jti": "abc"}
    db = _make_db()
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=None)),
        patch("src.app.core.security.jwt_service._check_db_fallback", new=AsyncMock(return_value=None)),
    ):
        assert await verify_token("tok", TokenType.ACCESS, db) is None


async def test_verify_token_redis_unavailable_db_true() -> None:
    payload = {"sub": "u", "token_type": "access", "jti": "abc"}
    db = _make_db()
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=None)),
        patch("src.app.core.security.jwt_service._check_db_fallback", new=AsyncMock(return_value=True)),
    ):
        assert await verify_token("tok", TokenType.ACCESS, db) is None


async def test_verify_token_token_version_mismatch_cache() -> None:
    payload = {
        "sub": "u",
        "token_type": "access",
        "jti": "abc",
        "token_version": 5,
        "tenant_id": 1,
    }
    db = _make_db()
    redis_client = MagicMock()
    redis_client.get = AsyncMock(return_value=b"2")  # cached version 2 != 5
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=False)),
        patch("src.app.core.utils.cache.client", redis_client),
    ):
        assert await verify_token("tok", TokenType.ACCESS, db) is None


async def test_verify_token_token_version_db_mismatch() -> None:
    payload = {
        "sub": "u",
        "token_type": "access",
        "jti": "abc",
        "token_version": 5,
        "tenant_id": 1,
    }
    db = _make_db()
    redis_client = MagicMock()
    redis_client.get = AsyncMock(return_value=None)  # cache miss
    redis_client.set = AsyncMock(return_value=True)
    user = {"token_version": 2}
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=False)),
        patch("src.app.core.utils.cache.client", redis_client),
        patch("src.app.modules.users.crud.crud_users.get", new=AsyncMock(return_value=user)),
    ):
        assert await verify_token("tok", TokenType.ACCESS, db) is None


async def test_verify_token_token_version_db_match_caches() -> None:
    payload = {
        "sub": "u",
        "token_type": "access",
        "jti": "abc",
        "token_version": 5,
        "tenant_id": 1,
    }
    db = _make_db()
    redis_client = MagicMock()
    redis_client.get = AsyncMock(return_value=None)
    redis_client.set = AsyncMock(return_value=True)
    user = {"token_version": 5}
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=False)),
        patch("src.app.core.utils.cache.client", redis_client),
        patch("src.app.modules.users.crud.crud_users.get", new=AsyncMock(return_value=user)),
    ):
        result = await verify_token("tok", TokenType.ACCESS, db)
    assert isinstance(result, TokenData)
    assert result.token_version == 5


async def test_verify_token_token_version_cache_read_error() -> None:
    payload = {
        "sub": "u",
        "token_type": "access",
        "jti": "abc",
        "token_version": 5,
        "tenant_id": 1,
    }
    db = _make_db()
    redis_client = MagicMock()
    redis_client.get = AsyncMock(side_effect=RuntimeError("redis boom"))
    redis_client.set = AsyncMock(return_value=True)
    user = {"token_version": 5}
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=False)),
        patch("src.app.core.utils.cache.client", redis_client),
        patch("src.app.modules.users.crud.crud_users.get", new=AsyncMock(return_value=user)),
    ):
        result = await verify_token("tok", TokenType.ACCESS, db)
    assert isinstance(result, TokenData)


async def test_verify_token_absolute_exp_reached() -> None:
    payload = {
        "sub": "u",
        "token_type": "access",
        "jti": "abc",
        "token_version": 0,
        "tenant_id": 1,
        "absolute_exp": (datetime.now(UTC) - timedelta(days=1)).timestamp(),
    }
    db = _make_db()
    redis_client = MagicMock()
    redis_client.get = AsyncMock(return_value=None)
    user = {"token_version": 0}
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=False)),
        patch("src.app.core.utils.cache.client", redis_client),
        patch("src.app.modules.users.crud.crud_users.get", new=AsyncMock(return_value=user)),
    ):
        assert await verify_token("tok", TokenType.ACCESS, db) is None


async def test_verify_token_success_no_version() -> None:
    payload = {
        "sub": "u",
        "token_type": "access",
        "jti": "abc",
        "tier_id": 3,
    }
    db = _make_db()
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=False)),
    ):
        result = await verify_token("tok", TokenType.ACCESS, db)
    assert isinstance(result, TokenData)
    assert result.tier_id == 3


# ---------------------------------------------------------------------------
# _resolve_tenant_id_for_token
# ---------------------------------------------------------------------------


async def test_resolve_tenant_from_payload() -> None:
    db = _make_db()
    assert await _resolve_tenant_id_for_token({"tenant_id": 9}, db) == 9


async def test_resolve_tenant_from_context() -> None:
    db = _make_db()
    tenant = MagicMock()
    tenant.id = 7
    with patch("src.app.core.tenancy.context.get_current_tenant", return_value=tenant):
        assert await _resolve_tenant_id_for_token({}, db) == 7


async def test_resolve_tenant_from_db() -> None:
    db = _make_db()
    result = MagicMock()
    result.scalar = MagicMock(return_value=4)
    db.execute = AsyncMock(return_value=result)
    with patch("src.app.core.tenancy.context.get_current_tenant", return_value=None):
        assert await _resolve_tenant_id_for_token({"sub": "u"}, db) == 4


async def test_resolve_tenant_none() -> None:
    db = _make_db()
    with patch("src.app.core.tenancy.context.get_current_tenant", return_value=None):
        assert await _resolve_tenant_id_for_token({}, db) is None


# ---------------------------------------------------------------------------
# blacklist_tokens
# ---------------------------------------------------------------------------


async def test_blacklist_tokens_both() -> None:
    db = _make_db()
    payload = {
        "exp": datetime.now(UTC).timestamp() + 100,
        "jti": "j1",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
    }
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.add_tokens_to_blacklist", new=AsyncMock()),
    ):
        await blacklist_tokens("atok", "rtok", db, access_payload=payload, refresh_payload=payload)
    db.commit.assert_awaited()


async def test_blacklist_tokens_no_tokens() -> None:
    db = _make_db()
    await blacklist_tokens(None, None, db)
    db.commit.assert_not_awaited()


async def test_blacklist_tokens_resolve_tenant() -> None:
    db = _make_db()
    payload = {
        "exp": datetime.now(UTC).timestamp() + 100,
        "jti": "j1",
    }
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch(
            "src.app.core.security.jwt_service._resolve_tenant_id_for_token",
            new=AsyncMock(return_value="11111111-1111-1111-1111-111111111111"),
        ),
        patch("src.app.core.security.jwt_service.add_tokens_to_blacklist", new=AsyncMock()),
    ):
        await blacklist_tokens("atok", None, db, access_payload=payload)
    db.commit.assert_awaited()


async def test_blacklist_tokens_no_tenant_skip() -> None:
    db = _make_db()
    payload = {"exp": datetime.now(UTC).timestamp() + 100, "jti": "j1"}
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch(
            "src.app.core.security.jwt_service._resolve_tenant_id_for_token",
            new=AsyncMock(return_value=None),
        ),
    ):
        await blacklist_tokens("atok", None, db, access_payload=payload)
    db.commit.assert_not_awaited()


async def test_blacklist_tokens_integrity_error() -> None:
    db = _make_db()

    db.flush = AsyncMock(side_effect=IntegrityError("dup", None, None))
    payload = {
        "exp": datetime.now(UTC).timestamp() + 100,
        "jti": "j1",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
    }
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.add_tokens_to_blacklist", new=AsyncMock()),
    ):
        await blacklist_tokens("atok", None, db, access_payload=payload)
    db.commit.assert_awaited()


async def test_blacklist_tokens_commit_error() -> None:
    db = _make_db()
    db.commit = AsyncMock(side_effect=RuntimeError("commit boom"))
    payload = {
        "exp": datetime.now(UTC).timestamp() + 100,
        "jti": "j1",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
    }
    with patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload):
        await blacklist_tokens("atok", None, db, access_payload=payload)
    db.commit.assert_awaited()


async def test_blacklist_tokens_jwt_error() -> None:
    db = _make_db()
    with patch(
        "src.app.core.security.jwt_service.jwt.decode",
        side_effect=jwt.exceptions.PyJWTError("bad"),
    ):
        await blacklist_tokens("atok", None, db)
    db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# blacklist_token (single)
# ---------------------------------------------------------------------------


async def test_blacklist_token_success() -> None:
    db = _make_db()
    payload = {
        "exp": datetime.now(UTC).timestamp() + 100,
        "jti": "j1",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
    }
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.crud_token_blacklist.create", new=AsyncMock()),
        patch("src.app.core.security.jwt_service.add_token_to_blacklist", new=AsyncMock()),
    ):
        await blacklist_token("atok", db, payload=payload)


async def test_blacklist_token_no_payload() -> None:
    db = _make_db()
    payload = {
        "exp": datetime.now(UTC).timestamp() + 100,
        "jti": "j1",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
    }
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch("src.app.core.security.jwt_service.crud_token_blacklist.create", new=AsyncMock()),
        patch("src.app.core.security.jwt_service.add_token_to_blacklist", new=AsyncMock()),
    ):
        await blacklist_token("atok", db)


async def test_blacklist_token_no_exp() -> None:
    db = _make_db()
    payload = {"jti": "j1", "tenant_id": "11111111-1111-1111-1111-111111111111"}
    with patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload):
        await blacklist_token("atok", db, payload=payload)
    db.begin_nested.return_value.__aenter__.assert_not_awaited()


async def test_blacklist_token_resolve_tenant_raises() -> None:
    db = _make_db()
    payload = {"exp": datetime.now(UTC).timestamp() + 100, "jti": "j1"}
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch(
            "src.app.core.security.jwt_service._resolve_tenant_id_for_token",
            new=AsyncMock(return_value=None),
        ),
    ):
        # H3/H9 unify: missing tenant now warn+return (no ValueError) — consistent with blacklist_tokens
        await blacklist_token("atok", db, payload=payload)
    db.begin_nested.return_value.__aenter__.assert_not_awaited()


async def test_blacklist_token_integrity_error() -> None:
    db = _make_db()

    payload = {
        "exp": datetime.now(UTC).timestamp() + 100,
        "jti": "j1",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
    }
    with (
        patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
        patch(
            "src.app.core.security.jwt_service.crud_token_blacklist.create",
            new=AsyncMock(side_effect=IntegrityError("dup", None, None)),
        ),
    ):
        await blacklist_token("atok", db, payload=payload)


async def test_blacklist_token_jwt_error() -> None:
    db = _make_db()
    with patch(
        "src.app.core.security.jwt_service.jwt.decode",
        side_effect=jwt.exceptions.PyJWTError("bad"),
    ):
        await blacklist_token("atok", db)


# ---------------------------------------------------------------------------
# get_mfa_pending_token_manager
# ---------------------------------------------------------------------------


def test_get_mfa_pending_token_manager() -> None:
    redis = MagicMock()
    manager = get_mfa_pending_token_manager(redis)
    assert manager is not None
    assert manager.prefix == "mfa_pending"
