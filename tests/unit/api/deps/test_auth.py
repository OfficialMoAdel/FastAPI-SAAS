"""Tests for src.app.api.deps.auth covering auth context resolution, token
verification, API key auth, optional auth, superuser checks, and scope gating.

External dependencies (UserDirectory, verify_token, JWT cache, tenant context,
API key service) are mocked.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
from src.app.core.principal import (
    DENIAL_TABLE,
    OPTIONAL,
    REQUIRED,
    DenialReason,
    denial,
)
from src.app.core.schemas import TokenData
from src.app.core.tenancy.context import TenantContext, TenantContextError
from src.app.shared.exceptions import ForbiddenError as ForbiddenException
from src.app.shared.exceptions import ServiceUnavailableError
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


@asynccontextmanager
async def _stub_db_context() -> Any:
    yield _make_db()


@pytest.fixture(autouse=True)
def _serve_lazily_acquired_db() -> Any:
    """Serve the session the auth seams now open for themselves.

    S-1 moved session acquisition out of the dependency signature and into
    ``get_db_context()`` at the point of use, so these tests can no longer
    inject a session by passing one in. Everything that touches the session in
    this module is mocked (``UserDirectory`` and friends), so a stub context
    is enough to reach it.
    """
    with patch("src.app.core.db.database.get_db_context", _stub_db_context):
        yield


def _bound_tenant(tenant_id: Any = 5) -> MagicMock:
    """A real, active tenant — the state production actually reaches.

    P1-6: the tenant middleware binds either a resolved tenant or the
    NO_TENANT sentinel; it never leaves the context unset for a request that
    reaches a guard (a non-exempt path with no header is rejected with 400).
    Tests that used ``return_value=None`` modelled an unreachable state and
    encoded the pre-fix behaviour where the binding check was skipped.

    Defaults to tenant 5, matching the fixtures in this module.
    """
    from src.app.core.tenancy.context import TenantContext

    # A real TenantContext: AuthContext is a pydantic model and validates the
    # tenant field, so a MagicMock is rejected at construction.
    return TenantContext(
        tenant_id=UUID(int=tenant_id),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )



def _dumpable(user: dict | None) -> MagicMock | None:
    """Wrap a raw user dict the way UserDirectory seam methods return it.

    Seam methods return validated models; callers immediately ``.model_dump()``.
    Mocking at the seam therefore means returning an object whose
    ``model_dump()`` yields the fixture dict (``None`` stays ``None``).
    """
    if user is None:
        return None
    m = MagicMock()
    m.model_dump.return_value = user
    return m


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
    ctx = _create_auth_context(user, {"sub": "a"}, None, _make_db())
    assert isinstance(ctx.user, AuthenticatedUser)
    assert ctx.user.id == 1
    assert ctx.token_data == {"sub": "a"}


async def test_create_auth_context_from_obj() -> None:
    user = AuthenticatedUser(id=2, email="c@d.com")
    ctx = _create_auth_context(user, None, None, _make_db())
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
            await get_auth_context(request, "")


async def test_get_auth_context_cached_revoked() -> None:
    request = _make_request()
    request.state.is_token_blacklisted = True
    with patch(
        "src.app.api.deps.auth.get_cached_jwt_payload",
        return_value={"jti": "j1", "token_type": "access", "sub": "u"},
    ):
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, "tok")


async def test_get_auth_context_cached_wrong_type() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    with patch(
        "src.app.api.deps.auth.get_cached_jwt_payload",
        return_value={"jti": "j1", "token_type": "refresh", "sub": "u"},
    ):
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, "tok")


async def test_get_auth_context_cached_no_sub() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    with patch(
        "src.app.api.deps.auth.get_cached_jwt_payload",
        return_value={"jti": "j1", "token_type": "access"},
    ):
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, "tok")


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
    user = {"id": 1, "email": "user@example.com", "is_active": True, "token_version": 2, "tenant_id": UUID(int=5)}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=_bound_tenant()),
        patch(
            "src.app.api.deps.auth._is_token_blacklisted_with_fallback",
            new=AsyncMock(return_value=False),
        ),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        ctx = await get_auth_context(request, "tok")
    assert ctx.user.id == 1


async def test_get_auth_context_verify_token_success() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "token_version": 0, "tenant_id": UUID(int=5)}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=_bound_tenant()),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        ctx = await get_auth_context(request, "tok")
    assert ctx.user.id == 2


async def test_get_auth_context_user_not_found() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=None)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, "tok")


async def test_get_auth_context_inactive() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": False, "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, "tok")


async def test_get_auth_context_deleted() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "is_deleted": True, "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, "tok")


async def test_get_auth_context_version_mismatch() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=5)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "token_version": 2}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, "tok")


async def test_get_auth_context_tenant_mismatch() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "token_version": 0, "tenant_id": UUID(int=5)}
    tenant = TenantContext(tenant_id=UUID(int=9), tenant_slug="other", tenant_name="Other", is_active=True)
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=tenant),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        with pytest.raises(ForbiddenException):
            await get_auth_context(request, "tok")


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
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        ctx = await get_auth_context(request, "tok")
    assert ctx.user.id == 2


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------


async def test_get_current_user_from_context() -> None:
    request = _make_request()
    ctx = AuthContext(user=AuthenticatedUser(id=1, email="a@b.com"), token_data=None, tenant=None)
    result = await get_current_user(request, "tok", ctx)
    assert result.id == 1


async def test_get_current_user_rebuild() -> None:
    request = _make_request(headers={"Authorization": "Bearer abc"})
    with patch("src.app.api.deps.auth.get_auth_context", new=AsyncMock()) as mock_gac:
        user = AuthenticatedUser(id=3, email="c@d.com")
        mock_gac.return_value = AuthContext(user=user, token_data=None, tenant=None)
        result = await get_current_user(request, "", auth_context=None)
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
    key_info = {"user_id": 1, "tenant_id": UUID(int=5), "scopes": []}
    with (
        patch("src.app.modules.api_keys.service.APIKeyService") as mock_svc_cls,
        patch("src.app.api.deps.auth.get_current_tenant", return_value=MagicMock(tenant_id=9)),
    ):
        svc = mock_svc_cls.return_value
        svc.validate_api_key = AsyncMock(return_value=key_info)
        with pytest.raises(UnauthorizedException):
            await get_user_by_api_key("key", _make_db(), request)


async def test_get_user_by_api_key_user_not_found() -> None:
    request = _make_request()
    key_info = {"user_id": 1, "tenant_id": UUID(int=5), "scopes": []}
    with (
        patch("src.app.modules.api_keys.service.APIKeyService") as mock_svc_cls,
        patch("src.app.api.deps.auth.get_current_tenant", return_value=_bound_tenant()),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        svc = mock_svc_cls.return_value
        svc.validate_api_key = AsyncMock(return_value=key_info)
        directory = mock_ud.return_value
        directory.by_id = AsyncMock(return_value=None)
        result = await get_user_by_api_key("key", _make_db(), request)
    assert result is None


async def test_get_user_by_api_key_dict_user() -> None:
    request = _make_request()
    key_info = {"user_id": 1, "tenant_id": UUID(int=5), "scopes": ["read"], "key_id": "k1"}
    user_data = {"id": 1, "username": "a", "email": "a@b.com", "is_active": True}
    with (
        patch("src.app.modules.api_keys.service.APIKeyService") as mock_svc_cls,
        patch("src.app.api.deps.auth.get_current_tenant", return_value=_bound_tenant()),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        svc = mock_svc_cls.return_value
        svc.validate_api_key = AsyncMock(return_value=key_info)
        directory = mock_ud.return_value
        directory.by_id = AsyncMock(return_value=_dumpable(user_data))
        result = await get_user_by_api_key("key", _make_db(), request)
    assert result is not None
    assert result.id == 1


async def test_get_user_by_api_key_obj_user() -> None:
    request = _make_request()
    key_info = {"user_id": 1, "tenant_id": UUID(int=5), "scopes": [], "key_id": "k1"}
    user_obj = MagicMock()
    user_obj.id = 1
    user_obj.username = "a"
    user_obj.email = "a@b.com"
    user_obj.tenant_id = 5
    user_obj.is_active = True
    user_obj.is_superuser = False
    user_obj.token_version = 0
    user_obj.model_dump.return_value = {
        "id": 1,
        "username": "a",
        "email": "a@b.com",
        "tenant_id": UUID(int=5),
        "is_active": True,
        "is_superuser": False,
        "token_version": 0,
    }
    with (
        patch("src.app.modules.api_keys.service.APIKeyService") as mock_svc_cls,
        patch("src.app.api.deps.auth.get_current_tenant", return_value=_bound_tenant()),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        svc = mock_svc_cls.return_value
        svc.validate_api_key = AsyncMock(return_value=key_info)
        directory = mock_ud.return_value
        directory.by_id = AsyncMock(return_value=user_obj)
        result = await get_user_by_api_key("key", _make_db(), request)
    assert result is not None
    assert result.id == 1


async def test_get_user_by_api_key_inactive() -> None:
    request = _make_request()
    key_info = {"user_id": 1, "tenant_id": UUID(int=5), "scopes": [], "key_id": "k1"}
    user_data = {"id": 1, "username": "a", "email": "a@b.com", "is_active": False}
    with (
        patch("src.app.modules.api_keys.service.APIKeyService") as mock_svc_cls,
        patch("src.app.api.deps.auth.get_current_tenant", return_value=_bound_tenant()),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        svc = mock_svc_cls.return_value
        svc.validate_api_key = AsyncMock(return_value=key_info)
        directory = mock_ud.return_value
        directory.by_id = AsyncMock(return_value=_dumpable(user_data))
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
            await get_auth_context(request, "")


async def test_get_current_user_no_token_no_header() -> None:
    request = _make_request(headers={})
    with patch("src.app.api.deps.auth.get_auth_context", new=AsyncMock()) as mock_gac:
        user = AuthenticatedUser(id=4, email="c@d.com")
        mock_gac.return_value = AuthContext(user=user, token_data=None, tenant=None)
        result = await get_current_user(request, "", auth_context=None)
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
    user = {"id": 1, "email": "user@example.com", "is_active": True, "token_version": 2, "tenant_id": UUID(int=5)}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=_bound_tenant()),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        ctx = await get_auth_context(request, "tok")
    assert ctx.user.id == 1


async def test_get_auth_context_cached_no_user() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=None)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, "tok")


async def test_get_auth_context_cached_deleted() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 0}
    user = {"id": 2, "email": "u@x.com", "is_active": True, "is_deleted": True, "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, "tok")


async def test_get_auth_context_cached_version_mismatch() -> None:
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 5}
    user = {"id": 2, "email": "u@x.com", "is_active": True, "token_version": 2}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await get_auth_context(request, "tok")


async def test_get_optional_auth_context_exception() -> None:
    """C3: a presented-but-unverifiable bearer token is fail-loud (401),
    never silent anonymous. Anonymous is only for no credential presented."""
    request = _make_request(headers={"Authorization": "Bearer abc"})
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch(
            "src.app.api.deps.auth.verify_token",
            new=AsyncMock(side_effect=ValueError("boom")),
        ),
    ):
        with pytest.raises(UnauthorizedException):
            await get_optional_auth_context(request, None)


async def test_get_optional_auth_context_user_lookup_exception() -> None:
    """C3: unexpected errors (e.g. DB down) on a presented credential are
    fail-loud (401), never silent anonymous."""
    request = _make_request(headers={"Authorization": "Bearer abc"})
    token_data = TokenData(username_or_email="bob", token_version=0)
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(side_effect=RuntimeError("db down"))
        with pytest.raises(UnauthorizedException):
            await get_optional_auth_context(request, None)


# ---------------------------------------------------------------------------
async def test_verify_optional_revoked() -> None:
    """C3: revoked presented token is fail-loud (401), never anonymous."""
    request = _make_request()
    request.state.is_token_blacklisted = True
    with pytest.raises(UnauthorizedException):
        await _verify_optional_bearer_token("tok", _make_db(), request, None)


async def test_verify_optional_wrong_type() -> None:
    """C3: wrong token_type on a presented credential is fail-loud (401)."""
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "refresh", "sub": "u"}
    with patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload):
        with pytest.raises(UnauthorizedException):
            await _verify_optional_bearer_token("tok", _make_db(), request, None)


async def test_verify_optional_no_sub() -> None:
    """C3: missing sub on a presented credential is fail-loud (401)."""
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access"}
    with patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload):
        with pytest.raises(UnauthorizedException):
            await _verify_optional_bearer_token("tok", _make_db(), request, None)


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
    user = {"id": 1, "email": "user@example.com", "is_active": True, "token_version": 2, "tenant_id": UUID(int=5)}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=_bound_tenant()),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        result = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert result is not None
    assert result.user.id == 1


async def test_verify_optional_cached_no_user() -> None:
    """C3: unknown user for a presented credential is fail-loud (401)."""
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=None)
        with pytest.raises(UnauthorizedException):
            await _verify_optional_bearer_token("tok", _make_db(), request, None)


async def test_verify_optional_cached_deleted() -> None:
    """C3: deleted user for a presented credential is fail-loud (401)."""
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 0}
    user = {"id": 2, "email": "u@x.com", "is_active": True, "is_deleted": True, "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await _verify_optional_bearer_token("tok", _make_db(), request, None)


async def test_verify_optional_cached_version_mismatch() -> None:
    """C3: stale token_version on a presented credential is fail-loud (401)."""
    state = MagicMock()
    state.is_token_blacklisted = False
    request = _make_request(state=state)
    payload = {"jti": "j1", "token_type": "access", "sub": "u", "token_version": 5}
    user = {"id": 2, "email": "u@x.com", "is_active": True, "token_version": 2}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await _verify_optional_bearer_token("tok", _make_db(), request, None)


async def test_verify_optional_verify_token_none() -> None:
    """C3: verify_token returning None on a presented token is fail-loud (401)."""
    request = _make_request()
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(UnauthorizedException):
            await _verify_optional_bearer_token("tok", _make_db(), request, None)


async def test_verify_optional_verify_token_exception() -> None:
    """C3: verify_token raising on a presented token is fail-loud (401)."""
    request = _make_request()
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(side_effect=ValueError("bad"))),
    ):
        with pytest.raises(UnauthorizedException):
            await _verify_optional_bearer_token("tok", _make_db(), request, None)


async def test_verify_optional_success() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "token_version": 0, "tenant_id": UUID(int=5)}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=_bound_tenant()),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        ctx = await _verify_optional_bearer_token("tok", _make_db(), request, None)
    assert ctx is not None
    assert ctx.user.id == 2


async def test_verify_optional_inactive() -> None:
    """C3: inactive user on a presented credential is fail-loud (401)."""
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": False, "token_version": 0}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        with pytest.raises(UnauthorizedException):
            await _verify_optional_bearer_token("tok", _make_db(), request, None)


async def test_verify_optional_tenant_mismatch() -> None:
    """C3: tenant mismatch on a presented credential is fail-loud (403)."""
    request = _make_request()
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "token_version": 0, "tenant_id": UUID(int=5)}
    tenant = MagicMock()
    tenant.id = 9
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        with pytest.raises(ForbiddenException):
            await _verify_optional_bearer_token("tok", _make_db(), request, tenant)


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
        with pytest.raises(UnauthorizedException):
            await _verify_optional_bearer_token("tok", _make_db(), request, None)
    mock_fb.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_optional_auth_context
# ---------------------------------------------------------------------------


async def test_get_optional_no_header() -> None:
    request = _make_request()
    result = await get_optional_auth_context(request, None)
    assert result is None


async def test_get_optional_non_bearer() -> None:
    """C3: a presented NON-Bearer scheme is fail-loud (401), never silently
    anonymous — only an absent credential yields None."""
    request = _make_request(headers={"Authorization": "Basic abc"})
    with pytest.raises(UnauthorizedException):
        await get_optional_auth_context(request, None)


async def test_get_optional_api_key_invalid() -> None:
    request = _make_request(headers={"X-API-Key": "key"})
    with patch("src.app.api.deps.auth.get_user_by_api_key", new=AsyncMock(return_value=None)):
        with pytest.raises(UnauthorizedException):
            await get_optional_auth_context(request, None)


async def test_get_optional_api_key_valid() -> None:
    request = _make_request(headers={"X-API-Key": "key"})
    api_user = AuthenticatedUser(id=1, email="a@b.com", api_key_id="k1", api_key_scopes=["read", "write"])
    with (
        patch("src.app.api.deps.auth.get_user_by_api_key", new=AsyncMock(return_value=api_user)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
    ):
        ctx = await get_optional_auth_context(request, None)
    assert ctx is not None
    assert ctx.user.id == 1


async def test_get_optional_bearer_success() -> None:
    request = _make_request(headers={"Authorization": "Bearer tok"})
    token_data = TokenData(username_or_email="bob", token_version=0)
    user = {"id": 2, "email": "bob@x.com", "is_active": True, "token_version": 0, "tenant_id": UUID(int=5)}
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=_bound_tenant()),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        directory = mock_ud.return_value
        directory.auth_info_dict = AsyncMock(return_value=user)
        ctx = await get_optional_auth_context(request, None)
    assert ctx is not None
    assert ctx.user.id == 2


async def test_get_optional_bearer_exception() -> None:
    """C3: verify failure on a presented bearer token is fail-loud (401)."""
    request = _make_request(headers={"Authorization": "Bearer tok"})
    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(side_effect=ValueError("x"))),
    ):
        with pytest.raises(UnauthorizedException):
            await get_optional_auth_context(request, None)


# ---------------------------------------------------------------------------
# get_optional_user
# ---------------------------------------------------------------------------


async def test_get_optional_user_none() -> None:
    request = _make_request()
    result = await get_optional_user(request)
    assert result is None


async def test_get_optional_user_from_context() -> None:
    request = _make_request()
    ctx = AuthContext(user=AuthenticatedUser(id=1, email="a@b.com"), token_data=None, tenant=None)
    result = await get_optional_user(request, None, ctx)
    assert result is not None
    assert result.id == 1


async def test_get_optional_user_rebuild() -> None:
    request = _make_request(headers={"Authorization": "Bearer tok"})
    with patch("src.app.api.deps.auth.get_optional_auth_context", new=AsyncMock()) as mock_g:
        user = AuthenticatedUser(id=5, email="e@f.com")
        mock_g.return_value = AuthContext(user=user, token_data=None, tenant=None)
        result = await get_optional_user(request)
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


async def test_resolve_principal_required_success() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="alice", token_version=1)
    user = {
        "id": 10,
        "email": "alice@example.com",
        "username": "alice",
        "is_active": True,
        "token_version": 1,
        "tenant_id": UUID(int=5),
    }

    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=_bound_tenant()),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        mock_ud.return_value.auth_info_dict = AsyncMock(return_value=user)
        ctx = await resolve_principal(request, _make_db(), token="valid_tok", policy=REQUIRED)
    assert ctx is not None
    assert ctx.user.id == 10
    assert ctx.user.email == "alice@example.com"


async def test_resolve_principal_required_unauthenticated_raises() -> None:
    request = _make_request()
    with pytest.raises(UnauthorizedException, match="User not authenticated"):
        await resolve_principal(request, _make_db(), token="", policy=REQUIRED)


async def test_resolve_principal_optional_unauthenticated_returns_none() -> None:
    request = _make_request()
    result = await resolve_principal(request, _make_db(), token="", policy=OPTIONAL)
    assert result is None


async def test_resolve_principal_optional_success() -> None:
    request = _make_request()
    token_data = TokenData(username_or_email="alice", token_version=0)
    user = {
        "id": 10,
        "email": "alice@example.com",
        "username": "alice",
        "is_active": True,
        "token_version": 0,
        "tenant_id": UUID(int=5),
    }

    with (
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
        patch("src.app.api.deps.auth.verify_token", new=AsyncMock(return_value=token_data)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=_bound_tenant()),
        patch("src.app.api.deps.auth.UserDirectory") as mock_ud,
    ):
        mock_ud.return_value.auth_info_dict = AsyncMock(return_value=user)
        result = await resolve_principal(request, _make_db(), token="valid_tok", policy=OPTIONAL)
    assert result is not None
    assert result.user.id == 10


def test_denial_table_covers_every_reason() -> None:
    """The outcome table is total: an unmapped refusal would be a KeyError at runtime."""
    assert set(DENIAL_TABLE) == set(DenialReason)


def test_denial_table_status_contract() -> None:
    """One table decides every status the principal path can emit."""
    # A missing tenant is the one no-tenant contract (P3-#14) -> 403.
    assert DENIAL_TABLE[DenialReason.TENANT_CONTEXT_MISSING].error is TenantContextError
    # No credential is an authentication failure -> 401.
    assert DENIAL_TABLE[DenialReason.NO_CREDENTIAL].error is UnauthorizedException
    # A valid principal lacking privilege is an authorization failure -> 403.
    assert DENIAL_TABLE[DenialReason.NOT_SUPERUSER].error is ForbiddenException
    # An unwired DIP seam is an operator problem, not a client one -> 503.
    assert DENIAL_TABLE[DenialReason.PROVIDER_UNWIRED].error is ServiceUnavailableError


def test_denial_formats_scope_message() -> None:
    """The table carries messages, so a parametrised row must interpolate."""
    exc = denial(DenialReason.SCOPE_MISSING, scope="read")
    assert isinstance(exc, ForbiddenException)
    assert "read" in exc.message


def test_denial_unknown_reason_fails_loud() -> None:
    """An unmapped refusal is a bug, not a policy — never a silent default."""
    with pytest.raises(KeyError):
        denial("not_a_real_reason")  # type: ignore[arg-type]


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
            await get_optional_auth_context(request, None)


async def test_seam_denies_read_key_on_write() -> None:
    request = _make_request(headers={"X-API-Key": "key"})
    request.method = "POST"
    api_user = AuthenticatedUser(id=1, email="a@b.com", api_key_id="k1", api_key_scopes=["read"])
    with (
        patch("src.app.api.deps.auth.get_user_by_api_key", new=AsyncMock(return_value=api_user)),
        patch("src.app.api.deps.auth.get_current_tenant", return_value=None),
    ):
        with pytest.raises(ForbiddenException, match="missing required scope"):
            await get_optional_auth_context(request, None)


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
        ctx = await get_optional_auth_context(request, None)
    assert ctx is not None
    assert ctx.user.id == 1


async def test_enforce_rejects_unverifiable_request_shape() -> None:
    user = AuthenticatedUser(id=1, email="a@b.com", api_key_id="k1", api_key_scopes=["admin"])
    with pytest.raises(ForbiddenException):
        enforce_api_key_scope(user, None)


# =============================================================================
# Phase 2.1 — the bearer path reads the RAW auth payload
# =============================================================================

_PROFILE_TENANT = UUID("11111111-1111-4111-8111-111111111111")


def _db_payload(**overrides: Any) -> dict:
    """A ``get_user_with_tier_eager`` dict as the DB hands it over."""
    payload: dict = {
        "id": 11,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": None,
        "name": "Test User",
        "username": "testuser",
        "email": "test@example.com",
        "profile_image_url": None,
        "tenant_id": _PROFILE_TENANT,
        "tier_id": 3,
        "hashed_password": "$2b$12$" + "x" * 53,
        "is_superuser": False,
        "is_deleted": False,
        "is_active": True,
        "is_email_verified": True,
        "token_version": 4,
        "tier": {
            "id": 3,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "updated_at": None,
            "name": "Free",
        },
    }
    payload.update(overrides)
    return payload


def _cache_payload(**overrides: Any) -> dict:
    """The same row after a JSON round trip (cache): str tenants, ISO timestamps."""
    payload = _db_payload(**overrides)
    payload["tenant_id"] = str(payload["tenant_id"])
    payload["created_at"] = payload["created_at"].isoformat()
    if isinstance(payload.get("tier"), dict):
        payload["tier"] = {**payload["tier"], "created_at": payload["tier"]["created_at"].isoformat()}
    return payload


def _patch_directory(*, cached: dict | None, db_row: dict | None = None):
    """Patch the directory's two sources; return the collaborators for assertions."""
    return (
        patch(
            "src.app.modules.users.directory.UserCacheInvalidator.get_cached_user",
            new=AsyncMock(return_value=cached),
        ),
        patch(
            "src.app.modules.users.directory.crud_users.get_user_with_tier_eager",
            new=AsyncMock(return_value=db_row),
        ),
        patch(
            "src.app.modules.users.directory.UserCacheInvalidator.cache_user_auth_info",
            new=AsyncMock(),
        ),
    )


