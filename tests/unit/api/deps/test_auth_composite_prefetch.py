"""Unit tests for P1-1 Composite Auth-Prefetch optimization."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.core.rbac.bitmask import PermissionBitmask
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.core.security.auth_prefetch import execute_composite_auth_prefetch
from src.app.shared.exceptions import ForbiddenError
from src.app.shared.exceptions import UnauthorizedError as UnauthorizedException


def _dumpable(payload):
    """Wrap a fixture dict so ``.model_dump()`` returns it (directory returns models)."""
    if payload is None:
        return None
    m = MagicMock()
    m.model_dump.return_value = payload
    return m


def _session_returning(slugs: list[str]) -> AsyncMock:
    """Shape a db so ``execute`` models ``AsyncSession.execute``.

    ``await db.execute(stmt)`` returns a ``Result`` whose ``.scalars().all()``
    is SYNCHRONOUS. A bare ``AsyncMock()`` db breaks that: every attribute of
    an unspeced ``AsyncMock`` is itself an ``AsyncMock``, so ``.scalars()``
    returns a coroutine and ``.all()`` lands on it
    (``AttributeError: 'coroutine' object has no attribute 'all'``).
    """
    result = MagicMock()
    result.scalars.return_value.all.return_value = slugs
    db = AsyncMock()
    db.execute.return_value = result
    return db



class TestCompositeAuthPrefetch:
    """Test suite for composite auth prefetch and sequential fallbacks."""

    @pytest.mark.asyncio
    async def test_composite_prefetch_all_valid(self) -> None:
        """Test composite prefetch when token is valid, session is active, and bitmask is cached."""
        mock_redis = AsyncMock()
        mock_lua = MagicMock()
        # [err=0, is_blacklisted=0, session_valid=1, bitmask="15"]
        mock_lua.execute = AsyncMock(return_value=[0, 0, 1, b"15"])

        with patch("src.app.core.security.auth_prefetch.get_lua_manager", return_value=mock_lua):
            is_blacklisted, session_valid, bitmask = await execute_composite_auth_prefetch(
                token="valid_token",
                jti="jti-123",
                user_id=42,
                session_id="sid-456",
                tenant_id=uuid4(),
                check_bitmask=True,
                redis_client=mock_redis,
            )

        assert is_blacklisted is False
        assert session_valid is True
        assert bitmask == 15
        mock_lua.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_composite_prefetch_blacklisted_token(self) -> None:
        """Test composite prefetch returns is_blacklisted=True when token is revoked."""
        mock_redis = AsyncMock()
        mock_lua = MagicMock()
        # [err=0, is_blacklisted=1, session_valid=1, bitmask=False]
        mock_lua.execute = AsyncMock(return_value=[0, 1, 1, False])

        with patch("src.app.core.security.auth_prefetch.get_lua_manager", return_value=mock_lua):
            is_blacklisted, session_valid, bitmask = await execute_composite_auth_prefetch(
                token="revoked_token",
                jti="jti-revoked",
                user_id=42,
                session_id="sid-456",
                tenant_id=uuid4(),
                check_bitmask=True,
                redis_client=mock_redis,
            )

        assert is_blacklisted is True
        assert session_valid is True
        assert bitmask is None

    @pytest.mark.asyncio
    async def test_composite_prefetch_session_revoked(self) -> None:
        """Test composite prefetch returns session_valid=False when session is expired/revoked."""
        mock_redis = AsyncMock()
        mock_lua = MagicMock()
        # [err=0, is_blacklisted=0, session_valid=0, bitmask="7"]
        mock_lua.execute = AsyncMock(return_value=[0, 0, 0, b"7"])

        with patch("src.app.core.security.auth_prefetch.get_lua_manager", return_value=mock_lua):
            is_blacklisted, session_valid, bitmask = await execute_composite_auth_prefetch(
                token="valid_token",
                jti="jti-123",
                user_id=42,
                session_id="sid-revoked",
                tenant_id=uuid4(),
                check_bitmask=True,
                redis_client=mock_redis,
            )

        assert is_blacklisted is False
        assert session_valid is False
        assert bitmask == 7

    @pytest.mark.asyncio
    async def test_composite_prefetch_error_returns_none_tri_state(self) -> None:
        """Test composite prefetch returns (None, None, None) on Redis/Lua error for sequential fallback."""
        mock_redis = AsyncMock()
        mock_lua = MagicMock()
        mock_lua.execute = AsyncMock(side_effect=Exception("Redis connection refused"))

        with patch("src.app.core.security.auth_prefetch.get_lua_manager", return_value=mock_lua):
            is_blacklisted, session_valid, bitmask = await execute_composite_auth_prefetch(
                token="valid_token",
                jti="jti-123",
                user_id=42,
                session_id="sid-456",
                tenant_id=uuid4(),
                check_bitmask=True,
                redis_client=mock_redis,
            )

        assert is_blacklisted is None
        assert session_valid is None
        assert bitmask is None

    @pytest.mark.asyncio
    async def test_requires_permission_uses_prefetched_bitmask(self) -> None:
        """Test requires_permission consumes request.state.cached_permissions_bitmask with 0 RTTs."""
        tenant_id = uuid4()
        user_id = 42

        # Create mask that includes USERS_READ
        mask = PermissionBitmask.encode({Permission.USERS_READ.value})

        # Set up mock request with pre-fetched bitmask
        mock_request = MagicMock()
        mock_request.state.cached_permissions_bitmask = (user_id, str(tenant_id), mask)

        mock_user = {"id": user_id, "tenant_id": tenant_id, "is_superuser": False}
        mock_tenant = {"id": tenant_id, "slug": "test-tenant"}

        from tests.helpers.auth import build_test_auth_context

        mock_auth_context = build_test_auth_context(mock_user, mock_tenant)

        checker = requires_permission(Permission.USERS_READ)
        mock_db = AsyncMock()

        with patch("src.app.core.rbac.dependencies.has_permission_cached") as mock_has_perm:
            # Execute dependency check
            await checker(
                request=mock_request,
                db=mock_db,
                auth_context=mock_auth_context,
            )
            # has_permission_cached should NOT be called because bitmask was in request.state
            mock_has_perm.assert_not_called()


    @pytest.mark.asyncio
    async def test_get_auth_context_revoked_token_via_prefetch(self) -> None:
        """Test get_auth_context raises UnauthorizedException when prefetch detects revoked token."""
        from src.app.api.deps.auth import get_auth_context

        mock_request = MagicMock()
        mock_request.state = MagicMock()
        mock_request.state.is_token_blacklisted = None

        mock_payload = {
            "sub": "testuser",
            "token_type": "access",
            "token_version": 1,
            "jti": "jti-revoked",
            "sid": "sid-123",
            "exp": 9999999999,
        }
        mock_user = {
            "id": 10,
            "username": "testuser",
            "is_active": True,
            "is_deleted": False,
            "token_version": 1,
            "tenant_id": uuid4(),
        }

        with (
            patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=mock_payload),
            patch(
                "src.app.modules.users.directory.UserDirectory.auth_info",
                new=AsyncMock(return_value=_dumpable(mock_user)),
            ),
            patch(
                "src.app.core.security.auth_prefetch.execute_composite_auth_prefetch",
                AsyncMock(return_value=(True, True, None)),
            ),
        ):
            with pytest.raises(UnauthorizedException) as exc_info:
                await get_auth_context(request=mock_request, token="sample_token")

            assert "revoked" in str(exc_info.value.detail).lower()
    @pytest.mark.asyncio
    async def test_get_auth_context_revoked_session_via_prefetch(self) -> None:
        """Test get_auth_context raises UnauthorizedException when prefetch detects revoked session."""
        from src.app.api.deps.auth import get_auth_context

        mock_request = MagicMock()
        mock_request.state = MagicMock()
        mock_request.state.is_token_blacklisted = None

        mock_payload = {
            "sub": "testuser",
            "token_type": "access",
            "token_version": 1,
            "jti": "jti-valid",
            "sid": "sid-revoked",
            "exp": 9999999999,
        }
        mock_user = {
            "id": 10,
            "username": "testuser",
            "is_active": True,
            "is_deleted": False,
            "token_version": 1,
            "tenant_id": uuid4(),
        }

        with (
            patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=mock_payload),
            patch(
                "src.app.modules.users.directory.UserDirectory.auth_info",
                new=AsyncMock(return_value=_dumpable(mock_user)),
            ),
            patch(
                "src.app.core.security.auth_prefetch.execute_composite_auth_prefetch",
                AsyncMock(return_value=(False, False, None)),
            ),
        ):
            with pytest.raises(UnauthorizedException) as exc_info:
                await get_auth_context(request=mock_request, token="sample_token")

            assert "revoked" in str(exc_info.value.detail).lower()
    async def test_requires_permission_denied_with_prefetched_bitmask(self) -> None:
        """Test requires_permission raises 403 when pre-fetched bitmask lacks requested permission."""
        tenant_id = uuid4()
        user_id = 42

        from tests.helpers.auth import build_test_auth_context

        mock_auth_context = build_test_auth_context(
            {"id": user_id, "tenant_id": tenant_id, "is_superuser": False},
            {"id": tenant_id, "slug": "test-tenant"},
        )

        # Prefetched mask includes USERS_READ only — ROLES_MANAGE denied with 0 RTTs.
        mask = PermissionBitmask.encode({Permission.USERS_READ.value})

        checker = requires_permission(Permission.ROLES_MANAGE)
        mock_db = _session_returning([])
        mock_request = MagicMock()
        mock_request.state.cached_permissions_bitmask = (user_id, str(tenant_id), mask)
        with pytest.raises(ForbiddenError) as exc_info:
            await checker(
                request=mock_request,
                db=mock_db,
                auth_context=mock_auth_context,
            )

        assert "roles:manage" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_requires_permission_allowed_by_db_when_prefetch_denies(self) -> None:
        """A prefetched mask that denies is NOT a denial: the DB-backed path grants roles:manage.

        The other half of the fallback regression test — the composite prefetch
        reads the bitmask key and yields 0 on a cache MISS, so a prefetched
        mask that denies must be confirmed against the DB before rejecting.
        Here the DB grants the role, so no exception may be raised.
        """
        tenant_id = uuid4()
        user_id = 42

        from tests.helpers.auth import build_test_auth_context

        mock_auth_context = build_test_auth_context(
            {"id": user_id, "tenant_id": tenant_id, "is_superuser": False},
            {"id": tenant_id, "slug": "test-tenant"},
        )

        # Prefetched mask includes USERS_READ only — ROLES_MANAGE denied at 0 RTTs...
        mask = PermissionBitmask.encode({Permission.USERS_READ.value})
        # ...but the DB-backed confirmation grants roles:manage.
        mock_db = _session_returning([Permission.ROLES_MANAGE.value])

        checker = requires_permission(Permission.ROLES_MANAGE)
        mock_request = MagicMock()
        mock_request.state.cached_permissions_bitmask = (user_id, str(tenant_id), mask)

        await checker(
            request=mock_request,
            db=mock_db,
            auth_context=mock_auth_context,
        )
