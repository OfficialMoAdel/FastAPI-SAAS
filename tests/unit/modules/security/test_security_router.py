from unittest.mock import AsyncMock, patch

import pytest

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


@pytest.mark.asyncio
async def test_add_ip_to_whitelist() -> None:
    request = IPAddressRequest(ip_address="1.2.3.4", reason="test")
    current_user = {"id": 1}

    with patch(
        "src.app.modules.security.router.ip_filter_service.add_to_whitelist", new_callable=AsyncMock
    ) as mock_add:
        mock_add.return_value = True
        response = await add_ip_to_whitelist(request, current_user)  # type: ignore[arg-type]

        assert response.success is True
        assert response.ip_address == "1.2.3.4"
        assert response.list_type == "whitelist"
        assert response.action == "added"
        mock_add.assert_called_once_with(ip_address="1.2.3.4", reason="test", expires_at=None, added_by=1)


@pytest.mark.asyncio
async def test_remove_ip_from_whitelist() -> None:
    current_user = {"id": 1}

    with patch(
        "src.app.modules.security.router.ip_filter_service.remove_from_whitelist", new_callable=AsyncMock
    ) as mock_remove:
        mock_remove.return_value = True
        response = await remove_ip_from_whitelist("1.2.3.4", current_user)  # type: ignore[arg-type]

        assert response.success is True
        assert response.ip_address == "1.2.3.4"
        assert response.action == "removed"
        mock_remove.assert_called_once_with("1.2.3.4")


@pytest.mark.asyncio
async def test_add_ip_to_blacklist() -> None:
    request = IPAddressRequest(ip_address="1.2.3.4", reason="test")
    current_user = {"id": 1}

    with patch(
        "src.app.modules.security.router.ip_filter_service.add_to_blacklist", new_callable=AsyncMock
    ) as mock_add:
        mock_add.return_value = True
        response = await add_ip_to_blacklist(request, current_user)  # type: ignore[arg-type]

        assert response.success is True
        assert response.ip_address == "1.2.3.4"
        assert response.list_type == "blacklist"
        assert response.action == "added"


@pytest.mark.asyncio
async def test_remove_ip_from_blacklist() -> None:
    current_user = {"id": 1}

    with patch(
        "src.app.modules.security.router.ip_filter_service.remove_from_blacklist", new_callable=AsyncMock
    ) as mock_remove:
        mock_remove.return_value = True
        response = await remove_ip_from_blacklist("1.2.3.4", current_user)  # type: ignore[arg-type]

        assert response.success is True
        assert response.action == "removed"


@pytest.mark.asyncio
async def test_get_ip_lists() -> None:
    current_user = {"id": 1}

    with (
        patch("src.app.modules.security.router.ip_filter_service.get_whitelist", new_callable=AsyncMock) as mock_white,
        patch("src.app.modules.security.router.ip_filter_service.get_blacklist", new_callable=AsyncMock) as mock_black,
    ):
        mock_white.return_value = [{"ip_address": "1.1.1.1", "added_at": "2023-01-01T00:00:00Z"}]
        mock_black.return_value = [{"ip_address": "2.2.2.2", "added_at": "2023-01-01T00:00:00Z"}]

        response = await get_ip_lists(current_user)  # type: ignore[arg-type]

        assert len(response.whitelist) == 1
        assert response.whitelist[0].ip_address == "1.1.1.1"
        assert len(response.blacklist) == 1
        assert response.blacklist[0].ip_address == "2.2.2.2"


@pytest.mark.asyncio
async def test_get_ip_filter_config() -> None:
    current_user = {"id": 1}
    response = await get_ip_filter_config(current_user)  # type: ignore[arg-type]
    assert response.whitelist_enabled == settings.IP_WHITELIST_ENABLED


@pytest.mark.asyncio
async def test_update_ip_filter_config() -> None:
    current_user = {"id": 1}
    update = IPFilterConfigUpdate(whitelist_enabled=False)

    # Store old setting
    old_setting = settings.IP_WHITELIST_ENABLED
    try:
        response = await update_ip_filter_config(update, current_user)  # type: ignore[arg-type]
        assert response.whitelist_enabled is False
        assert settings.IP_WHITELIST_ENABLED is False
    finally:
        # Restore old setting
        settings.IP_WHITELIST_ENABLED = old_setting