class TestBearerPathReadsTheRawAuthPayload:
    """One validation, not two: ``AuthenticatedUser`` is the validator."""

    @pytest.mark.asyncio
    async def test_cache_and_db_payloads_build_the_same_principal(self) -> None:
        """Every TYPED field is coerced identically, whichever source answered."""
        from src.app.api.deps.auth import _load_and_validate_user

        cache_patch, _, _ = _patch_directory(cached=_cache_payload())
        with cache_patch:
            cached_user = await _load_and_validate_user(_make_db(), "test@example.com", 4)

        _, db_patch, write_patch = _patch_directory(cached=None, db_row=_db_payload())
        with db_patch, write_patch:
            db_user = await _load_and_validate_user(_make_db(), "test@example.com", 4)

        cached_principal = AuthenticatedUser(**cached_user)
        db_principal = AuthenticatedUser(**db_user)

        assert cached_principal.model_dump(exclude={"tier"}) == db_principal.model_dump(exclude={"tier"})
        assert cached_principal.tier is not None and db_principal.tier is not None
        assert {
            key: value for key, value in cached_principal.tier.items() if key not in ("created_at", "updated_at")
        } == {
            key: value for key, value in db_principal.tier.items() if key not in ("created_at", "updated_at")
        }

    @pytest.mark.asyncio
    async def test_the_untyped_tier_bag_keeps_its_source_timestamp_representation(self) -> None:
        """Pinned difference: JSON has no datetime, so the cache carries ISO strings.

        ``AuthenticatedUser.tier`` is a ``dict[str, Any]`` bag, so its values are
        the only place where cache and DB payloads can disagree — every typed
        field is coerced by the principal itself. Nothing on this path reads the
        tier timestamps (tier consumers use ``tier_id`` and ``tier["name"]``);
        if that ever changes, normalize HERE rather than adding a validator to
        the per-request principal.
        """
        from src.app.api.deps.auth import _load_and_validate_user

        cache_patch, _, _ = _patch_directory(cached=_cache_payload())
        with cache_patch:
            cached_user = await _load_and_validate_user(_make_db(), "test@example.com", 4)

        _, db_patch, write_patch = _patch_directory(cached=None, db_row=_db_payload())
        with db_patch, write_patch:
            db_user = await _load_and_validate_user(_make_db(), "test@example.com", 4)

        cached_tier = AuthenticatedUser(**cached_user).tier
        db_tier = AuthenticatedUser(**db_user).tier
        assert cached_tier is not None and db_tier is not None
        assert isinstance(cached_tier["created_at"], str)
        assert isinstance(db_tier["created_at"], datetime)
        assert cached_tier["created_at"] == db_tier["created_at"].isoformat()

    @pytest.mark.asyncio
    async def test_the_request_path_does_not_validate_through_user_auth_info(self) -> None:
        """A ``UserAuthInfo`` round trip here is the thing Phase 2.1 removed."""
        from src.app.api.deps.auth import _load_and_validate_user

        cache_patch, _, _ = _patch_directory(cached=_cache_payload())
        with (
            patch(
                "src.app.modules.users.directory.UserAuthInfo.model_validate",
                side_effect=AssertionError("the bearer path re-validated through UserAuthInfo"),
            ),
            cache_patch,
        ):
            user = await _load_and_validate_user(_make_db(), "test@example.com", 4)

        assert user["id"] == 11

    @pytest.mark.asyncio
    async def test_a_cache_hit_does_not_touch_the_database(self) -> None:
        from src.app.api.deps.auth import _load_and_validate_user

        cache_patch, db_patch, write_patch = _patch_directory(cached=_cache_payload())
        with cache_patch, db_patch as db_get, write_patch:
            await _load_and_validate_user(_make_db(), "test@example.com", 4)

        db_get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_miss_writes_through_and_returns_the_db_row(self) -> None:
        from src.app.api.deps.auth import _load_and_validate_user

        cache_patch, db_patch, write_patch = _patch_directory(cached=None, db_row=_db_payload())
        with cache_patch, db_patch, write_patch as write:
            user = await _load_and_validate_user(_make_db(), "testuser", 4)

        assert user["id"] == 11
        write.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tenant", "expected"),
        [
            (_PROFILE_TENANT, _PROFILE_TENANT),
            # JSON cache payload: a UUID-shaped STRING must arrive as the UUID,
            # or a principal's tenant type would depend on which tier answered.
            (str(_PROFILE_TENANT), _PROFILE_TENANT),
            # Non-UUID ids are legal (int tenant ids / slugs). The union's
            # smart mode renders a bare int as its string form, which is the
            # behaviour the principal has always had.
            (7, "7"),
        ],
    )
    async def test_every_tenant_id_shape_yields_a_usable_principal(self, tenant: Any, expected: Any) -> None:
        from src.app.api.deps.auth import _load_and_validate_user

        cache_patch, _, _ = _patch_directory(cached=_cache_payload(tenant_id=tenant))
        with cache_patch:
            user = await _load_and_validate_user(_make_db(), "test@example.com", 4)

        assert AuthenticatedUser(**user).tenant_id == expected

    @pytest.mark.asyncio
    async def test_a_tier_dict_and_none_both_survive(self) -> None:
        from src.app.api.deps.auth import _load_and_validate_user

        cache_patch, _, _ = _patch_directory(cached=_cache_payload())
        with cache_patch:
            with_tier = AuthenticatedUser(**(await _load_and_validate_user(_make_db(), "test@example.com", 4)))

        none_patch, _, _ = _patch_directory(cached=_cache_payload(tier=None, tier_id=None))
        with none_patch:
            without_tier = AuthenticatedUser(**(await _load_and_validate_user(_make_db(), "test@example.com", 4)))

        assert with_tier.tier is not None and with_tier.tier["name"] == "Free"
        assert without_tier.tier is None

    @pytest.mark.asyncio
    async def test_extra_payload_keys_never_reach_the_principal(self) -> None:
        """``hashed_password`` travels in the payload; it must not ride along."""
        from src.app.api.deps.auth import _load_and_validate_user

        cache_patch, _, _ = _patch_directory(cached=_cache_payload())
        with cache_patch:
            user = await _load_and_validate_user(_make_db(), "test@example.com", 4)

        principal = AuthenticatedUser(**user)
        assert not hasattr(principal, "hashed_password")
        assert "hashed_password" not in principal.model_dump()

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("payload_overrides", "payload_version", "reason"),
        [
            ({"is_active": False}, 4, DenialReason.INACTIVE_USER),
            ({"is_deleted": True}, 4, DenialReason.DELETED_USER),
            ({}, 99, DenialReason.STALE_TOKEN_VERSION),
        ],
    )
    async def test_the_liveness_and_version_gates_still_read_the_raw_payload(
        self, payload_overrides: dict, payload_version: int, reason: DenialReason
    ) -> None:
        """The raw payload is data, not a bypass: same gates, same verdicts."""
        from src.app.api.deps.auth import _load_and_validate_user

        cache_patch, _, _ = _patch_directory(cached=_cache_payload(**payload_overrides))
        with cache_patch:
            with pytest.raises(UnauthorizedException) as exc_info:
                await _load_and_validate_user(_make_db(), "test@example.com", payload_version)

        # The SPECIFIC verdict, not merely "something was refused".
        assert str(exc_info.value.detail) == DENIAL_TABLE[reason].message


