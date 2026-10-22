"""Unit tests for the security module's service layer and its router.

The security module gained a service layer (F1). The router is now a thin
adapter: it resolves the request context and delegates. These tests
therefore exercise the SERVICE for the business logic and the ROUTER
only for the adapter contract — that the router passes the right
arguments through and shapes the response.

The patch targets are the service module's names, not the router's. A
patch of ``security.router.ip_filter_service`` would no longer intercept
anything, because the router no longer reaches the core singleton
directly; the seam moved with the logic.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.api.deps.auth import AuthenticatedUser
from src.app.core.config import settings
from src.app.modules.security.router import (
    add_ip_to_blacklist,
    add_ip_to_whitelist,
    delete_passkey,
    get_auto_ban_config,
    get_ip_filter_config,
    get_ip_lists,
    get_ip_violation_stats,
    get_violation_statistics,
    list_banned_ips,
    list_passkeys,
    manually_ban_ip,
    remove_ip_from_blacklist,
    remove_ip_from_whitelist,
    unban_ip_address,
    update_auto_ban_config,
    update_ip_filter_config,
)
from src.app.modules.security.schemas import (
    AutoBanConfigUpdate,
    IPAddressRequest,
    IPFilterConfigUpdate,
)
from src.app.modules.security.service import SecurityService

SERVICE = "src.app.modules.security.service"


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(id=1, email="admin@e.com")


@pytest.mark.asyncio
async def test_add_ip_to_whitelist() -> None:
    request = IPAddressRequest(ip_address="1.2.3.4", reason="test")
    current_user = _admin()

    with patch(
        f"{SERVICE}.ip_filter_service.add_to_whitelist", new_callable=AsyncMock
    ) as mock_add:
        mock_add.return_value = True
        response = await add_ip_to_whitelist(request, current_user, SecurityService())  # type: ignore[arg-type]

        assert response.success is True
        assert response.ip_address == "1.2.3.4"
        assert response.list_type == "whitelist"
        assert response.action == "added"
        mock_add.assert_called_once_with(ip_address="1.2.3.4", reason="test", expires_at=None, added_by=1)


@pytest.mark.asyncio
async def test_remove_ip_from_whitelist() -> None:
    current_user = _admin()

    with patch(
        f"{SERVICE}.ip_filter_service.remove_from_whitelist", new_callable=AsyncMock
    ) as mock_remove:
        mock_remove.return_value = True
        response = await remove_ip_from_whitelist("1.2.3.4", current_user, SecurityService())  # type: ignore[arg-type]

        assert response.success is True
        assert response.ip_address == "1.2.3.4"
        assert response.action == "removed"
        mock_remove.assert_called_once_with("1.2.3.4")


@pytest.mark.asyncio
async def test_add_ip_to_blacklist() -> None:
    request = IPAddressRequest(ip_address="1.2.3.4", reason="test")
    current_user = _admin()

    with patch(
        f"{SERVICE}.ip_filter_service.add_to_blacklist", new_callable=AsyncMock
    ) as mock_add:
        mock_add.return_value = True
        response = await add_ip_to_blacklist(request, current_user, SecurityService())  # type: ignore[arg-type]

        assert response.success is True
        assert response.ip_address == "1.2.3.4"
        assert response.list_type == "blacklist"
        assert response.action == "added"


@pytest.mark.asyncio
async def test_remove_ip_from_blacklist() -> None:
    current_user = _admin()

    with patch(
        f"{SERVICE}.ip_filter_service.remove_from_blacklist", new_callable=AsyncMock
    ) as mock_remove:
        mock_remove.return_value = True
        response = await remove_ip_from_blacklist("1.2.3.4", current_user, SecurityService())  # type: ignore[arg-type]

        assert response.success is True
        assert response.action == "removed"


@pytest.mark.asyncio
async def test_get_ip_lists() -> None:
    current_user = _admin()

    with (
        patch(f"{SERVICE}.ip_filter_service.get_whitelist", new_callable=AsyncMock) as mock_white,
        patch(f"{SERVICE}.ip_filter_service.get_blacklist", new_callable=AsyncMock) as mock_black,
    ):
        mock_white.return_value = [{"ip_address": "1.1.1.1", "added_at": "2023-01-01T00:00:00Z"}]
        mock_black.return_value = [{"ip_address": "2.2.2.2", "added_at": "2023-01-01T00:00:00Z"}]

        response = await get_ip_lists(current_user, SecurityService())  # type: ignore[arg-type]

        assert len(response.whitelist) == 1
        assert response.whitelist[0].ip_address == "1.1.1.1"
        assert len(response.blacklist) == 1
        assert response.blacklist[0].ip_address == "2.2.2.2"


@pytest.mark.asyncio
async def test_get_ip_filter_config() -> None:
    response = await get_ip_filter_config(_admin(), SecurityService())  # type: ignore[arg-type]
    assert response.whitelist_enabled == settings.IP_WHITELIST_ENABLED


@pytest.mark.asyncio
async def test_update_ip_filter_config() -> None:
    update = IPFilterConfigUpdate(whitelist_enabled=False)

    # Store old setting
    old_setting = settings.IP_WHITELIST_ENABLED
    try:
        response = await update_ip_filter_config(update, _admin(), SecurityService())  # type: ignore[arg-type]
        assert response.whitelist_enabled is False
        assert settings.IP_WHITELIST_ENABLED is False
    finally:
        # Restore old setting
        settings.IP_WHITELIST_ENABLED = old_setting


@pytest.mark.asyncio
async def test_get_auto_ban_config() -> None:
    response = await get_auto_ban_config(_admin(), SecurityService())  # type: ignore[arg-type]
    assert response.enabled == settings.AUTO_BAN_ENABLED


@pytest.mark.asyncio
async def test_update_auto_ban_config() -> None:
    update = AutoBanConfigUpdate(enabled=not settings.AUTO_BAN_ENABLED)

    old_setting = settings.AUTO_BAN_ENABLED
    try:
        response = await update_auto_ban_config(update, _admin(), SecurityService())  # type: ignore[arg-type]
        assert response.enabled == (not old_setting)
    finally:
        settings.AUTO_BAN_ENABLED = old_setting


@pytest.mark.asyncio
async def test_update_ip_filter_config_writes_through_the_runtime_config_seam() -> None:
    """Both PATCH endpoints mutate + persist through the one writer, mapping
    request fields to the store's key grammar themselves."""
    update = IPFilterConfigUpdate(whitelist_enabled=False, whitelist_bypass_rate_limit=True)

    with patch(
        "src.app.core.security.runtime_config.apply_and_store_runtime_config", new_callable=AsyncMock
    ) as mock_store:
        await update_ip_filter_config(update, _admin(), SecurityService())  # type: ignore[arg-type]

    assert mock_store.await_args.args[0] == {
        "IP_WHITELIST_ENABLED": False,
        "IP_WHITELIST_BYPASS_RATE_LIMIT": True,
    }


