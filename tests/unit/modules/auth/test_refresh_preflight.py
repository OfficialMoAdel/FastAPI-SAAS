"""Focused coverage for the refresh-path preflight and rotation-marker reuse.

The preflight is optional: any Redis/Lua/shape failure must fall back to the
pre-existing sequential ``_reject_reused_refresh_token`` plus ``verify_token``
path. The Redis happy path fuses the marker, blacklist and token-version
reads, but malformed markers keep the documented "warn and continue to
verification" shape.
"""

from __future__ import annotations

import json
import secrets
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security import jwt_service
from src.app.core.security.token_blacklist_cache import BLACKLIST_KEY
from src.app.core.utils.cache.keys import build_tenant_key

SUBJECT = "refresh-preflight-probe"
AUD = "fastapi-saas-api"
ISS = "fastapi-saas"
TENANT_ID = "test-tenant-e3"
OLD_JTI = "jti-old"

MARKER = {
    "user_id": 7,
    "sid": "sess-1",
    "new_jti": "jti-new",
    "new_exp": 4102444800.0,
}


def _fresh_key() -> str:
    return secrets.token_hex(32)


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": SUBJECT,
        "aud": AUD,
        "iss": ISS,
        "token_type": "refresh",
        "tier_id": 1,
        "tenant_id": TENANT_ID,
        "jti": OLD_JTI,
        "sid": "sid-old",
        "token_version": 1,
        "iat": now,
        "exp": now + 3600,
        "absolute_exp": time.time() + 3600,
    }
    claims.update(overrides)
    return claims


def _version_key() -> str:
    return build_tenant_key(f"user:{SUBJECT.lower().strip()}:token_version", TENANT_ID)


def _marker_key(jti: str = OLD_JTI) -> str:
    return f"refresh_rotated:{jti}"


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


@pytest.fixture
def service():
    from src.app.modules.auth.service import AuthService

    return AuthService.__new__(AuthService)


def _fake_issued(access_token: str = "new-access") -> tuple[MagicMock, dict[str, str | int]]:
    expected = {"access_token": access_token, "token_type": "bearer", "expires_in": 1800}
    issued = MagicMock()
    issued.access_response.return_value = expected
    return issued, expected


def _user_dict() -> dict:
    return {
        "id": 7,
        "username": SUBJECT,
        "email": "refresh-probe@example.com",
        "is_active": True,
        "is_deleted": False,
        "tenant_id": TENANT_ID,
        "tier_id": 1,
        "tier": {"name": "pro"},
        "token_version": 1,
    }


def _mint(key: str, **overrides) -> str:
    import jwt

    return jwt.encode(_claims(**overrides), key, algorithm="HS256")


def _patch_issuance(monkeypatch) -> MagicMock:
    from src.app.modules.auth.services.tokens import TokenIssuer

    issued, _expected = _fake_issued()
    rotate = AsyncMock(return_value=issued)
    monkeypatch.setattr(TokenIssuer, "rotate_session", rotate)
    monkeypatch.setattr(
        "src.app.modules.auth.service.AuthService._resolve_user_auth_info",
        AsyncMock(return_value=_user_dict()),
    )
    return rotate


@pytest.mark.asyncio
async def test_refresh_uses_the_fused_preflight_when_it_succeeds(mock_redis, lookups, service, monkeypatch) -> None:
    key = _fresh_key()
    token = _mint(key, jti=OLD_JTI, token_version=1)
    mock_redis.store[_version_key()] = "1"
    version_lookup = AsyncMock(return_value=2)
    jwt_service.register_token_version_lookup(version_lookup)
    sequential_reject = AsyncMock(side_effect=AssertionError("sequential path must not run"))
    rotate = _patch_issuance(monkeypatch)
    monkeypatch.setattr(service, "_reject_reused_refresh_token", sequential_reject)

    with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=key):
        result = await service.refresh_access_token(token, response=None, db=_db_double())

    assert result["access_token"] == "new-access"
    rotate.assert_awaited_once()
    version_lookup.assert_not_awaited()
    sequential_reject.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_consumes_the_reuse_marker_before_verifying(mock_redis, lookups, service, monkeypatch) -> None:
    key = _fresh_key()
    token = _mint(key, jti=OLD_JTI, token_version=1)
    mock_redis.store[_version_key()] = "1"
    mock_redis.store[_marker_key()] = json.dumps(MARKER)
    preflighted = AsyncMock(side_effect=AssertionError("verification must not run"))
    invalidate = AsyncMock(return_value=True)
    blacklist = AsyncMock(return_value=True)
    audit = AsyncMock()
    _patch_issuance(monkeypatch)
    monkeypatch.setattr("src.app.modules.auth.service.verify_refresh_token_preflighted", preflighted)
    monkeypatch.setattr("src.app.core.security.session_manager.session_manager.invalidate_session", invalidate)
    monkeypatch.setattr("src.app.core.security.token_blacklist_cache.add_token_to_blacklist", blacklist)
    monkeypatch.setattr("src.app.modules.auth.service.audit", audit)

    with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=key):
        with pytest.raises(Exception, match="Invalid refresh token"):
            await service.refresh_access_token(token, response=None, db=_db_double())

    preflighted.assert_not_awaited()
    invalidate.assert_awaited_once_with(7, "sess-1")
    assert "jti-new" in str(blacklist.await_args)
    assert audit.await_args is not None
    assert audit.await_args.kwargs["action"] == "auth.refresh_token_reuse_detected"


