from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import Request

from src.app.core.exceptions.http_exceptions import BadRequestException
from src.app.modules.sso.schemas import SSOProvider
from src.app.modules.sso.service import SSOService


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


@pytest.mark.asyncio
@patch("src.app.modules.sso_settings.service.sso_settings_service.get_decrypted_settings")
@patch("src.app.modules.sso.service.get_current_tenant")
@patch("src.app.modules.sso.service.get_sso_state_manager")
@patch("src.app.core.utils.cache.client")
async def test_get_authorization_url_success(
    mock_redis, mock_get_manager, mock_get_tenant, mock_get_settings, service, mock_db, mock_request
):  # noqa: E501
    mock_get_settings.return_value = {
        "is_enabled": True,
        "client_id": "client_id",
        "client_secret": "secret",
    }

    mock_tenant = MagicMock()
    mock_tenant.id = uuid4()
    mock_get_tenant.return_value = mock_tenant

    mock_manager = AsyncMock()
    mock_get_manager.return_value = mock_manager

    with patch("src.app.core.security.tokens.TokenGenerator.generate_sso_state", return_value="state-token-123"):
        auth_url, state = await service.get_authorization_url(mock_db, mock_request, SSOProvider.GOOGLE)

    assert state == "state-token-123"
    assert "accounts.google.com/o/oauth2/v2/auth" in auth_url
    mock_manager.create.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.sso_settings.service.sso_settings_service.get_decrypted_settings")
async def test_get_authorization_url_disabled_provider(mock_get_settings, service, mock_db, mock_request):
    mock_get_settings.return_value = {"is_enabled": False}
    with pytest.raises(BadRequestException) as excinfo:
        await service.get_authorization_url(mock_db, mock_request, SSOProvider.GOOGLE)
    assert "disabled" in str(excinfo.value)


@pytest.mark.asyncio
@patch("src.app.modules.sso_settings.service.sso_settings_service.get_settings_by_provider")
async def test_get_enabled_providers(mock_get_settings, service, mock_db):
    mock_get_settings.return_value.is_enabled = True

    providers = await service.get_enabled_providers(mock_db)

    assert len(providers) == 4
    for p in providers:
        assert p["enabled"] is True


@pytest.mark.asyncio
@patch("src.app.modules.sso.service.crud_user_sso_connections")
@patch("src.app.modules.sso.service.UserService")
async def test_disconnect_provider_success(mock_user_service_cls, mock_crud_sso, service, mock_db):
    mock_connection = MagicMock()
    mock_connection.id = uuid4()
    mock_crud_sso.get_by_criteria = AsyncMock(return_value=mock_connection)
    mock_crud_sso.delete_by_criteria = AsyncMock()

    mock_user = MagicMock()
    mock_user.hashed_password = "hashed_password"
    mock_user_service = MagicMock()
    mock_user_service.get = AsyncMock(return_value=mock_user)
    mock_user_service_cls.return_value = mock_user_service

    tenant_id = uuid4()
    await service.disconnect_provider(mock_db, 1, SSOProvider.GOOGLE, tenant_id=tenant_id)

    mock_crud_sso.delete_by_criteria.assert_awaited_once_with(db=mock_db, id=mock_connection.id, tenant_id=tenant_id)


@pytest.mark.asyncio
@patch("src.app.modules.sso.service.crud_user_sso_connections")
@patch("src.app.modules.sso.service.UserService")
async def test_disconnect_provider_fails_only_method(mock_user_service_cls, mock_crud_sso, service, mock_db):
    mock_connection = MagicMock()
    mock_crud_sso.get_by_criteria = AsyncMock(return_value=mock_connection)

    mock_user = MagicMock()
    mock_user.hashed_password = None
    mock_user_service = MagicMock()
    mock_user_service.get = AsyncMock(return_value=mock_user)
    mock_user_service_cls.return_value = mock_user_service

    mock_crud_sso.count_for_user = AsyncMock(return_value=1)  # Only 1 connection

    with pytest.raises(BadRequestException) as excinfo:
        await service.disconnect_provider(mock_db, 1, SSOProvider.GOOGLE, tenant_id=uuid4())
    assert "only authentication method" in str(excinfo.value)


# ============================================================================
# MISSING COVERAGE TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_get_provider_instance_unsupported_provider(service):
    """Test _get_provider_instance raises for unsupported provider (line 68)."""
    with pytest.raises(BadRequestException) as excinfo:
        service._get_provider_instance(
            "unsupported_provider",
            {"client_id": "id", "client_secret": "secret"},
            "http://localhost/callback",
        )
    assert "not supported" in str(excinfo.value.detail).lower()


@pytest.mark.asyncio
async def test_get_provider_instance_google(service):
    """Test provider instance creation for Google."""
    result = service._get_provider_instance(
        SSOProvider.GOOGLE,
        {"client_id": "id", "client_secret": "secret"},
        "http://localhost/callback",
    )
    from src.app.modules.sso.providers.google import GoogleProvider

    assert isinstance(result, GoogleProvider)


