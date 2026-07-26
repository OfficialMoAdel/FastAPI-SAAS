"""
Security Router

Provides endpoints for security-related operations including:
- Passkey management (backward compatibility)
- IP whitelist/blacklist administration
- Security configuration management

Admin Endpoints:
- POST /admin/security/ip/whitelist - Add IP to whitelist
- DELETE /admin/security/ip/whitelist/{ip} - Remove from whitelist
- POST /admin/security/ip/blacklist - Add IP to blacklist
- DELETE /admin/security/ip/blacklist/{ip} - Remove from blacklist
- GET /admin/security/ip/lists - Get both whitelist and blacklist
- GET /admin/security/config - Get current IP filter config
- PATCH /admin/security/config - Update IP filter config
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_superuser, get_current_user
from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.security.auto_ban import (
    ban_ip,
    get_all_banned_ips,
    get_violation_stats,
    unban_ip,
)
from src.app.modules.passkey.schemas import (
    PasskeyCredentialResponse,
    PasskeyDeleteResponse,
)
from src.app.modules.passkey.service import passkey_service
from src.app.modules.security.schemas import (
    AutoBanConfig,
    AutoBanConfigUpdate,
    BanActionResponse,
    BannedIPEntry,
    BannedIPsResponse,
    IPAddressRequest,
    IPFilterConfig,
    IPFilterConfigUpdate,
    IPListEntry,
    IPListsResponse,
    IPListUpdateResponse,
    IPViolationStats,
    ViolationStats,
)
from src.app.modules.security.service import ip_filter_service

router = APIRouter(prefix="/security", tags=["security"])


# ============================================================================
# User-facing Endpoints (Passkey Management)
# ============================================================================


@router.get("/passkeys", response_model=list[PasskeyCredentialResponse])
async def list_passkeys(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
):
    """List user's passkey credentials.

    This is a convenience endpoint that wraps /auth/passkey/credentials
    for backward compatibility with the old backend.
    """
    return await passkey_service.list_credentials(db=db, user_id=current_user["id"])


@router.delete("/passkeys/{passkey_id}", response_model=PasskeyDeleteResponse)
async def delete_passkey(
    passkey_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
):
    """Delete a passkey credential.

    This is a convenience endpoint that wraps /auth/passkey/credentials/{credential_id}
    for backward compatibility with the old backend.
    """
    await passkey_service.delete_credential(db=db, user_id=current_user["id"], credential_id=passkey_id)
    return PasskeyDeleteResponse(message="Passkey deleted successfully")


# ============================================================================
# Admin Endpoints (IP Filter Management)
# ============================================================================


@router.post(
    "/admin/ip/whitelist",
    response_model=IPListUpdateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_superuser)],
)
async def add_ip_to_whitelist(
    request: IPAddressRequest,
    current_user: Annotated[dict, Depends(get_current_superuser)],
) -> IPListUpdateResponse:
    """Add an IP address to the whitelist.

    Requires superuser privileges. Whitelisted IPs are allowed access
    regardless of blacklist status. Can optionally set an expiration time.

    - **ip_address**: IPv4 or IPv6 address
    - **reason**: Optional reason for whitelisting
    - **expires_at**: Optional expiration datetime
    """
    success = await ip_filter_service.add_to_whitelist(
        ip_address=request.ip_address,
        reason=request.reason,
        expires_at=request.expires_at,
        added_by=current_user.get("id"),
    )

    return IPListUpdateResponse(
        success=success,
        ip_address=request.ip_address,
        list_type="whitelist",
        action="added",
        message=f"IP {request.ip_address} added to whitelist"
        if success
        else f"Failed to add IP {request.ip_address} to whitelist",
    )


@router.delete(
    "/admin/ip/whitelist",
    response_model=IPListUpdateResponse,
    dependencies=[Depends(get_current_superuser)],
)
async def remove_ip_from_whitelist(
    ip_address: str,
    current_user: Annotated[dict, Depends(get_current_superuser)],  # noqa: ARG001
) -> IPListUpdateResponse:
    """Remove an IP address from the whitelist.

    Requires superuser privileges.

    - **ip_address**: IPv4 or IPv6 address to remove
    """
    success = await ip_filter_service.remove_from_whitelist(ip_address)

    return IPListUpdateResponse(
        success=success,
        ip_address=ip_address,
        list_type="whitelist",
        action="removed",
        message=f"IP {ip_address} removed from whitelist" if success else f"IP {ip_address} was not in whitelist",
    )


@router.post(
    "/admin/ip/blacklist",
    response_model=IPListUpdateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_superuser)],
)
async def add_ip_to_blacklist(
    request: IPAddressRequest,
    current_user: Annotated[dict, Depends(get_current_superuser)],
) -> IPListUpdateResponse:
    """Add an IP address to the blacklist.

    Requires superuser privileges. Blacklisted IPs are blocked from
    accessing the API. Can optionally set an expiration time.

    - **ip_address**: IPv4 or IPv6 address
    - **reason**: Optional reason for blacklisting
    - **expires_at**: Optional expiration datetime
    """
    success = await ip_filter_service.add_to_blacklist(
        ip_address=request.ip_address,
        reason=request.reason,
        expires_at=request.expires_at,
        added_by=current_user.get("id"),
    )

    return IPListUpdateResponse(
        success=success,
        ip_address=request.ip_address,
        list_type="blacklist",
        action="added",
        message=f"IP {request.ip_address} added to blacklist"
        if success
        else f"Failed to add IP {request.ip_address} to blacklist",
    )


@router.delete(
    "/admin/ip/blacklist",
    response_model=IPListUpdateResponse,
    dependencies=[Depends(get_current_superuser)],
)
async def remove_ip_from_blacklist(
    ip_address: str,
    current_user: Annotated[dict, Depends(get_current_superuser)],  # noqa: ARG001
) -> IPListUpdateResponse:
    """Remove an IP address from the blacklist.

    Requires superuser privileges.

    - **ip_address**: IPv4 or IPv6 address to remove
    """
    success = await ip_filter_service.remove_from_blacklist(ip_address)

    return IPListUpdateResponse(
        success=success,
        ip_address=ip_address,
        list_type="blacklist",
        action="removed",
        message=f"IP {ip_address} removed from blacklist" if success else f"IP {ip_address} was not in blacklist",
    )


@router.get(
    "/admin/ip/lists",
    response_model=IPListsResponse,
    dependencies=[Depends(get_current_superuser)],
)
async def get_ip_lists(
    current_user: Annotated[dict, Depends(get_current_superuser)],  # noqa: ARG001
) -> IPListsResponse:
    """Get both whitelist and blacklist with all entries.

    Requires superuser privileges.

    Returns current configuration status and all IP entries
    with their metadata (added time, reason, expiration).
    """
    whitelist_raw = await ip_filter_service.get_whitelist()
    blacklist_raw = await ip_filter_service.get_blacklist()

    # Convert raw entries to schema objects
    whitelist = [
        IPListEntry(
            ip_address=entry["ip_address"],
            added_at=entry["added_at"],
            reason=entry.get("reason"),
            expires_at=entry.get("expires_at"),
            added_by=entry.get("added_by"),
        )
        for entry in whitelist_raw
    ]

    blacklist = [
        IPListEntry(
            ip_address=entry["ip_address"],
            added_at=entry["added_at"],
            reason=entry.get("reason"),
            expires_at=entry.get("expires_at"),
            added_by=entry.get("added_by"),
        )
        for entry in blacklist_raw
    ]

    return IPListsResponse(
        whitelist=whitelist,
        blacklist=blacklist,
        whitelist_enabled=settings.IP_WHITELIST_ENABLED,
        blacklist_enabled=settings.IP_BLACKLIST_ENABLED,
        whitelist_bypass_rate_limit=settings.IP_WHITELIST_BYPASS_RATE_LIMIT,
    )


@router.get(
    "/admin/config",
    response_model=IPFilterConfig,
    dependencies=[Depends(get_current_superuser)],
)
async def get_ip_filter_config(
    current_user: Annotated[dict, Depends(get_current_superuser)],  # noqa: ARG001
) -> IPFilterConfig:
    """Get current IP filter configuration.

    Requires superuser privileges.

    Returns the current configuration values for IP filtering.
    Note: Configuration changes require application restart to take effect.
    """
    return IPFilterConfig(
        whitelist_enabled=settings.IP_WHITELIST_ENABLED,
        blacklist_enabled=settings.IP_BLACKLIST_ENABLED,
        whitelist_bypass_rate_limit=settings.IP_WHITELIST_BYPASS_RATE_LIMIT,
    )


@router.patch(
    "/admin/config",
    response_model=IPFilterConfig,
    dependencies=[Depends(get_current_superuser)],
)
async def update_ip_filter_config(
    config_update: IPFilterConfigUpdate,
    current_user: Annotated[dict, Depends(get_current_superuser)],  # noqa: ARG001
) -> IPFilterConfig:
    """Update IP filter configuration.

    Requires superuser privileges.

    **Important**: Changes to configuration are stored in memory only
    and will be lost on application restart. To make permanent changes,
    update environment variables:

    - `IP_WHITELIST_ENABLED` - Enable/disable whitelist
    - `IP_BLACKLIST_ENABLED` - Enable/disable blacklist
    - `IP_WHITELIST_BYPASS_RATE_LIMIT` - Allow whitelist to bypass rate limits

    **Note**: This endpoint updates the in-memory settings only.
    For persistent configuration, modify environment variables.
    """
    from typing import Any

    from src.app.core.utils.config_store import update_runtime_config
    updates: dict[str, Any] = {}
    if config_update.whitelist_enabled is not None:
        settings.IP_WHITELIST_ENABLED = config_update.whitelist_enabled
        updates["IP_WHITELIST_ENABLED"] = config_update.whitelist_enabled
    if config_update.blacklist_enabled is not None:
        settings.IP_BLACKLIST_ENABLED = config_update.blacklist_enabled
        updates["IP_BLACKLIST_ENABLED"] = config_update.blacklist_enabled
    if config_update.whitelist_bypass_rate_limit is not None:
        settings.IP_WHITELIST_BYPASS_RATE_LIMIT = config_update.whitelist_bypass_rate_limit
        updates["IP_WHITELIST_BYPASS_RATE_LIMIT"] = config_update.whitelist_bypass_rate_limit

    await update_runtime_config(updates)

    return IPFilterConfig(
        whitelist_enabled=settings.IP_WHITELIST_ENABLED,
        blacklist_enabled=settings.IP_BLACKLIST_ENABLED,
        whitelist_bypass_rate_limit=settings.IP_WHITELIST_BYPASS_RATE_LIMIT,
    )


# ============================================================================
# Admin Endpoints (Auto-ban Management)
# ============================================================================


@router.get(
    "/admin/auto-ban/config",
    response_model=AutoBanConfig,
    dependencies=[Depends(get_current_superuser)],
)
async def get_auto_ban_config(
    current_user: Annotated[dict, Depends(get_current_superuser)],  # noqa: ARG001
) -> AutoBanConfig:
    """Get current auto-ban configuration.

    Requires superuser privileges.

    Returns the current auto-ban settings including threshold,
    duration, and escalation settings.
    """
    return AutoBanConfig(
        enabled=settings.AUTO_BAN_ENABLED,
        threshold=settings.AUTO_BAN_THRESHOLD,
        duration=settings.AUTO_BAN_DURATION,
        escalation=settings.AUTO_BAN_ESCALATION,
    )


@router.patch(
    "/admin/auto-ban/config",
    response_model=AutoBanConfig,
    dependencies=[Depends(get_current_superuser)],
)
async def update_auto_ban_config(
    config_update: AutoBanConfigUpdate,
    current_user: Annotated[dict, Depends(get_current_superuser)],  # noqa: ARG001
) -> AutoBanConfig:
    """Update auto-ban configuration.

    Requires superuser privileges.

    **Important**: Changes to configuration are stored in memory only
    and will be lost on application restart. To make permanent changes,
    update environment variables:

    - `AUTO_BAN_ENABLED` - Enable/disable auto-ban
    - `AUTO_BAN_THRESHOLD` - Violations before auto-ban
    - `AUTO_BAN_DURATION` - Ban duration in seconds
    - `AUTO_BAN_ESCALATION` - Enable duration escalation
    """
    from typing import Any

    from src.app.core.utils.config_store import update_runtime_config
    updates: dict[str, Any] = {}
    if config_update.enabled is not None:
        settings.AUTO_BAN_ENABLED = config_update.enabled
        updates["AUTO_BAN_ENABLED"] = config_update.enabled
    if config_update.threshold is not None:
        settings.AUTO_BAN_THRESHOLD = config_update.threshold
        updates["AUTO_BAN_THRESHOLD"] = config_update.threshold
    if config_update.duration is not None:
        settings.AUTO_BAN_DURATION = config_update.duration
        updates["AUTO_BAN_DURATION"] = config_update.duration
    if config_update.escalation is not None:
        settings.AUTO_BAN_ESCALATION = config_update.escalation
        updates["AUTO_BAN_ESCALATION"] = config_update.escalation

    await update_runtime_config(updates)

    return AutoBanConfig(
        enabled=settings.AUTO_BAN_ENABLED,
        threshold=settings.AUTO_BAN_THRESHOLD,
        duration=settings.AUTO_BAN_DURATION,
        escalation=settings.AUTO_BAN_ESCALATION,
    )


@router.get(
    "/admin/auto-ban/banned",
    response_model=BannedIPsResponse,
    dependencies=[Depends(get_current_superuser)],
)
async def list_banned_ips(
    current_user: Annotated[dict, Depends(get_current_superuser)],  # noqa: ARG001
) -> BannedIPsResponse:
    """Get all currently banned IP addresses.

    Requires superuser privileges.

    Returns a list of all IPs currently banned by the auto-ban system,
    including ban reason and time remaining.
    """
    banned_raw = await get_all_banned_ips()

    banned = [
        BannedIPEntry(
            ip=entry["ip"],
            reason=entry["reason"],
            ttl_seconds=entry["ttl_seconds"],
            ban_count=entry["ban_count"],
        )
        for entry in banned_raw
    ]

    return BannedIPsResponse(
        banned_ips=banned,
        total_count=len(banned),
    )


@router.post(
    "/admin/auto-ban/ban",
    response_model=BanActionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_superuser)],
)
async def manually_ban_ip(
    request: IPAddressRequest,
    current_user: Annotated[dict, Depends(get_current_superuser)],
) -> BanActionResponse:
    """Manually ban an IP address.

    Requires superuser privileges.

    This endpoint allows manual banning of an IP address outside of
    the automatic rate limit violation system.

    - **ip_address**: IPv4 or IPv6 address to ban
    - **reason**: Optional reason for the ban
    """
    success = await ban_ip(
        request.ip_address,
        reason=request.reason or f"Manual ban by admin {current_user.get('id')}",
    )

    return BanActionResponse(
        success=success,
        ip_address=request.ip_address,
        action="banned",
        message=f"IP {request.ip_address} banned successfully" if success else f"Failed to ban IP {request.ip_address}",
    )


@router.delete(
    "/admin/auto-ban/ban",
    response_model=BanActionResponse,
    dependencies=[Depends(get_current_superuser)],
)
async def unban_ip_address(
    ip_address: str,
    current_user: Annotated[dict, Depends(get_current_superuser)],  # noqa: ARG001
) -> BanActionResponse:
    """Remove ban from an IP address.

    Requires superuser privileges.

    - **ip_address**: IPv4 or IPv6 address to unban
    """
    # Validate IP format
    import ipaddress

    try:
        ipaddress.ip_address(ip_address)
    except ValueError:
        return BanActionResponse(
            success=False,
            ip_address=ip_address,
            action="unbanned",
            message=f"Invalid IP address format: {ip_address}",
        )

    success = await unban_ip(ip_address)

    return BanActionResponse(
        success=success,
        ip_address=ip_address,
        action="unbanned",
        message=f"IP {ip_address} unbanned" if success else f"IP {ip_address} was not banned",
    )


@router.get(
    "/admin/auto-ban/stats",
    response_model=ViolationStats,
    dependencies=[Depends(get_current_superuser)],
)
async def get_violation_statistics(
    current_user: Annotated[dict, Depends(get_current_superuser)],  # noqa: ARG001
) -> ViolationStats:
    """Get violation statistics.

    Requires superuser privileges.

    Returns aggregate statistics about rate limit violations across
    all tracked IP addresses.
    """
    stats = await get_violation_stats()

    if isinstance(stats, dict):
        return ViolationStats(
            total_ips=stats.get("total_ips", 0),
            total_violations=stats.get("total_violations", 0),
        )

    return ViolationStats(
        total_ips=0,
        total_violations=0,
    )


@router.get(
    "/admin/auto-ban/stats/{ip_address}",
    response_model=IPViolationStats,
    dependencies=[Depends(get_current_superuser)],
)
async def get_ip_violation_stats(
    ip_address: str,
    current_user: Annotated[dict, Depends(get_current_superuser)],  # noqa: ARG001
) -> IPViolationStats:
    """Get violation statistics for a specific IP.

    Requires superuser privileges.

    - **ip_address**: IPv4 or IPv6 address to check

    Returns violation count and ban status for the specified IP.
    """
    # Validate IP format
    import ipaddress

    try:
        ipaddress.ip_address(ip_address)
    except ValueError:
        return IPViolationStats(ip=ip_address, violations=0, total_ips=0, total_violations=0)

    stats = await get_violation_stats(ip_address)
    # ban_info = await get_ban_info(ip_address)

    if isinstance(stats, dict):
        violations = stats.get("violations", 0)
    else:
        violations = 0

    return IPViolationStats(
        ip=ip_address,
        violations=violations,
        total_ips=1,
        total_violations=violations,
    )