@pytest.mark.asyncio
async def test_update_auto_ban_config_writes_through_the_runtime_config_seam() -> None:
    update = AutoBanConfigUpdate(threshold=7, escalation=False)

    with patch(
        "src.app.core.security.runtime_config.apply_and_store_runtime_config", new_callable=AsyncMock
    ) as mock_store:
        await update_auto_ban_config(update, _admin(), SecurityService())  # type: ignore[arg-type]

    assert mock_store.await_args.args[0] == {"AUTO_BAN_THRESHOLD": 7, "AUTO_BAN_ESCALATION": False}


@pytest.mark.asyncio
async def test_update_ip_filter_config_audits() -> None:
    """P2-1: mutating the IP filter config must emit an audit trail with the actor."""
    update = IPFilterConfigUpdate(whitelist_enabled=False)

    old_setting = settings.IP_WHITELIST_ENABLED
    try:
        with patch(f"{SERVICE}.audit", new_callable=AsyncMock) as mock_audit:
            await update_ip_filter_config(update, _admin(), SecurityService())  # type: ignore[arg-type]

            mock_audit.assert_awaited_once()
            kwargs = mock_audit.call_args.kwargs
            assert kwargs["action"] == "security.ip_filter_config_updated"
            assert kwargs["user_id"] == 1
    finally:
        settings.IP_WHITELIST_ENABLED = old_setting


@pytest.mark.asyncio
async def test_update_auto_ban_config_audits() -> None:
    """P2-1: mutating the auto-ban config must emit an audit trail with the actor."""
    update = AutoBanConfigUpdate(enabled=not settings.AUTO_BAN_ENABLED)

    old_setting = settings.AUTO_BAN_ENABLED
    try:
        with patch(f"{SERVICE}.audit", new_callable=AsyncMock) as mock_audit:
            await update_auto_ban_config(update, _admin(), SecurityService())  # type: ignore[arg-type]

            mock_audit.assert_awaited_once()
            kwargs = mock_audit.call_args.kwargs
            assert kwargs["action"] == "security.auto_ban_config_updated"
            assert kwargs["user_id"] == 1
    finally:
        settings.AUTO_BAN_ENABLED = old_setting


