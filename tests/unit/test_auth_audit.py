from unittest.mock import AsyncMock, patch

import pytest

from src.app.modules.auth.audit import auth_audit


@pytest.fixture
def mock_event_bus():
    """Mock the global event_bus instance."""
    with patch("src.app.modules.auth.audit.event_bus") as mock_bus:
        mock_bus.publish = AsyncMock()
        yield mock_bus

@pytest.fixture
def mock_logger():
    """Mock the structlog logger instance."""
    with patch("src.app.modules.auth.audit.logger") as mock_log:
        yield mock_log

async def test_log_event_with_tenant(mock_event_bus, mock_logger):
    """Test log_event publishes to event bus when tenant_id is provided."""
    await auth_audit.log_event(
        action="test.action",
        tenant_id=1,
        user_id=2,
        details={"key": "value"},
        ip_address="127.0.0.1",
        user_agent="pytest"
    )

    # Verify structlog log
    mock_logger.info.assert_called_once()
    kwargs = mock_logger.info.call_args[1]
    assert kwargs["action"] == "test.action"
    assert kwargs["tenant_id"] == 1
    assert kwargs["user_id"] == 2
    assert kwargs["key"] == "value"

    # Verify event bus publish
    mock_event_bus.publish.assert_called_once()
    args, kwargs = mock_event_bus.publish.call_args
    assert args[0] == "audit.log"

    event_payload = args[1]
    assert event_payload["action"] == "test.action"
    assert event_payload["tenant_id"] == "1"
    assert event_payload["user_id"] == "2"
    assert event_payload["details"]["key"] == "value"
    assert event_payload["ip_address"] == "127.0.0.1"
    assert event_payload["user_agent"] == "pytest"

async def test_log_event_without_tenant(mock_event_bus, mock_logger):
    """Test log_event logs but does not publish to event bus when tenant_id is omitted."""
    await auth_audit.log_event(
        action="test.action",
        tenant_id=None,
        user_id=None
    )

    mock_logger.info.assert_called_once()
    mock_event_bus.publish.assert_not_called()

async def test_log_event_bus_exception(mock_event_bus, mock_logger):
    """Test log_event handles exceptions gracefully when publishing to event bus."""
    mock_event_bus.publish.side_effect = Exception("Bus error")

    await auth_audit.log_event(
        action="test.action",
        tenant_id=1
    )

    # Log info should succeed
    mock_logger.info.assert_called_once()
    # Log error should capture exception
    mock_logger.error.assert_called_once()
    assert mock_logger.error.call_args[1]["error"] == "Bus error"
    assert mock_logger.error.call_args[1]["action"] == "test.action"

async def test_magic_link_events(mock_event_bus):
    await auth_audit.log_magic_link_sent(1, "test@example.com")
    mock_event_bus.publish.assert_called_once()
    payload = mock_event_bus.publish.call_args[0][1]
    assert payload["action"] == "auth.magic_link.sent"
    assert payload["details"]["email"] == "test@example.com"
    mock_event_bus.publish.reset_mock()

    await auth_audit.log_magic_link_verified(1, 2, "test@example.com")
    mock_event_bus.publish.assert_called_once()
    payload = mock_event_bus.publish.call_args[0][1]
    assert payload["action"] == "auth.magic_link.verified"
    mock_event_bus.publish.reset_mock()

    await auth_audit.log_magic_link_failed(1, "invalid_token")
    mock_event_bus.publish.assert_called_once()
    payload = mock_event_bus.publish.call_args[0][1]
    assert payload["action"] == "auth.magic_link.failed"
    assert payload["details"]["reason"] == "invalid_token"
    mock_event_bus.publish.reset_mock()

async def test_sso_events(mock_event_bus):
    await auth_audit.log_sso_initiated(1, "google")
    assert mock_event_bus.publish.call_args[0][1]["action"] == "auth.sso.initiated"
    mock_event_bus.publish.reset_mock()

    await auth_audit.log_sso_completed(1, 2, "google", is_new_user=True)
    assert mock_event_bus.publish.call_args[0][1]["action"] == "auth.sso.completed"
    assert mock_event_bus.publish.call_args[0][1]["details"]["is_new_user"] is True
    mock_event_bus.publish.reset_mock()

    await auth_audit.log_sso_failed(1, "google", "network error")
    assert mock_event_bus.publish.call_args[0][1]["action"] == "auth.sso.failed"
    mock_event_bus.publish.reset_mock()

    await auth_audit.log_sso_connected(1, 2, "google")
    assert mock_event_bus.publish.call_args[0][1]["action"] == "auth.sso.connected"
    mock_event_bus.publish.reset_mock()

    await auth_audit.log_sso_disconnected(1, 2, "google")
    assert mock_event_bus.publish.call_args[0][1]["action"] == "auth.sso.disconnected"
    mock_event_bus.publish.reset_mock()

async def test_passkey_events(mock_event_bus):
    await auth_audit.log_passkey_registered(1, 2, "webauthn")
    assert mock_event_bus.publish.call_args[0][1]["action"] == "auth.passkey.registered"
    mock_event_bus.publish.reset_mock()

    await auth_audit.log_passkey_authenticated(1, 2, 3)
    assert mock_event_bus.publish.call_args[0][1]["action"] == "auth.passkey.authenticated"
    mock_event_bus.publish.reset_mock()

    await auth_audit.log_passkey_failed(1, "invalid_signature")
    assert mock_event_bus.publish.call_args[0][1]["action"] == "auth.passkey.failed"
    mock_event_bus.publish.reset_mock()

    await auth_audit.log_passkey_deleted(1, 2, 3)
    assert mock_event_bus.publish.call_args[0][1]["action"] == "auth.passkey.deleted"
    mock_event_bus.publish.reset_mock()

async def test_general_login_events(mock_event_bus):
    await auth_audit.log_login_success(1, 2, "password")
    assert mock_event_bus.publish.call_args[0][1]["action"] == "auth.login.success"
    mock_event_bus.publish.reset_mock()

    await auth_audit.log_login_failed(1, "password", "wrong credentials")
    assert mock_event_bus.publish.call_args[0][1]["action"] == "auth.login.failed"
    mock_event_bus.publish.reset_mock()

    await auth_audit.log_logout(1, 2)
    assert mock_event_bus.publish.call_args[0][1]["action"] == "auth.logout"
