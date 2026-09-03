from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import Request

from src.app.core.exceptions.http_exceptions import BadRequestException
from src.app.modules.auth.providers.apple import AppleProvider
from src.app.modules.auth.schemas import SSOProvider
from src.app.modules.auth.services.sso import SSOService


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


@pytest.mark.asyncio
@patch("src.app.modules.auth.services.sso.get_sso_state_manager")
@patch("src.app.core.utils.cache.client")
async def test_process_callback_invalid_state(mock_redis, mock_get_manager, service, mock_db, mock_request):
    mock_manager = AsyncMock()
    mock_manager.consume.return_value = None
    mock_get_manager.return_value = mock_manager

    with pytest.raises(BadRequestException, match="Invalid or expired state parameter"):
        await service.process_callback(mock_db, mock_request, SSOProvider.GOOGLE, "code123", "state123")


@pytest.mark.asyncio
@patch("src.app.modules.auth.services.sso.get_sso_state_manager")
@patch("src.app.core.utils.cache.client")
async def test_process_callback_provider_mismatch(mock_redis, mock_get_manager, service, mock_db, mock_request):
    mock_manager = AsyncMock()
    mock_manager.consume.return_value = {"provider": "github"}
    mock_get_manager.return_value = mock_manager

    with pytest.raises(BadRequestException, match="State provider mismatch"):
        await service.process_callback(mock_db, mock_request, SSOProvider.GOOGLE, "code123", "state123")


@pytest.mark.asyncio
@patch("src.app.modules.auth.services.sso.get_sso_state_manager")
@patch("src.app.core.utils.cache.client")
@patch("src.app.modules.auth.services.sso_settings.sso_settings_service.get_decrypted_settings")
@patch.object(SSOService, "_get_provider_instance")
async def test_process_callback_exchange_failed(
    mock_get_provider, mock_get_settings, mock_redis, mock_get_manager, service, mock_db, mock_request
):
    mock_manager = AsyncMock()
    mock_manager.consume.return_value = {
        "provider": SSOProvider.GOOGLE.value,
        "redirect_uri": "http://test",
    }
    mock_get_manager.return_value = mock_manager

    mock_provider_instance = AsyncMock()
    mock_provider_instance.exchange_code_for_token.side_effect = Exception("Exchange error")
    mock_get_provider.return_value = mock_provider_instance

    with pytest.raises(BadRequestException, match="Failed to exchange code with provider"):
        await service.process_callback(mock_db, mock_request, SSOProvider.GOOGLE, "code123", "state123")


@pytest.mark.asyncio
@patch("src.app.modules.auth.services.sso.get_sso_state_manager")
@patch("src.app.core.utils.cache.client")
@patch("src.app.modules.auth.services.sso_settings.sso_settings_service.get_decrypted_settings")
@patch.object(SSOService, "_get_provider_instance")
@patch("src.app.modules.auth.services.sso.crud_user_sso_connections")
@patch("src.app.modules.auth.services.sso.UserService")
@patch("src.app.modules.auth.services.tokens.create_access_token")
@patch("src.app.modules.auth.services.tokens.create_refresh_token")
async def test_process_callback_success(
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
):
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
    mock_user_info.email = "test@example.com"
    mock_user_info.provider_user_id = "gov_123"
    mock_user_info.email_verified = True
    mock_provider_instance.get_user_info.return_value = mock_user_info
    mock_get_provider.return_value = mock_provider_instance

    mock_crud_sso.get_by_provider_user_id = AsyncMock(return_value=MagicMock(user_id=1, id=uuid4()))
    mock_crud_sso.get_by_criteria = AsyncMock(return_value=MagicMock(user_id=1, id=uuid4()))
    mock_crud_sso.update = AsyncMock()

    mock_user_svc = MagicMock()
    mock_user_svc.get = AsyncMock(
        return_value={
            "id": 1,
            "email": "test@example.com",
            "username": "testuser",
            "is_deleted": False,
            "is_active": True,
            "tenant_id": uuid4(),
        }
    )
    mock_user_svc.get_user_auth_info = AsyncMock(
        return_value={
            "id": 1,
            "email": "test@example.com",
            "username": "testuser",
            "tenant_id": uuid4(),
            "tier": {"name": "free"},
            "tier_id": 1,
            "token_version": 1,
            "is_active": True,
        }
    )
    mock_user_service_cls.return_value = mock_user_svc

    mock_create_access.return_value = "jwt_access"
    mock_create_refresh.return_value = "jwt_refresh"

    # process_callback now requires an active tenant context (raises
    # "Cannot process SSO login without tenant context" otherwise).
    with patch("src.app.modules.auth.services.sso.get_current_tenant") as mock_get_tenant_ctx:
        mock_tenant = MagicMock()
        mock_tenant.id = uuid4()
        mock_get_tenant_ctx.return_value = mock_tenant

        response = await service.process_callback(mock_db, mock_request, SSOProvider.GOOGLE, "code", "state")

    assert response.access_token == "jwt_access"
    assert response.refresh_token == "jwt_refresh"


