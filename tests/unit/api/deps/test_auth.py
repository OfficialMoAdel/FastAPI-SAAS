"""Tests for src.app.api.deps.auth covering auth context resolution, token
verification, API key auth, optional auth, superuser checks, and scope gating.

External dependencies (UserService, verify_token, JWT cache, tenant context,
API key service) are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import Request

from src.app.api.deps.auth import (
    AuthContext,
    AuthenticatedUser,
    _create_auth_context,
    _is_token_blacklisted_with_fallback,
    _verify_optional_bearer_token,
    api_key_scope_allows,
    enforce_api_key_scope,
    get_auth_context,
    get_current_superuser,
    get_current_user,
    get_optional_auth_context,
    get_optional_user,
    get_user_by_api_key,
    resolve_principal,
)
from src.app.core.schemas import TokenData
from src.app.core.tenancy.context import TenantContext
from src.app.shared.exceptions import ForbiddenError as ForbiddenException
from src.app.shared.exceptions import UnauthorizedError as UnauthorizedException


def _make_request(headers: dict[str, str] | None = None, state: Any = None) -> MagicMock:
    request = MagicMock(spec=Request)
    request.headers = headers or {}
    request.method = "GET"
    request.url.path = "/"
    request.app = MagicMock()
    request.app.dependency_overrides = {}
    request.state = state if state is not None else MagicMock()
    return request


def _make_db() -> MagicMock:
    return MagicMock()


@pytest.fixture(autouse=True)
def _clear_security_caches():
    """Clear jwt_service DB-fallback caches that persist across tests."""
    from src.app.core.security.jwt_service import _db_fallback_cache, _in_flight_db_checks

    _db_fallback_cache.clear()
    _in_flight_db_checks.clear()
    yield
    _db_fallback_cache.clear()
    _in_flight_db_checks.clear()


async def test_authenticated_user_attr_access() -> None:
    from src.app.core.utils.user_utils import get_user_attr

    u = AuthenticatedUser(id=1, email="a@b.com", username="a")
    assert u.email == "a@b.com"
    assert get_user_attr(u, "email") == "a@b.com"
    assert get_user_attr(u, "missing", "x") == "x"


async def test_create_auth_context_from_dict() -> None:
    user = {"id": 1, "email": "a@b.com", "username": "a"}
    ctx = await _create_auth_context(user, {"sub": "a"}, None, _make_db())
    assert isinstance(ctx.user, AuthenticatedUser)
    assert ctx.user.id == 1
    assert ctx.token_data == {"sub": "a"}


async def test_create_auth_context_from_obj() -> None:
    user = AuthenticatedUser(id=2, email="c@d.com")
    ctx = await _create_auth_context(user, None, None, _make_db())
    assert ctx.user.id == 2
    assert ctx.tenant is None


async def test_is_token_blacklisted_cache_true() -> None:
    with patch(
        "src.app.core.security.jwt_service.is_token_blacklisted",
        new=AsyncMock(return_value=True),
    ):
        assert await _is_token_blacklisted_with_fallback("tok", "jti", _make_db()) is True


async def test_is_token_blacklisted_cache_false_db_hit() -> None:
    db = _make_db()
    with (
        patch(
            "src.app.core.security.jwt_service.is_token_blacklisted",
            new=AsyncMock(return_value=None),
        ),
        patch("src.app.core.security.jwt_service.crud_token_blacklist") as mock_crud,
    ):
        mock_crud.exists = AsyncMock(return_value=True)
        assert await _is_token_blacklisted_with_fallback("tok", "jti", db) is True


async def test_is_token_blacklisted_cache_none_db_miss() -> None:
    db = _make_db()
    with (
        patch(
            "src.app.core.security.jwt_service.is_token_blacklisted",
            new=AsyncMock(return_value=None),
        ),
        patch("src.app.core.security.jwt_service.crud_token_blacklist") as mock_crud,
    ):
        mock_crud.exists = AsyncMock(return_value=False)
        assert await _is_token_blacklisted_with_fallback("tok", "jti", db) is False


# ---------------------------------------------------------------------------
# get_auth_context
# ---------------------------------------------------------------------------


async def test_get_auth_context_no_token_from_header() -> None:
    request = _make_request(headers={"Authorization": "Bearer abc"})
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, _make_db(), "")


async def test_get_auth_context_cached_revoked() -> None:
    request = _make_request()
    request.state.is_token_blacklisted = True
    with patch(
        "src.app.api.deps.auth.get_cached_jwt_payload",
        return_value={"jti": "j1", "token_type": "access", "sub": "u"},
    ):
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, _make_db(), "tok")


async def test_get_auth_context_cached_wrong_type() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    with patch(
        "src.app.api.deps.auth.get_cached_jwt_payload",
        return_value={"jti": "j1", "token_type": "refresh", "sub": "u"},
    ):
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, _make_db(), "tok")


async def test_get_auth_context_cached_no_sub() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    with patch(
        "src.app.api.deps.auth.get_cached_jwt_payload",
        return_value={"jti": "j1", "token_type": "access"},
    ):
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, _make_db(), "tok")


async def test_get_auth_context_cached_success() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {
        "jti": "j1",
        "token_type": "access",
        "sub": "user@example.com",
        "token_version": 2,
    }
    user = {"id": 1, "email": "user@example.com", "is_active": True, "token_version": 2, "tenant_id": 5}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch(
            "src.app.api.deps.auth._is_token_blacklisted_with_fallback",
            new=AsyncMock(return_value=False),
        ),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        ctx = await get_auth_context(request, _make_db(), "tok")
    assert ctx.user.id == 1


async def test_get_auth_context_verify_token_success() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "token_version": 0, "tenant_id": 1}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        ctx = await get_auth_context(request, _make_db(), "tok")
    assert ctx.user.id == 2


async def test_get_auth_context_user_not_found() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=None)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, _make_db(), "tok")


async def test_get_auth_context_inactive() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": False, "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, _make_db(), "tok")


async def test_get_auth_context_deleted() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "is_deleted": True, "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, _make_db(), "tok")


async def test_get_auth_context_version_mismatch() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=5)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "token_version": 2}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, _make_db(), "tok")


async def test_get_auth_context_tenant_mismatch() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "token_version": 0, "tenant_id": 1}
    tenant = TenantContext(tenant_id=UUID(int=9), tenant_slug="other", tenant_name="Other", is_active=True)
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=tenant),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        with pytest.raises(ForbiddenException):
            await get_auth_context(request, _make_db(), "tok")


async def test_get_auth_context_superuser_tenant_ok() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {
        "id": 2,
        "email": "bob@x.com",
        "is_active": True,
        "is_superuser": True,
        "token_version": 0,
        "tenant_id": 1,
    }
    tenant = TenantContext(tenant_id=UUID(int=9), tenant_slug="other", tenant_name="Other", is_active=True)
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=tenant),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        ctx = await get_auth_context(request, _make_db(), "tok")
    assert ctx.user.id == 2


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------


async def test_get_current_user_from_context() -> None:
    request = _make_request()
    ctx = AuthContext(user=AuthenticatedUser(id=1, email="a@b.com"), token_data=None, tenant=None)
    result = await get_current_user(request, "tok", _make_db(), ctx)
    assert result.id == 1


async def test_get_current_user_rebuild() -> None:
    request = _make_request(headers={"Authorization": "Bearer abc"})
    with patch("src.app.api.deps.auth.get_auth_context", new=AsyncMock()) as mock_gac:
        user = AuthenticatedUser(id=3, email="c@d.com")
        mock_gac.return_value = AuthContext(user=user, token_data=None, tenant=None)
        result = await get_current_user(request, "", _make_db(), auth_context=None)
    assert result.id == 3


# ---------------------------------------------------------------------------
# get_user_by_api_key
# ---------------------------------------------------------------------------


async def test_get_user_by_api_key_none() -> None:
    request = _make_request()
    result = await get_user_by_api_key(None, _make_db(), request)
    assert result is None


async def test_get_user_by_api_key_invalid() -> None:
    request = _make_request()
    with patch("src.app.modules.api_keys.service.APIKeyService") as mock_svc_cls:
        svc = mock_svc_cls.return_value
        svc.validate_api_key = AsyncMock(return_value=None)
        result = await get_user_by_api_key("key", _make_db(), request)
    assert result is None


async def test_get_user_by_api_key_tenant_mismatch() -> None:
    request = _make_request()
    key_info = {"user_id": 1, "tenant_id": 5, "scopes": []}
    with (
        patch("src.app.modules.api_keys.service.APIKeyService") as mock_svc_cls,
        patch("src.app.core.tenancy.context.get_current_tenant", return_value=MagicMock(tenant_id=9)),
    ):
        svc = mock_svc_cls.return_value
        svc.validate_api_key = AsyncMock(return_value=key_info)
        with pytest.raises(UnauthorizedException):
            await get_user_by_api_key("key", _make_db(), request)


async def test_get_user_by_api_key_user_not_found() -> None:
    request = _make_request()
    key_info = {"user_id": 1, "tenant_id": 5, "scopes": []}
    with (
        patch("src.app.modules.api_keys.service.APIKeyService") as mock_svc_cls,
        patch("src.app.core.tenancy.context.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_svc_cls.return_value
        svc.validate_api_key = AsyncMock(return_value=key_info)
        us = mock_us.return_value
        us.get = AsyncMock(return_value=None)
        result = await get_user_by_api_key("key", _make_db(), request)
    assert result is None


async def test_get_user_by_api_key_dict_user() -> None:
    request = _make_request()
    key_info = {"user_id": 1, "tenant_id": 5, "scopes": ["read"], "key_id": "k1"}
    user_data = {"id": 1, "username": "a", "email": "a@b.com", "is_active": True}
    with (
        patch("src.app.modules.api_keys.service.APIKeyService") as mock_svc_cls,
        patch("src.app.core.tenancy.context.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_svc_cls.return_value
        svc.validate_api_key = AsyncMock(return_value=key_info)
        us = mock_us.return_value
        us.get = AsyncMock(return_value=user_data)
        result = await get_user_by_api_key("key", _make_db(), request)
    assert result is not None
    assert result.id == 1


async def test_get_user_by_api_key_obj_user() -> None:
    request = _make_request()
    key_info = {"user_id": 1, "tenant_id": 5, "scopes": [], "key_id": "k1"}
    user_obj = MagicMock()
    user_obj.id = 1
    user_obj.username = "a"
    user_obj.email = "a@b.com"
    user_obj.tenant_id = 5
    user_obj.is_active = True
    user_obj.is_superuser = False
    user_obj.token_version = 0
    with (
        patch("src.app.modules.api_keys.service.APIKeyService") as mock_svc_cls,
        patch("src.app.core.tenancy.context.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_svc_cls.return_value
        svc.validate_api_key = AsyncMock(return_value=key_info)
        us = mock_us.return_value
        us.get = AsyncMock(return_value=user_obj)
        result = await get_user_by_api_key("key", _make_db(), request)
    assert result is not None
    assert result.id == 1


async def test_get_user_by_api_key_inactive() -> None:
    request = _make_request()
    key_info = {"user_id": 1, "tenant_id": 5, "scopes": [], "key_id": "k1"}
    user_data = {"id": 1, "username": "a", "email": "a@b.com", "is_active": False}
    with (
        patch("src.app.modules.api_keys.service.APIKeyService") as mock_svc_cls,
        patch("src.app.core.tenancy.context.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_svc_cls.return_value
        svc.validate_api_key = AsyncMock(return_value=key_info)
        us = mock_us.return_value
        us.get = AsyncMock(return_value=user_data)
        result = await get_user_by_api_key("key", _make_db(), request)
    assert result is None


async def test_is_token_blacklisted_token_check() -> None:
    db = _make_db()
    with (
        patch(
            "src.app.core.security.jwt_service.is_token_blacklisted",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.app.core.security.jwt_service.crud_token_blacklist.exists",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await _is_token_blacklisted_with_fallback("tok", None, db)
    assert result is True


async def test_get_auth_context_no_token_no_header() -> None:
    request = _make_request(headers={})
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, _make_db(), "")


async def test_get_current_user_no_token_no_header() -> None:
    request = _make_request(headers={})
    with patch("src.app.api.deps.auth.get_auth_context", new=AsyncMock()) as mock_gac:
        user = AuthenticatedUser(id=4, email="c@d.com")
        mock_gac.return_value = AuthContext(user=user, token_data=None, tenant=None)
        result = await get_current_user(request, "", _make_db(), auth_context=None)
    assert result.id == 4


async def test_get_auth_context_cached_email_user() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {
        "jti": "j1",
        "token_type": "access",
        "sub": "user@example.com",
        "token_version": 2,
    }
    user = {"id": 1, "email": "user@example.com", "is_active": True, "token_version": 2, "tenant_id": 5}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        ctx = await get_auth_context(request, _make_db(), "tok")
    assert ctx.user.id == 1


async def test_get_auth_context_cached_no_user() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=None)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, _make_db(), "tok")


async def test_get_auth_context_cached_deleted() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 0}
    user = {"id": 2, "email": "u@x.com", "is_active": True, "is_deleted": True, "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, _make_db(), "tok")


async def test_get_auth_context_cached_version_mismatch() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 5}
    user = {"id": 2, "email": "u@x.com", "is_active": True, "token_version": 2}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, _make_db(), "tok")


async def test_get_optional_auth_context_exception() -> None:
    request = _make_request(headers={"Authorization": "Bearer abc"})
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch(
            "src.app.api.deps.auth.verify_token",
            new=AsyncMock(side_effect=ValueError("boom")),
        ),
    ):
        result = await get_optional_auth_context(request, _make_db())
    assert result is None


async def test_get_optional_auth_context_user_lookup_exception() -> None:
    request = _make_request(headers={"Authorization": "Bearer abc"})
    token_data = TokenData(username_or_email="bob", token_version=0)
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(side_effect=RuntimeError("db down"))
        result = await get_optional_auth_context(request, _make_db())
    assert result is None


# ---------------------------------------------------------------------------
# _verify_optional_bearer_token
# ---------------------------------------------------------------------------


async def test_verify_optional_revoked() -> None:
    request = _make_request()
    request.state.is_token_blacklisted = True
    result = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert result is None


async def test_verify_optional_wrong_type() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "refresh", "sub": "u"}
    with patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload):
        result = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert result is None


async def test_verify_optional_no_sub() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access"}
    with patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload):
        result = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert result is None


async def test_verify_optional_cached_email_success() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {
        "jti": "j1",
        "token_type": "access",
        "sub": "user@example.com",
        "token_version": 2,
    }
    user = {"id": 1, "email": "user@example.com", "is_active": True, "token_version": 2, "tenant_id": 5}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        result = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert result is not None
    assert result.user.id == 1


async def test_verify_optional_cached_no_user() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=None)
        result = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert result is None


async def test_verify_optional_cached_deleted() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 0}
    user = {"id": 2, "email": "u@x.com", "is_active": True, "is_deleted": True, "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        result = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert result is None


async def test_verify_optional_cached_version_mismatch() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 5}
    user = {"id": 2, "email": "u@x.com", "is_active": True, "token_version": 2}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        result = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert result is None


async def test_verify_optional_verify_token_none() -> None:
    request = _make_request()
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=None)),
    ):
        result = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert result is None


async def test_verify_optional_verify_token_exception() -> None:
    request = _make_request()
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(side_effect=ValueError("bad"))),
    ):
        result = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert result is None


async def test_verify_optional_success() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "token_version": 0, "tenant_id": 1}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        ctx = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert ctx is not None
    assert ctx.user.id == 2


async def test_verify_optional_inactive() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": False, "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        result = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert result is None


async def test_verify_optional_tenant_mismatch() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "token_version": 0, "tenant_id": 1}
    tenant = MagicMock()
    tenant.id = 9
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        result = await _verify_optional_bearer_token("tok", _make_db(), request, tenant)
    assert result is None


async def test_verify_optional_state_none_runs_db_fallback() -> None:
    """Regression: middleware tri-state None (Redis down) must trigger the DB
    fallback in the optional path, not be treated as "not blacklisted".
    Previously getattr(..., False) collapsed None to falsy and skipped the
    fallback — a fail-open hole for revoked tokens during Redis outages."""
    state = MagicMock()
    state.is_token_blacklisted = None  # Redis was down during the middleware probe
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch(
            "src.app.api.deps.auth._is_token_blacklisted_with_fallback",
            new=AsyncMock(return_value=True),
        ) as mock_fb,
    ):
        result = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert result is None
    mock_fb.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_optional_auth_context
# ---------------------------------------------------------------------------


async def test_get_optional_no_header() -> None:
    request = _make_request()
    result = await get_optional_auth_context(request, _make_db(), None)
    assert result is None


async def test_get_optional_non_bearer() -> None:
    request = _make_request(headers={"Authorization": "Basic abc"})
    result = await get_optional_auth_context(request, _make_db(), None)
    assert result is None


async def test_get_optional_api_key_invalid() -> None:
    request = _make_request(headers={"X-API-Key": "key"})
    with patch("src.app.api.deps.auth.get_user_by_api_key", new=AsyncMock(return_value=None)):
        with pytest.raises(UnauthorizedException):
            await get_optional_auth_context(request, _make_db(), None)


async def test_get_optional_api_key_valid() -> None:
    request = _make_request(headers={"X-API-Key": "key"})
    api_user = AuthenticatedUser(id=1, email="a@b.com", api_key_id="k1", api_key_scopes=["read", "write"])
    with (
        patch("src.app.api.deps.auth.get_user_by_api_key", new=AsyncMock(return_value=api_user)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
    ):
        ctx = await get_optional_auth_context(request, _make_db(), None)
    assert ctx is not None
    assert ctx.user.id == 1


async def test_get_optional_bearer_success() -> None:
    request = _make_request(headers={"Authorization": "Bearer tok"})
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        svc = mock_us.return_value
        svc.get_user_auth_info = AsyncMock(return_value=user)
        ctx = await get_optional_auth_context(request, _make_db(), None)
    assert ctx is not None
    assert ctx.user.id == 2


async def test_get_optional_bearer_exception() -> None:
    request = _make_request(headers={"Authorization": "Bearer tok"})
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(side_effect=ValueError("x"))),
    ):
        result = await get_optional_auth_context(request, _make_db(), None)
    assert result is None


# ---------------------------------------------------------------------------
# get_optional_user
# ---------------------------------------------------------------------------


async def test_get_optional_user_none() -> None:
    request = _make_request()
    result = await get_optional_user(request, _make_db())
    assert result is None


async def test_get_optional_user_from_context() -> None:
    request = _make_request()
    ctx = AuthContext(user=AuthenticatedUser(id=1, email="a@b.com"), token_data=None, tenant=None)
    result = await get_optional_user(request, _make_db(), None, ctx)
    assert result is not None
    assert result.id == 1


async def test_get_optional_user_rebuild() -> None:
    request = _make_request(headers={"Authorization": "Bearer tok"})
    with patch("src.app.api.deps.auth.get_optional_auth_context", new=AsyncMock()) as mock_g:
        user = AuthenticatedUser(id=5, email="e@f.com")
        mock_g.return_value = AuthContext(user=user, token_data=None, tenant=None)
        result = await get_optional_user(request, _make_db())
    assert result is not None
    assert result.id == 5


# ---------------------------------------------------------------------------
# get_current_superuser
# ---------------------------------------------------------------------------


async def test_get_current_superuser_inactive() -> None:
    user = AuthenticatedUser(id=1, email="a@b.com", is_active=False)
    with pytest.raises(ForbiddenException):
        await get_current_superuser(user)


async def test_get_current_superuser_not_super() -> None:
    user = AuthenticatedUser(id=1, email="a@b.com", is_superuser=False)
    with pytest.raises(ForbiddenException):
        await get_current_superuser(user)


async def test_get_current_superuser_ok() -> None:
    user = AuthenticatedUser(id=1, email="a@b.com", is_superuser=True, is_active=True)
    result = await get_current_superuser(user)
    assert result.id == 1


# ---------------------------------------------------------------------------
# resolve_principal (unified seam tests)
# ---------------------------------------------------------------------------


async def test_resolve_principal_strict_success() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="alice", token_version=1)
    user = {"id": 10, "email": "alice@example.com", "username": "alice", "is_active": True, "token_version": 1}

    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        mock_us.return_value.get_user_auth_info = AsyncMock(return_value=user)
        ctx = await resolve_principal(request, _make_db(), token="valid_tok", strict=True)
    assert ctx is not None
    assert ctx.user.id == 10
    assert ctx.user.email == "alice@example.com"


async def test_resolve_principal_strict_unauthenticated_raises() -> None:
    request = _make_request()
    with pytest.raises(UnauthorizedException, match="User not authenticated"):
        await resolve_principal(request, _make_db(), token="", strict=True)


async def test_resolve_principal_optional_unauthenticated_returns_none() -> None:
    request = _make_request()
    result = await resolve_principal(request, _make_db(), token="", strict=False)
    assert result is None


async def test_resolve_principal_optional_success() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="alice", token_version=0)
    user = {"id": 10, "email": "alice@example.com", "username": "alice", "is_active": True, "token_version": 0}

    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserService") as mock_us,
    ):
        mock_us.return_value.get_user_auth_info = AsyncMock(return_value=user)
        result = await resolve_principal(request, _make_db(), token="valid_tok", strict=False)
    assert result is not None
    assert result.user.id == 10


# ---------------------------------------------------------------------------
# P1-1: API-key scope enforcement at the resolve-principal seam
# ---------------------------------------------------------------------------


def test_scope_allows_coarse_read_write() -> None:
    assert api_key_scope_allows(["read"], "GET", "/v1/posts") is True
    assert api_key_scope_allows(["read"], "POST", "/v1/posts") is False
    assert api_key_scope_allows(["write"], "POST", "/v1/posts") is True
    # "write" implies "read" for safe methods; "read" never implies "write".
    assert api_key_scope_allows(["write"], "GET", "/v1/posts") is True


def test_scope_allows_admin_wildcard_and_denies_empty() -> None:
    assert api_key_scope_allows(["admin"], "DELETE", "/v1/users/1") is True
    assert api_key_scope_allows([], "GET", "/v1/posts") is False
    assert api_key_scope_allows(None, "GET", "/v1/posts") is False
    assert api_key_scope_allows(None, "POST", "/v1/posts") is False


def test_scope_allows_resource_qualified() -> None:
    assert api_key_scope_allows(["read:posts"], "GET", "/v1/posts/1") is True
    assert api_key_scope_allows(["read:posts"], "GET", "/v1/users") is False
    assert api_key_scope_allows(["read:posts"], "POST", "/v1/posts") is False
    assert api_key_scope_allows(["write:posts"], "POST", "/api/v1/posts") is True
    # Version prefix is skipped; a non-version first segment is the resource.
    assert api_key_scope_allows(["read:posts"], "GET", "/posts/1") is True
    assert api_key_scope_allows(["read:vault"], "GET", "/v1/vault/x") is True


async def test_seam_denies_unscoped_key_even_on_get() -> None:
    request = _make_request(headers={"X-API-Key": "key"})
    api_user = AuthenticatedUser(id=1, email="a@b.com", api_key_id="k1", api_key_scopes=[])
    with (
        patch("src.app.api.deps.auth.get_user_by_api_key", new=AsyncMock(return_value=api_user)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
    ):
        with pytest.raises(ForbiddenException, match="missing required scope"):
            await get_optional_auth_context(request, _make_db(), None)


async def test_seam_denies_read_key_on_write() -> None:
    request = _make_request(headers={"X-API-Key": "key"})
    request.method = "POST"
    api_user = AuthenticatedUser(id=1, email="a@b.com", api_key_id="k1", api_key_scopes=["read"])
    with (
        patch("src.app.api.deps.auth.get_user_by_api_key", new=AsyncMock(return_value=api_user)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
    ):
        with pytest.raises(ForbiddenException, match="missing required scope"):
            await get_optional_auth_context(request, _make_db(), None)


async def test_seam_allows_scoped_key() -> None:
    request = _make_request(headers={"X-API-Key": "key"})
    request.url.path = "/v1/posts/1"
    api_user = AuthenticatedUser(
        id=1, email="a@b.com", api_key_id="k1", api_key_scopes=["read:posts"]
    )
    with (
        patch("src.app.api.deps.auth.get_user_by_api_key", new=AsyncMock(return_value=api_user)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
    ):
        ctx = await get_optional_auth_context(request, _make_db(), None)
    assert ctx is not None
    assert ctx.user.id == 1


async def test_enforce_rejects_unverifiable_request_shape() -> None:
    user = AuthenticatedUser(id=1, email="a@b.com", api_key_id="k1", api_key_scopes=["admin"])
    with pytest.raises(ForbiddenException):
        enforce_api_key_scope(user, None)
