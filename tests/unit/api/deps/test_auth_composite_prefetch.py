"""Unit tests for P1-1 Composite Auth-Prefetch optimization."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.app.core.exceptions.http_exceptions import UnauthorizedException
from src.app.core.rbac.bitmask import PermissionBitmask
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.core.security.auth_prefetch import execute_composite_auth_prefetch


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

        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.tenant_id = tenant_id
        mock_user.is_superuser = False

        mock_tenant = MagicMock()
        mock_tenant.id = tenant_id

        mock_auth_context = MagicMock()
        from src.app.api.deps import AuthContext

        # Mock isinstance(auth_context, AuthContext)
        mock_auth_context = MagicMock(spec=AuthContext)
        mock_auth_context.user = mock_user
        mock_auth_context.tenant = mock_tenant
        mock_auth_context.is_dependency_override = False

        checker = requires_permission(Permission.USERS_READ)
        mock_db = AsyncMock()

        with patch("src.app.core.rbac.dependencies.has_permission_cached") as mock_has_perm:
            # Execute dependency check
            await checker(
                request=mock_request,
                token="fake_token",
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
        mock_db = AsyncMock()

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
            patch("src.app.modules.users.service.UserService.get_user_auth_info", AsyncMock(return_value=mock_user)),
            patch(
                "src.app.core.security.auth_prefetch.execute_composite_auth_prefetch",
                AsyncMock(return_value=(True, True, None)),
            ),
        ):
            with pytest.raises(UnauthorizedException) as exc_info:
                await get_auth_context(request=mock_request, db=mock_db, token="sample_token")

            assert "revoked" in str(exc_info.value.detail).lower()
    @pytest.mark.asyncio
    async def test_get_auth_context_revoked_session_via_prefetch(self) -> None:
        """Test get_auth_context raises UnauthorizedException when prefetch detects revoked session."""
        from src.app.api.deps.auth import get_auth_context

        mock_request = MagicMock()
        mock_request.state = MagicMock()
        mock_request.state.is_token_blacklisted = None
        mock_db = AsyncMock()

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
            patch("src.app.modules.users.service.UserService.get_user_auth_info", AsyncMock(return_value=mock_user)),
            patch(
                "src.app.core.security.auth_prefetch.execute_composite_auth_prefetch",
                AsyncMock(return_value=(False, False, None)),
            ),
        ):
            with pytest.raises(UnauthorizedException) as exc_info:
                await get_auth_context(request=mock_request, db=mock_db, token="sample_token")

            assert "revoked" in str(exc_info.value.detail).lower()
    async def test_requires_permission_denied_with_prefetched_bitmask(self) -> None:
        """Test requires_permission raises 403 when pre-fetched bitmask lacks requested permission."""
        tenant_id = uuid4()
        user_id = 42

        # Create mask that only includes USERS_READ, not ROLES_MANAGE
        mask = PermissionBitmask.encode({Permission.USERS_READ.value})

        mock_request = MagicMock()
        mock_request.state.cached_permissions_bitmask = (user_id, str(tenant_id), mask)

        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.tenant_id = tenant_id
        mock_user.is_superuser = False

        mock_tenant = MagicMock()
        mock_tenant.id = tenant_id

        from src.app.api.deps import AuthContext

        mock_auth_context = MagicMock(spec=AuthContext)
        mock_auth_context.user = mock_user
        mock_auth_context.tenant = mock_tenant
        mock_auth_context.is_dependency_override = False

        checker = requires_permission(Permission.ROLES_MANAGE)
        mock_db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await checker(
                request=mock_request,
                token="fake_token",
                db=mock_db,
                auth_context=mock_auth_context,
            )

        assert exc_info.value.status_code == 403
