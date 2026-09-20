"""Adversarial validation tests for get_optional_auth_context dependency."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps.auth import (
    AuthContext,
    get_optional_auth_context,
)
from src.app.core.schemas import TokenData
from src.app.core.tenancy.context import TenantContext
from src.app.shared.exceptions import ForbiddenError, UnauthorizedError


@pytest.fixture
def mock_request():
    """Mock FastAPI Request."""
    request = MagicMock(spec=Request)
    request.headers = {}
    return request


@pytest.fixture
def mock_db():
    """Mock database session."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def mock_tenant():
    """Mock Tenant."""
    return TenantContext(
        tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        tenant_slug="test-tenant",
        tenant_name="Test Tenant",
        is_active=True,
    )


@pytest.mark.asyncio
async def test_case_1_valid_jwt_token(mock_request, mock_db, mock_tenant):
    """1. Valid JWT token: User must be parsed correctly."""
    mock_request.headers = {"Authorization": "Bearer valid_token_123"}

    user_dict = {
        "id": 123,
        "email": "test@example.com",
        "username": "testuser",
        "is_active": True,
        "is_deleted": False,
        "tenant_id": mock_tenant.id,
        "tier_id": 1,
        "token_version": 1,
    }

    with (
        patch("src.app.api.deps.auth.verify_token") as mock_verify,
        patch("src.app.api.deps.auth.UserService") as MockUserService,
        patch("src.app.api.deps.auth.get_current_tenant") as mock_get_tenant,
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
    ):
        mock_verify.return_value = TokenData(username_or_email="test@example.com", token_version=1)
        mock_get_tenant.return_value = mock_tenant

        mock_user_service = AsyncMock()
        mock_user_service.get_user_auth_info.return_value = user_dict
        MockUserService.return_value = mock_user_service

        result = await get_optional_auth_context(mock_request, db=mock_db)

        assert isinstance(result, AuthContext)
        assert result.user.id == 123
        assert result.user.email == "test@example.com"
        assert result.tenant == mock_tenant


@pytest.mark.asyncio
async def test_case_2_suspended_user_token(mock_request, mock_db, mock_tenant):
    """2. Suspended user token: Request must be rejected with 401/403."""
    mock_request.headers = {"Authorization": "Bearer suspended_token"}

    user_dict = {
        "id": 123,
        "email": "suspended@example.com",
        "username": "suspended",
        "is_active": False,  # Suspended
        "is_deleted": False,
        "tenant_id": mock_tenant.id,
        "tier_id": 1,
        "token_version": 1,
    }

    with (
        patch("src.app.api.deps.auth.verify_token") as mock_verify,
        patch("src.app.api.deps.auth.UserService") as MockUserService,
        patch("src.app.api.deps.auth.get_current_tenant") as mock_get_tenant,
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
    ):
        mock_verify.return_value = MagicMock(username_or_email="suspended@example.com", token_version=1)
        mock_get_tenant.return_value = mock_tenant

        mock_user_service = AsyncMock()
        mock_user_service.get_user_auth_info.return_value = user_dict
        MockUserService.return_value = mock_user_service

        with pytest.raises((UnauthorizedError, ForbiddenError)):
            await get_optional_auth_context(mock_request, db=mock_db)


@pytest.mark.asyncio
async def test_case_3_deleted_user_token(mock_request, mock_db, mock_tenant):
    """3. Deleted user token: Request must be rejected with 401/403."""
    mock_request.headers = {"Authorization": "Bearer deleted_token"}

    user_dict = {
        "id": 123,
        "email": "deleted@example.com",
        "username": "deleted",
        "is_active": True,
        "is_deleted": True,  # Deleted
        "tenant_id": mock_tenant.id,
        "tier_id": 1,
        "token_version": 1,
    }

    with (
        patch("src.app.api.deps.auth.verify_token") as mock_verify,
        patch("src.app.api.deps.auth.UserService") as MockUserService,
        patch("src.app.api.deps.auth.get_current_tenant") as mock_get_tenant,
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
    ):
        mock_verify.return_value = MagicMock(username_or_email="deleted@example.com", token_version=1)
        mock_get_tenant.return_value = mock_tenant

        mock_user_service = AsyncMock()
        mock_user_service.get_user_auth_info.return_value = user_dict
        MockUserService.return_value = mock_user_service

        with pytest.raises((UnauthorizedError, ForbiddenError)):
            await get_optional_auth_context(mock_request, db=mock_db)


@pytest.mark.asyncio
async def test_case_4_expired_token_version(mock_request, mock_db, mock_tenant):
    """4. Expired token version: Request must be rejected with 401/403."""
    mock_request.headers = {"Authorization": "Bearer expired_version_token"}

    user_dict = {
        "id": 123,
        "email": "expired@example.com",
        "username": "expired",
        "is_active": True,
        "is_deleted": False,
        "tenant_id": mock_tenant.id,
        "tier_id": 1,
        "token_version": 2,  # Database has newer version
    }

    with (
        patch("src.app.api.deps.auth.verify_token") as mock_verify,
        patch("src.app.api.deps.auth.UserService") as MockUserService,
        patch("src.app.api.deps.auth.get_current_tenant") as mock_get_tenant,
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
    ):
        # Token carries version 1
        mock_verify.return_value = MagicMock(username_or_email="expired@example.com", token_version=1)
        mock_get_tenant.return_value = mock_tenant

        mock_user_service = AsyncMock()
        mock_user_service.get_user_auth_info.return_value = user_dict
        MockUserService.return_value = mock_user_service

        with pytest.raises((UnauthorizedError, ForbiddenError)):
            await get_optional_auth_context(mock_request, db=mock_db)


@pytest.mark.asyncio
async def test_case_5_tenant_mismatch(mock_request, mock_db, mock_tenant):
    """5. Tenant mismatch: Request must be rejected with 403."""
    mock_request.headers = {"Authorization": "Bearer tenant_mismatch_token"}

    user_dict = {
        "id": 123,
        "email": "mismatch@example.com",
        "username": "mismatch",
        "is_active": True,
        "is_deleted": False,
        "tenant_id": UUID("00000000-0000-0000-0000-000000000002"),  # Different tenant
        "tier_id": 1,
        "token_version": 1,
    }

    with (
        patch("src.app.api.deps.auth.verify_token") as mock_verify,
        patch("src.app.api.deps.auth.UserService") as MockUserService,
        patch("src.app.api.deps.auth.get_current_tenant") as mock_get_tenant,
        patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=None),
    ):
        mock_verify.return_value = MagicMock(username_or_email="mismatch@example.com", token_version=1)
        mock_get_tenant.return_value = mock_tenant

        mock_user_service = AsyncMock()
        mock_user_service.get_user_auth_info.return_value = user_dict
        MockUserService.return_value = mock_user_service

        with pytest.raises((UnauthorizedError, ForbiddenError)):
            await get_optional_auth_context(mock_request, db=mock_db)