@pytest.mark.asyncio
async def test_get_provider_instance_github(service):
    """Test provider instance creation for GitHub."""
    result = service._get_provider_instance(
        SSOProvider.GITHUB,
        {"client_id": "id", "client_secret": "secret"},
        "http://localhost/callback",
    )
    from src.app.modules.sso.providers.github import GitHubProvider

    assert isinstance(result, GitHubProvider)


@pytest.mark.asyncio
async def test_get_provider_instance_microsoft(service):
    """Test provider instance creation for Microsoft."""
    result = service._get_provider_instance(
        SSOProvider.MICROSOFT,
        {"client_id": "id", "client_secret": "secret"},
        "http://localhost/callback",
    )
    from src.app.modules.sso.providers.microsoft import MicrosoftProvider

    assert isinstance(result, MicrosoftProvider)


@pytest.mark.asyncio
async def test_get_provider_instance_apple(service):
    """Test provider instance creation for Apple with all fields."""
    result = service._get_provider_instance(
        SSOProvider.APPLE,
        {
            "client_id": "id",
            "client_secret": "secret",
            "team_id": "team123",
            "key_id": "key123",
            "private_key": "private_key",
        },
        "http://localhost/callback",
    )
    from src.app.modules.sso.providers.apple import AppleProvider

    assert isinstance(result, AppleProvider)


@pytest.mark.asyncio
async def test_get_provider_instance_apple_missing_optional_fields(service):
    """Test Apple provider handles missing optional fields gracefully."""
    # When client_secret is missing, it should still work with defaults
    # Note: private_key is required, client_secret is optional
    result = service._get_provider_instance(
        SSOProvider.APPLE,
        {"client_id": "id", "team_id": "team123", "key_id": "key123", "private_key": "test-key"},
        "http://localhost/callback",
    )
    from src.app.modules.sso.providers.apple import AppleProvider

    assert isinstance(result, AppleProvider)


@pytest.mark.asyncio
@patch("src.app.modules.sso_settings.service.sso_settings_service.get_decrypted_settings")
async def test_get_authorization_url_custom_redirect(mock_get_settings, service, mock_db, mock_request):
    """Test authorization URL with custom redirect URI."""
    mock_get_settings.return_value = {
        "is_enabled": True,
        "client_id": "client_id",
        "client_secret": "secret",
    }

    mock_tenant = MagicMock()
    mock_tenant.id = uuid4()

    with patch("src.app.modules.sso.service.get_current_tenant", return_value=mock_tenant):
        with patch("src.app.modules.sso.service.get_sso_state_manager") as mock_manager:
            mock_manager_instance = AsyncMock()
            mock_manager_instance.create = AsyncMock()
            mock_manager.return_value = mock_manager_instance

            auth_url, state = await service.get_authorization_url(
                mock_db,
                mock_request,
                SSOProvider.GOOGLE,
                redirect_uri="http://custom-callback.com/callback",
            )

            assert "redirect_uri" in auth_url
            assert "custom-callback.com" in auth_url


@pytest.mark.asyncio
@patch("src.app.modules.sso_settings.service.sso_settings_service.get_decrypted_settings")
async def test_get_authorization_url_connect_mode(mock_get_settings, service, mock_db, mock_request):
    """Test authorization URL in connect mode (linking provider to existing account)."""
    mock_get_settings.return_value = {
        "is_enabled": True,
        "client_id": "client_id",
        "client_secret": "secret",
    }

    mock_tenant = MagicMock()
    mock_tenant.id = uuid4()

    with patch("src.app.modules.sso.service.get_current_tenant", return_value=mock_tenant):
        with patch("src.app.modules.sso.service.get_sso_state_manager") as mock_manager:
            mock_manager_instance = AsyncMock()
            mock_manager_instance.create = AsyncMock()
            mock_manager.return_value = mock_manager_instance

            with patch("src.app.core.security.tokens.TokenGenerator.generate_sso_state", return_value="state-token"):
                auth_url, state = await service.get_authorization_url(
                    mock_db,
                    mock_request,
                    SSOProvider.GOOGLE,
                    connect_mode=True,
                    user_id=123,
                )

            assert "state-token" in state


@pytest.mark.asyncio
@patch("src.app.modules.sso.service.crud_user_sso_connections")
@patch("src.app.modules.sso.service.UserService")
async def test_disconnect_provider_success_with_user_check(mock_user_service_cls, mock_crud_sso, service, mock_db):
    """Test disconnect provider validates user has password."""
    mock_connection = MagicMock()
    mock_connection.id = uuid4()
    mock_crud_sso.get_by_criteria = AsyncMock(return_value=mock_connection)
    mock_crud_sso.delete_by_criteria = AsyncMock()  # Make it async

    mock_user = MagicMock()
    mock_user.hashed_password = "hashed_password"
    mock_user_service = MagicMock()
    mock_user_service.get = AsyncMock(return_value=mock_user)
    mock_user_service_cls.return_value = mock_user_service

    mock_crud_sso.count_for_user = AsyncMock(return_value=2)  # More than 1 connection

    # Should not raise
    await service.disconnect_provider(mock_db, 1, SSOProvider.GOOGLE, tenant_id=uuid4())

    mock_crud_sso.delete_by_criteria.assert_awaited_once()