@pytest.mark.asyncio
async def test_get_auto_ban_config() -> None:
    current_user = {"id": 1}
    response = await get_auto_ban_config(current_user)  # type: ignore[arg-type]
    assert response.enabled == settings.AUTO_BAN_ENABLED


@pytest.mark.asyncio
async def test_update_auto_ban_config() -> None:
    current_user = {"id": 1}
    update = AutoBanConfigUpdate(enabled=not settings.AUTO_BAN_ENABLED)

    old_setting = settings.AUTO_BAN_ENABLED
    try:
        response = await update_auto_ban_config(update, current_user)  # type: ignore[arg-type]
        assert response.enabled == (not old_setting)
    finally:
        settings.AUTO_BAN_ENABLED = old_setting


@pytest.mark.asyncio
async def test_list_banned_ips() -> None:
    current_user = {"id": 1}
    with patch("src.app.modules.security.router.get_all_banned_ips", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = [{"ip": "1.2.3.4", "reason": "test", "ttl_seconds": 60, "ban_count": 1}]

        response = await list_banned_ips(current_user)  # type: ignore[arg-type]
        assert response.total_count == 1
        assert response.banned_ips[0].ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_manually_ban_ip() -> None:
    request = IPAddressRequest(ip_address="1.2.3.4")
    current_user = {"id": 1}

    with patch("src.app.modules.security.router.ban_ip", new_callable=AsyncMock) as mock_ban:
        mock_ban.return_value = True
        response = await manually_ban_ip(request, current_user)  # type: ignore[arg-type]
        assert response.success is True
        assert response.ip_address == "1.2.3.4"


@pytest.mark.asyncio
async def test_unban_ip_address() -> None:
    current_user = {"id": 1}

    with patch("src.app.modules.security.router.unban_ip", new_callable=AsyncMock) as mock_unban:
        mock_unban.return_value = True
        response = await unban_ip_address("1.2.3.4", current_user)  # type: ignore[arg-type]
        assert response.success is True
        assert response.ip_address == "1.2.3.4"


@pytest.mark.asyncio
async def test_unban_ip_address_invalid() -> None:
    current_user = {"id": 1}
    response = await unban_ip_address("invalid-ip", current_user)  # type: ignore[arg-type]
    assert response.success is False
    assert "Invalid IP address format" in response.message


@pytest.mark.asyncio
async def test_get_violation_statistics() -> None:
    current_user = {"id": 1}
    with patch("src.app.modules.security.router.get_violation_stats", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"total_ips": 5, "total_violations": 10}
        response = await get_violation_statistics(current_user)  # type: ignore[arg-type]
        assert response.total_ips == 5
        assert response.total_violations == 10


@pytest.mark.asyncio
async def test_get_violation_statistics_none() -> None:
    current_user = {"id": 1}
    with patch("src.app.modules.security.router.get_violation_stats", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        response = await get_violation_statistics(current_user)  # type: ignore[arg-type]
        assert response.total_ips == 0
        assert response.total_violations == 0


@pytest.mark.asyncio
async def test_get_ip_violation_stats() -> None:
    current_user = {"id": 1}
    with patch("src.app.modules.security.router.get_violation_stats", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"violations": 5}
        response = await get_ip_violation_stats("1.2.3.4", current_user)  # type: ignore[arg-type]
        assert response.violations == 5
        assert response.ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_get_ip_violation_stats_invalid() -> None:
    current_user = {"id": 1}
    response = await get_ip_violation_stats("invalid-ip", current_user)  # type: ignore[arg-type]
    assert response.violations == 0


@pytest.mark.asyncio
async def test_get_ip_violation_stats_none() -> None:
    current_user = {"id": 1}
    with patch("src.app.modules.security.router.get_violation_stats", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        response = await get_ip_violation_stats("1.2.3.4", current_user)  # type: ignore[arg-type]
        assert response.violations == 0


@pytest.mark.asyncio
async def test_passkey_routes() -> None:
    mock_db = AsyncMock()
    mock_ctx = type("Ctx", (), {"db": mock_db, "user_id": 1, "user": {"id": 1}})()
    mock_svc = AsyncMock()
    mock_svc.list_credentials.return_value = []
    res = await list_passkeys(mock_ctx, mock_svc)
    assert res == []
    mock_svc.list_credentials.assert_awaited_once_with(db=mock_db, user_id=1)

    await delete_passkey(123, mock_ctx, mock_svc)
    mock_svc.delete_credential.assert_awaited_once_with(db=mock_db, user_id=1, credential_id=123)