@pytest.mark.asyncio
@patch("src.app.modules.auth.services.sso.get_sso_state_manager")
@patch("src.app.core.utils.cache.client")
@patch("src.app.modules.auth.services.sso_settings.sso_settings_service.get_decrypted_settings")
@patch.object(SSOService, "_get_provider_instance")
@patch("src.app.modules.auth.services.sso.crud_user_sso_connections")
@patch("src.app.modules.auth.services.sso.UserService")
@patch("src.app.modules.auth.services.tokens.create_access_token")
@patch("src.app.modules.auth.services.tokens.create_refresh_token")
async def test_process_callback_connect_mode(
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
):
    mock_manager = AsyncMock()
    mock_manager.consume.return_value = {
        "provider": SSOProvider.GITHUB.value,
        "redirect_uri": "http://test",
        "extra_data": {"connect_mode": True, "user_id": 2},
    }
    mock_get_manager.return_value = mock_manager

    mock_provider_instance = AsyncMock()
    mock_provider_instance.exchange_code_for_token.return_value = {"access_token": "acc_tok"}

    mock_user_info = MagicMock()
    mock_user_info.email = "github@example.com"
    mock_user_info.provider_user_id = "gh_123"
    mock_user_info.email_verified = True
    mock_provider_instance.get_user_info.return_value = mock_user_info
    mock_get_provider.return_value = mock_provider_instance

    mock_crud_sso.get_by_provider_user_id = AsyncMock(return_value=None)
    mock_crud_sso.get_by_criteria = AsyncMock(return_value=None)
    mock_crud_sso.create = AsyncMock()

    mock_user_svc = MagicMock()
    mock_user_svc.get = AsyncMock(
        return_value={
            "id": 2,
            "email": "github@example.com",
            "username": "ghuser",
            "is_deleted": False,
            "is_active": True,
            "tenant_id": uuid4(),
        }
    )
    mock_user_svc.get_user_auth_info = AsyncMock(
        return_value={
            "id": 2,
            "email": "github@example.com",
            "username": "ghuser",
            "tenant_id": uuid4(),
            "tier": {"name": "free"},
            "tier_id": 1,
            "token_version": 1,
            "is_active": True,
        }
    )
    mock_user_service_cls.return_value = mock_user_svc

    mock_create_access.return_value = "jwt_access"
    mock_create_refresh.return_value = "jwt_refresh"

    # We must patch get_current_tenant because _link_account needs it
    with patch("src.app.modules.auth.services.sso.get_current_tenant") as mock_get_tenant_ctx:
        mock_tenant = MagicMock()
        mock_tenant.id = uuid4()
        mock_get_tenant_ctx.return_value = mock_tenant

        response = await service.process_callback(mock_db, mock_request, SSOProvider.GITHUB, "code", "state")

    assert response.access_token == "jwt_access"
    mock_crud_sso.create.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.auth.services.sso.get_sso_state_manager")
@patch("src.app.core.utils.cache.client")
@patch("src.app.modules.auth.services.sso_settings.sso_settings_service.get_decrypted_settings")
@patch.object(SSOService, "_get_provider_instance")
@patch("src.app.modules.auth.services.sso.crud_user_sso_connections")
@patch("src.app.modules.auth.services.sso.UserService")
@patch("src.app.modules.auth.services.tokens.create_access_token")
@patch("src.app.modules.auth.services.tokens.create_refresh_token")
async def test_process_callback_apple_custom_flow(
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
):
    mock_manager = AsyncMock()
    mock_manager.consume.return_value = {
        "provider": SSOProvider.APPLE.value,
        "redirect_uri": "http://test",
    }
    mock_get_manager.return_value = mock_manager

    mock_provider_instance = AsyncMock(spec=AppleProvider)
    mock_provider_instance.exchange_code_for_token.return_value = {"access_token": "acc_tok", "id_token": "idt"}

    mock_user_info = MagicMock()
    mock_user_info.email = "apple@example.com"
    mock_user_info.provider_user_id = "apple_123"
    mock_user_info.email_verified = True
    mock_user_info.name = "Apple User"
    mock_provider_instance.get_user_info.return_value = mock_user_info
    mock_get_provider.return_value = mock_provider_instance

    mock_crud_sso.get_by_provider_user_id = AsyncMock(return_value=None)
    mock_crud_sso.get_by_criteria = AsyncMock(return_value=None)
    mock_crud_sso.create = AsyncMock()

    mock_user_svc = MagicMock()
    # Mocking new user creation logic
    mock_user_svc.get_by_email = AsyncMock(return_value=None)
    mock_new_user = MagicMock()
    mock_new_user.id = 3
    mock_new_user.email = "apple@example.com"
    mock_new_user.username = "appleuser"
    mock_new_user.tenant_id = uuid4()
    mock_new_user.is_deleted = False
    mock_new_user.is_active = True
    mock_user_svc.create = AsyncMock(return_value=mock_new_user)

    mock_user_svc.get_user_auth_info = AsyncMock(
        return_value={
            "id": 3,
            "email": "apple@example.com",
            "username": "appleuser",
            "tenant_id": mock_new_user.tenant_id,
            "tier": {"name": "pro"},
            "tier_id": 2,
            "token_version": 1,
            "is_active": True,
        }
    )
    mock_user_service_cls.return_value = mock_user_svc
    # _create_sso_user awaits user_svc.crud.update -> must be awaitable
    mock_user_svc.crud.update = AsyncMock()

    mock_create_access.return_value = "jwt_access"
    mock_create_refresh.return_value = "jwt_refresh"

    with patch("src.app.modules.auth.services.sso.get_current_tenant") as mock_get_tenant_ctx:
        mock_tenant = MagicMock()
        mock_tenant.id = uuid4()
        mock_get_tenant_ctx.return_value = mock_tenant

        response = await service.process_callback(
            mock_db, mock_request, SSOProvider.APPLE, "code", "state", user_data={"name": "test"}
        )

    assert response.access_token == "jwt_access"