class TestTenantIdTypeIsStableAcrossPayloadSources:
    """The principal's ``tenant_id`` TYPE must not depend on which tier answered.

    ``AuthenticatedUser.tenant_id`` is ``UUID | str | int | None``. The union is
    load-bearing — ``enforce_superuser_tenant_rule`` deliberately compares the
    RAW value and supports legacy int / slug ids, pinned by
    ``tests/unit/core/rbac/test_superuser_tenant_rule.py``. But it means the field
    can hold four types, and a comparison like
    ``Model.tenant_id == current_user.tenant_id`` binds only for the UUID half.

    The trap is real in one direction and was closed by the before-validator: the
    auth cache stores JSON, so a cached payload carries the tenant as a STRING
    while the DB row carries a ``UUID``. These tests pin that both now produce a
    UUID, so a future change cannot silently reintroduce the split.
    """

    def test_db_and_cached_payloads_yield_the_same_tenant_type(self) -> None:
        db_principal = AuthenticatedUser(**_db_payload())
        cached_principal = AuthenticatedUser(**_cache_payload())

        assert isinstance(db_principal.tenant_id, UUID)
        assert isinstance(cached_principal.tenant_id, UUID)
        assert type(db_principal.tenant_id) is type(cached_principal.tenant_id)
        assert db_principal.tenant_id == cached_principal.tenant_id

    def test_the_api_key_path_also_yields_a_uuid(self) -> None:
        """``UserRead.model_dump()`` hands over a UUID; the validator must keep it."""
        from src.app.modules.users.schemas import UserRead

        payload = _db_payload()
        payload.pop("hashed_password", None)
        payload.pop("token_version", None)
        principal = AuthenticatedUser(**UserRead.model_validate(payload).model_dump())

        assert isinstance(principal.tenant_id, UUID)

    @pytest.mark.parametrize("legacy", [7, "acme-corp"])
    def test_non_uuid_tenant_ids_still_pass_through_unchanged(self, legacy: object) -> None:
        """Anti-over-fix: the union exists so legacy int/slug tenants keep working.

        Narrowing the field to ``UUID`` would break tenants the tenant-binding
        rule explicitly supports, so the validator parses UUID-SHAPED values only.
        """
        principal = AuthenticatedUser(id=1, email="u@example.com", tenant_id=legacy)  # type: ignore[arg-type]

        assert principal.tenant_id == legacy
        assert type(principal.tenant_id) is type(legacy)

    def test_an_absent_tenant_stays_none(self) -> None:
        """A genuinely tenant-less principal must not be coerced into a value."""
        assert AuthenticatedUser(id=1, email="u@example.com").tenant_id is None
