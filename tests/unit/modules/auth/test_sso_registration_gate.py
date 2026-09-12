"""P1-4: SSO callback enforces the S-M2 closed-registration gate.

Parity with ``POST /account/register``: while AUTH_OPEN_REGISTRATION is
False, a new SSO user may only bootstrap a tenant's FIRST account. Joining
an existing tenant via IdP-validated email requires an admin-created
account (else anyone could join any tenant by prior IdP signup).
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import Request

from src.app.modules.auth.schemas import SSOProvider
from src.app.modules.auth.services.sso import SSOService
from src.app.shared.exceptions import ForbiddenError


@pytest.fixture
def service():
    return SSOService()


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_request():
    request = MagicMock(spec=Request)
    request.base_url = "http://testserver/"
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = {"user-agent": "test-agent"}
    return request


@pytest.fixture(autouse=True)
def mock_mfa_service_dependency():
    with patch("src.app.modules.auth.services.mfa.get_mfa_service") as mock_get_mfa:
        mock_mfa = AsyncMock()
        mock_mfa.is_mfa_enabled = AsyncMock(return_value=False)
        mock_get_mfa.return_value = mock_mfa
        yield mock_get_mfa


@pytest.fixture
def gate_closed():
    from src.app.core.config import settings

    original = settings.auth.AUTH_OPEN_REGISTRATION
    settings.auth.AUTH_OPEN_REGISTRATION = False
    yield
    settings.auth.AUTH_OPEN_REGISTRATION = original


@pytest.fixture
def gate_open():
    from src.app.core.config import settings

    original = settings.auth.AUTH_OPEN_REGISTRATION
    settings.auth.AUTH_OPEN_REGISTRATION = True
    yield
    settings.auth.AUTH_OPEN_REGISTRATION = original


def _mock_tenant():
    tenant = MagicMock()
    tenant.id = uuid4()
    tenant.slug = "gated-tenant"
    tenant.is_active = True
    return tenant


class TestRegistrationGateHelper:
    """The shared helper answers the S-M2 question identically for both flows."""

    async def _allowed(self, mock_db, gate_open_flag, tenant, tenant_user_row):
        from src.app.core.config import settings

        original = settings.auth.AUTH_OPEN_REGISTRATION
        settings.auth.AUTH_OPEN_REGISTRATION = gate_open_flag
        try:
            from src.app.modules.auth.services import registration_gate

            mock_db.scalar = AsyncMock(return_value=tenant_user_row)
            with patch.object(registration_gate, "get_current_tenant", return_value=tenant):
                return await registration_gate.is_self_registration_allowed(mock_db)
        finally:
            settings.auth.AUTH_OPEN_REGISTRATION = original

    async def test_open_registration_allows_everything(self, mock_db):
        assert await self._allowed(mock_db, True, _mock_tenant(), tenant_user_row=1) is True

    async def test_closed_gate_rejects_populated_tenant(self, mock_db):
        assert await self._allowed(mock_db, False, _mock_tenant(), tenant_user_row=1) is False

    async def test_closed_gate_allows_first_user_bootstrap(self, mock_db):
        assert await self._allowed(mock_db, False, _mock_tenant(), tenant_user_row=None) is True

    async def test_closed_gate_falls_through_without_tenant(self, mock_db):
        # NO_TENANT parity with /register: the service path owns the failure.
        assert await self._allowed(mock_db, False, None, tenant_user_row=1) is True

    async def test_closed_gate_falls_through_for_inactive_tenant(self, mock_db):
        tenant = _mock_tenant()
        tenant.is_active = False
        assert await self._allowed(mock_db, False, tenant, tenant_user_row=1) is True

    async def test_closed_gate_counts_via_tenant_scope_seam(self, mock_db):
        """P2-2: the gate counts through tenant_scope(User, tenant.id), not a hand-rolled filter."""
        from src.app.core.config import settings
        from src.app.modules.auth.services import registration_gate
        from src.app.modules.users.models import User

        original = settings.auth.AUTH_OPEN_REGISTRATION
        settings.auth.AUTH_OPEN_REGISTRATION = False
        try:
            mock_db.scalar = AsyncMock(return_value=1)
            tenant = _mock_tenant()
            with (
                patch.object(registration_gate, "get_current_tenant", return_value=tenant),
                patch.object(registration_gate, "tenant_scope", return_value=True) as mock_scope,
            ):
                assert await registration_gate.is_self_registration_allowed(mock_db) is False
            mock_scope.assert_called_once_with(User, tenant.id)
        finally:
            settings.auth.AUTH_OPEN_REGISTRATION = original


def _drive_new_user_callback_patches(mock_get_manager, mock_get_provider, mock_crud_sso, mock_user_service_cls):
    """Common stub stack driving process_callback into the new-user branch."""
    mock_manager = AsyncMock()
    mock_manager.consume.return_value = {
        "provider": SSOProvider.GOOGLE.value,
        "redirect_uri": "http://test",
        "extra_data": {},
    }
    mock_get_manager.return_value = mock_manager

    mock_provider_instance = AsyncMock()
    mock_provider_instance.exchange_code_for_token.return_value = {"access_token": "acc_tok"}
    mock_user_info = MagicMock()
    mock_user_info.email = "new-sso@example.com"
    mock_user_info.name = "New SSO"
    mock_user_info.provider_user_id = "gov_new"
    mock_user_info.email_verified = True
    mock_provider_instance.get_user_info.return_value = mock_user_info
    mock_get_provider.return_value = mock_provider_instance

    # No existing SSO connection and no existing user -> creation branch.
    mock_crud_sso.get_by_provider_user_id = AsyncMock(return_value=None)
    mock_crud_sso.get_by_criteria = AsyncMock(return_value=None)

    mock_user_svc = MagicMock()
    mock_user_svc.get_by_email = AsyncMock(return_value=None)
    mock_user_svc.get_user_auth_info = AsyncMock(
        return_value={
            "id": 7,
            "email": "new-sso@example.com",
            "username": "newsso",
            "tenant_id": uuid4(),
            "tier": {"name": "free"},
            "tier_id": 1,
            "token_version": 1,
            "is_active": True,
        }
    )
    mock_user_service_cls.return_value = mock_user_svc
    return mock_user_info


@pytest.mark.asyncio
@patch("src.app.modules.auth.services.sso.get_sso_state_manager")
@patch("src.app.core.utils.cache.client")
@patch("src.app.modules.auth.services.sso_settings.sso_settings_service.get_decrypted_settings")
@patch.object(SSOService, "_get_provider_instance")
@patch("src.app.modules.auth.services.sso.crud_user_sso_connections")
@patch("src.app.modules.auth.services.sso.UserService")
@patch("src.app.modules.auth.services.tokens.create_access_token")
@patch("src.app.modules.auth.services.tokens.create_refresh_token")
async def test_sso_callback_rejected_for_existing_tenant(
    mock_create_refresh,
    mock_create_access,
    mock_user_service_cls,
    mock_crud_sso,
    mock_get_provider,
    mock_get_settings,
    mock_redis,
    mock_get_manager,
    service,
    mock_db,
    mock_request,
    gate_closed,
):
    """Gate closed + tenant already has users -> ForbiddenError, no user created."""
    _drive_new_user_callback_patches(mock_get_manager, mock_get_provider, mock_crud_sso, mock_user_service_cls)
    mock_create_access.return_value = "jwt_access"
    mock_create_refresh.return_value = "jwt_refresh"
    mock_db.scalar = AsyncMock(return_value=1)

    with (
        patch(
            "src.app.modules.auth.services.registration_gate.get_current_tenant", return_value=_mock_tenant()
        ),
        patch("src.app.modules.auth.services.sso.get_current_tenant", return_value=_mock_tenant()),
        patch.object(SSOService, "_create_sso_user", new=AsyncMock()) as mock_create_user,
    ):
        with pytest.raises(ForbiddenError, match="Registration is closed"):
            await service.process_callback(mock_db, mock_request, SSOProvider.GOOGLE, "code", "state")
        mock_create_user.assert_not_awaited()


@pytest.mark.asyncio
@patch("src.app.modules.auth.services.sso.get_sso_state_manager")
@patch("src.app.core.utils.cache.client")
@patch("src.app.modules.auth.services.sso_settings.sso_settings_service.get_decrypted_settings")
@patch.object(SSOService, "_get_provider_instance")
@patch("src.app.modules.auth.services.sso.crud_user_sso_connections")
@patch("src.app.modules.auth.services.sso.UserService")
@patch("src.app.modules.auth.services.tokens.create_access_token")
@patch("src.app.modules.auth.services.tokens.create_refresh_token")
async def test_sso_callback_allowed_for_first_user_bootstrap(
    mock_create_refresh,
    mock_create_access,
    mock_user_service_cls,
    mock_crud_sso,
    mock_get_provider,
    mock_get_settings,
    mock_redis,
    mock_get_manager,
    service,
    mock_db,
    mock_request,
    gate_closed,
):
    """Gate closed but tenant has NO users -> bootstrap allowed."""
    _drive_new_user_callback_patches(mock_get_manager, mock_get_provider, mock_crud_sso, mock_user_service_cls)
    mock_create_access.return_value = "jwt_access"
    mock_create_refresh.return_value = "jwt_refresh"
    mock_db.scalar = AsyncMock(return_value=None)

    created = {
        "id": 7,
        "email": "new-sso@example.com",
        "username": "newsso",
        "tenant_id": uuid4(),
        "is_active": True,
        "is_deleted": False,
    }

    with (
        patch(
            "src.app.modules.auth.services.registration_gate.get_current_tenant", return_value=_mock_tenant()
        ),
        patch("src.app.modules.auth.services.sso.get_current_tenant", return_value=_mock_tenant()),
        patch.object(SSOService, "_create_sso_user", new=AsyncMock(return_value=created)) as mock_create_user,
        patch.object(SSOService, "_link_account", new=AsyncMock()),
    ):
        response = await service.process_callback(mock_db, mock_request, SSOProvider.GOOGLE, "code", "state")

    mock_create_user.assert_awaited_once()
    assert response.access_token == "jwt_access"


@pytest.mark.asyncio
@patch("src.app.modules.auth.services.sso.get_sso_state_manager")
@patch("src.app.core.utils.cache.client")
@patch("src.app.modules.auth.services.sso_settings.sso_settings_service.get_decrypted_settings")
@patch.object(SSOService, "_get_provider_instance")
@patch("src.app.modules.auth.services.sso.crud_user_sso_connections")
@patch("src.app.modules.auth.services.sso.UserService")
@patch("src.app.modules.auth.services.tokens.create_access_token")
@patch("src.app.modules.auth.services.tokens.create_refresh_token")
async def test_sso_callback_allowed_when_gate_open(
    mock_create_refresh,
    mock_create_access,
    mock_user_service_cls,
    mock_crud_sso,
    mock_get_provider,
    mock_get_settings,
    mock_redis,
    mock_get_manager,
    service,
    mock_db,
    mock_request,
    gate_open,
):
    """AUTH_OPEN_REGISTRATION=True restores open signup via SSO too."""
    _drive_new_user_callback_patches(mock_get_manager, mock_get_provider, mock_crud_sso, mock_user_service_cls)
    mock_create_access.return_value = "jwt_access"
    mock_create_refresh.return_value = "jwt_refresh"
    # Even a populated tenant is accepted when the gate is open.
    mock_db.scalar = AsyncMock(return_value=1)

    created = {
        "id": 7,
        "email": "new-sso@example.com",
        "username": "newsso",
        "tenant_id": uuid4(),
        "is_active": True,
        "is_deleted": False,
    }

    with (
        patch(
            "src.app.modules.auth.services.registration_gate.get_current_tenant", return_value=_mock_tenant()
        ),
        patch("src.app.modules.auth.services.sso.get_current_tenant", return_value=_mock_tenant()),
        patch.object(SSOService, "_create_sso_user", new=AsyncMock(return_value=created)) as mock_create_user,
        patch.object(SSOService, "_link_account", new=AsyncMock()),
    ):
        response = await service.process_callback(mock_db, mock_request, SSOProvider.GOOGLE, "code", "state")

    mock_create_user.assert_awaited_once()
    assert response.access_token == "jwt_access"