@pytest.mark.asyncio
async def test_list_banned_ips() -> None:
    with patch(f"{SERVICE}.get_all_banned_ips", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = [{"ip": "1.2.3.4", "reason": "test", "ttl_seconds": 60, "ban_count": 1}]

        response = await list_banned_ips(_admin(), SecurityService())  # type: ignore[arg-type]
        assert response.total_count == 1
        assert response.banned_ips[0].ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_manually_ban_ip() -> None:
    request = IPAddressRequest(ip_address="1.2.3.4")

    with patch(f"{SERVICE}.ban_ip", new_callable=AsyncMock) as mock_ban:
        mock_ban.return_value = True
        response = await manually_ban_ip(request, _admin(), SecurityService())  # type: ignore[arg-type]
        assert response.success is True
        assert response.ip_address == "1.2.3.4"


@pytest.mark.asyncio
async def test_unban_ip_address() -> None:
    with patch(f"{SERVICE}.unban_ip", new_callable=AsyncMock) as mock_unban:
        mock_unban.return_value = True
        response = await unban_ip_address("1.2.3.4", _admin(), SecurityService())  # type: ignore[arg-type]
        assert response.success is True
        assert response.ip_address == "1.2.3.4"


@pytest.mark.asyncio
async def test_unban_ip_address_invalid() -> None:
    response = await unban_ip_address("invalid-ip", _admin(), SecurityService())  # type: ignore[arg-type]
    assert response.success is False
    assert "Invalid IP address format" in response.message


@pytest.mark.asyncio
async def test_get_violation_statistics() -> None:
    with patch(f"{SERVICE}.get_violation_stats", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"total_ips": 5, "total_violations": 10}
        response = await get_violation_statistics(_admin(), SecurityService())  # type: ignore[arg-type]
        assert response.total_ips == 5
        assert response.total_violations == 10


@pytest.mark.asyncio
async def test_get_violation_statistics_none() -> None:
    with patch(f"{SERVICE}.get_violation_stats", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        response = await get_violation_statistics(_admin(), SecurityService())  # type: ignore[arg-type]
        assert response.total_ips == 0
        assert response.total_violations == 0


@pytest.mark.asyncio
async def test_get_ip_violation_stats() -> None:
    with patch(f"{SERVICE}.get_violation_stats", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"violations": 5}
        response = await get_ip_violation_stats("1.2.3.4", _admin(), SecurityService())  # type: ignore[arg-type]
        assert response.violations == 5
        assert response.ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_get_ip_violation_stats_invalid() -> None:
    response = await get_ip_violation_stats("invalid-ip", _admin(), SecurityService())  # type: ignore[arg-type]
    assert response.violations == 0


@pytest.mark.asyncio
async def test_get_ip_violation_stats_none() -> None:
    with patch(f"{SERVICE}.get_violation_stats", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        response = await get_ip_violation_stats("1.2.3.4", _admin(), SecurityService())  # type: ignore[arg-type]
        assert response.violations == 0


@pytest.mark.asyncio
async def test_passkey_routes_delegate_to_the_service() -> None:
    """The router delegates; the service owns the passkey read."""
    from src.app.core.pagination import PaginationParams

    mock_db = AsyncMock()
    mock_ctx = type("Ctx", (), {"db": mock_db, "user_id": 1, "user": {"id": 1}})()
    pagination = PaginationParams(page=1, per_page=20)

    service = SecurityService()
    service.passkey_service = MagicMock()
    service.passkey_service.list_credentials_page = AsyncMock(
        return_value={"data": [], "total_count": 0}
    )
    service.passkey_service.delete_credential = AsyncMock()

    res = await list_passkeys(mock_ctx, service, pagination)  # type: ignore[arg-type]
    assert res.items == []
    assert res.total == 0
    service.passkey_service.list_credentials_page.assert_awaited_once_with(
        db=mock_db, user_id=1, offset=0, limit=20
    )

    await delete_passkey(123, mock_ctx, service)
    service.passkey_service.delete_credential.assert_awaited_once_with(db=mock_db, user_id=1, credential_id=123)