@pytest.mark.asyncio
async def test_refresh_blacklisted_token_consumes_reuse_marker(mock_redis, lookups, service, monkeypatch) -> None:
    key = _fresh_key()
    token = _mint(key, jti=OLD_JTI, token_version=1)
    now = time.time()
    mock_redis.store[BLACKLIST_KEY] = {f"jti:{OLD_JTI}": now + 60}
    mock_redis.store[_version_key()] = "not-an-integer"
    mock_redis.store[_marker_key()] = json.dumps(MARKER)
    preflighted = AsyncMock(side_effect=AssertionError("verification must not run"))
    invalidate = AsyncMock(return_value=True)
    blacklist = AsyncMock(return_value=True)
    audit = AsyncMock()
    _patch_issuance(monkeypatch)
    monkeypatch.setattr("src.app.modules.auth.service.verify_refresh_token_preflighted", preflighted)
    monkeypatch.setattr("src.app.core.security.session_manager.session_manager.invalidate_session", invalidate)
    monkeypatch.setattr("src.app.core.security.token_blacklist_cache.add_token_to_blacklist", blacklist)
    monkeypatch.setattr("src.app.modules.auth.service.audit", audit)

    with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=key):
        with pytest.raises(Exception, match="Invalid refresh token"):
            await service.refresh_access_token(token, response=None, db=_db_double())

    preflighted.assert_not_awaited()
    invalidate.assert_awaited_once_with(7, "sess-1")
    assert "jti-new" in str(blacklist.await_args)


@pytest.mark.asyncio
async def test_refresh_rejects_a_mismatched_fused_token_version(mock_redis, lookups, service, monkeypatch) -> None:
    key = _fresh_key()
    token = _mint(key, jti=OLD_JTI, token_version=1)
    mock_redis.store[_version_key()] = "2"
    version_lookup = AsyncMock(return_value=1)
    jwt_service.register_token_version_lookup(version_lookup)
    rotate = _patch_issuance(monkeypatch)

    with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=key):
        with pytest.raises(Exception, match="Invalid refresh token"):
            await service.refresh_access_token(token, response=None, db=_db_double())

    rotate.assert_not_awaited()
    version_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_missing_version_falls_back_to_singleflight(mock_redis, lookups, service, monkeypatch) -> None:
    key = _fresh_key()
    token = _mint(key, jti=OLD_JTI, token_version=1)
    version_lookup = AsyncMock(return_value=1)
    jwt_service.register_token_version_lookup(version_lookup)
    rotate = _patch_issuance(monkeypatch)

    with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=key):
        result = await service.refresh_access_token(token, response=None, db=_db_double())

    assert result["access_token"] == "new-access"
    rotate.assert_awaited_once()
    version_lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_preflight_error_falls_back_to_sequential(mock_redis, lookups, service, monkeypatch) -> None:
    key = _fresh_key()
    token = _mint(key, jti=OLD_JTI, token_version=1)
    user_data = MagicMock()
    user_data.absolute_exp = None
    user_data.username_or_email = SUBJECT
    user_data.raw_payload = _claims()
    verify_token = AsyncMock(return_value=user_data)
    reject_reused = AsyncMock()
    rotate = _patch_issuance(monkeypatch)
    monkeypatch.setattr(
        "src.app.modules.auth.service.check_refresh_preflight", AsyncMock(side_effect=RuntimeError("lua down"))
    )
    monkeypatch.setattr(service, "_reject_reused_refresh_token", reject_reused)
    monkeypatch.setattr("src.app.modules.auth.service.verify_token", verify_token)

    with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=key):
        result = await service.refresh_access_token(token, response=None, db=_db_double())

    assert result["access_token"] == "new-access"
    reject_reused.assert_awaited_once()
    verify_token.assert_awaited_once()
    rotate.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_preflight_none_falls_back_to_sequential(mock_redis, lookups, service, monkeypatch) -> None:
    key = _fresh_key()
    token = _mint(key, jti=OLD_JTI, token_version=1)
    user_data = MagicMock()
    user_data.absolute_exp = None
    user_data.username_or_email = SUBJECT
    user_data.raw_payload = _claims()
    verify_token = AsyncMock(return_value=user_data)
    reject_reused = AsyncMock()
    _patch_issuance(monkeypatch)
    monkeypatch.setattr("src.app.modules.auth.service.check_refresh_preflight", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_reject_reused_refresh_token", reject_reused)
    monkeypatch.setattr("src.app.modules.auth.service.verify_token", verify_token)

    with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=key):
        await service.refresh_access_token(token, response=None, db=_db_double())

    reject_reused.assert_awaited_once()
    verify_token.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_malformed_marker_still_reaches_verification(mock_redis, lookups, service, monkeypatch) -> None:
    key = _fresh_key()
    token = _mint(key, jti=OLD_JTI, token_version=1)
    mock_redis.store[_version_key()] = "1"
    mock_redis.store[_marker_key()] = b"not json"
    version_lookup = AsyncMock(return_value=2)
    jwt_service.register_token_version_lookup(version_lookup)
    rotate = _patch_issuance(monkeypatch)

    with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=key):
        result = await service.refresh_access_token(token, response=None, db=_db_double())

    # A corrupt marker is not a reuse proof. Verification proceeds with the
    # prefetched version, which matches in this setup, so rotation happens.
    assert result["access_token"] == "new-access"
    rotate.assert_awaited_once()
    version_lookup.assert_not_awaited()
