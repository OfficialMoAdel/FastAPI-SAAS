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

Every handler here is a thin adapter: the business logic lives in
``src/app/modules/security/service.py``. The router resolves the request
context, delegates, and shapes the response. The core singletons
(``ip_filter_service``, the auto-ban functions) are reached from the
service, not from here.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from structlog import get_logger

from src.app.api.deps import RequestContextDep, get_current_superuser
from src.app.api.deps.auth import AuthenticatedUser
from src.app.core.pagination import PaginationParams, get_pagination_params
from src.app.modules.auth.schemas import (
    PasskeyCredentialList,
    PasskeyDeleteResponse,
)
from src.app.modules.security.schemas import (
    AutoBanConfig,
    AutoBanConfigUpdate,
    BanActionResponse,
    BannedIPsResponse,
    IPAddressRequest,
    IPFilterConfig,
    IPFilterConfigUpdate,
    IPListsResponse,
    IPListUpdateResponse,
    IPViolationStats,
    ViolationStats,
)
from src.app.modules.security.service import SecurityService, get_security_service

router = APIRouter(prefix="/security", tags=["security"])

logger = get_logger(__name__)

SecurityServiceDep = Annotated[SecurityService, Depends(get_security_service)]


# ============================================================================
# User-facing Endpoints (Passkey Management)
# ============================================================================


@router.get("/passkeys", response_model=PasskeyCredentialList)
async def list_passkeys(
    ctx: RequestContextDep,
    service: SecurityServiceDep,
    pagination: Annotated[PaginationParams, Depends(get_pagination_params)],
) -> PasskeyCredentialList:
    """List user's passkey credentials (paginated).

    This is a convenience endpoint that wraps /auth/passkey/credentials
    for backward compatibility with the old backend.

    .. deprecated::
        Legacy frontend compatibility shim around
        ``GET /auth/passkey/credentials``. New clients should call the
        canonical ``/auth`` endpoint directly.
    """
    page = await service.list_passkeys(
        db=ctx.db,
        user_id=ctx.user_id,
        offset=pagination.offset,
        limit=pagination.limit,
    )
    return PasskeyCredentialList(
        items=page["items"],
        total=page["total"],
        page=page["page"],
        per_page=page["per_page"],
        has_next=page["has_next"],
        has_previous=page["has_previous"],
    )


@router.delete("/passkeys/{passkey_id}", response_model=PasskeyDeleteResponse)
async def delete_passkey(
    passkey_id: int,
    ctx: RequestContextDep,
    service: SecurityServiceDep,
) -> PasskeyDeleteResponse:
    """Delete a passkey credential.

    This is a convenience endpoint that wraps /auth/passkey/credentials/{credential_id}
    for backward compatibility with the old backend.

    .. deprecated::
        Legacy frontend compatibility shim around
        ``DELETE /auth/passkey/credentials/{credential_id}``. New clients
        should call the canonical ``/auth`` endpoint directly.
    """
    await service.delete_passkey(db=ctx.db, user_id=ctx.user_id, credential_id=passkey_id)
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
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],
    service: SecurityServiceDep,
) -> IPListUpdateResponse:
    """Add an IP address to the whitelist.

    Requires superuser privileges. Whitelisted IPs are allowed access
    regardless of blacklist status. Can optionally set an expiration time.

    - **ip_address**: IPv4 or IPv6 address
    - **reason**: Optional reason for whitelisting
    - **expires_at**: Optional expiration datetime
    """
    success = await service.add_to_whitelist(
        request.ip_address,
        reason=request.reason,
        expires_at=request.expires_at,
        added_by=current_user.id,
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
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],  # noqa: ARG001
    service: SecurityServiceDep,
) -> IPListUpdateResponse:
    """Remove an IP address from the whitelist.

    Requires superuser privileges.

    - **ip_address**: IPv4 or IPv6 address to remove
    """
    success = await service.remove_from_whitelist(ip_address)

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
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],
    service: SecurityServiceDep,
) -> IPListUpdateResponse:
    """Add an IP address to the blacklist.

    Requires superuser privileges. Blacklisted IPs are blocked from
    accessing the API. Can optionally set an expiration time.

    - **ip_address**: IPv4 or IPv6 address
    - **reason**: Optional reason for blacklisting
    - **expires_at**: Optional expiration datetime
    """
    success = await service.add_to_blacklist(
        request.ip_address,
        reason=request.reason,
        expires_at=request.expires_at,
        added_by=current_user.id,
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
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],  # noqa: ARG001
    service: SecurityServiceDep,
) -> IPListUpdateResponse:
    """Remove an IP address from the blacklist.

    Requires superuser privileges.

    - **ip_address**: IPv4 or IPv6 address to remove
    """
    success = await service.remove_from_blacklist(ip_address)

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
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],  # noqa: ARG001
    service: SecurityServiceDep,
) -> IPListsResponse:
    """Get both whitelist and blacklist with all entries.

    Requires superuser privileges.

    Returns current configuration status and all IP entries
    with their metadata (added time, reason, expiration).
    """
    return await service.get_ip_lists()


@router.get(
    "/admin/config",
    response_model=IPFilterConfig,
    dependencies=[Depends(get_current_superuser)],
)
async def get_ip_filter_config(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],  # noqa: ARG001
    service: SecurityServiceDep,
) -> IPFilterConfig:
    """Get current IP filter configuration.

    Requires superuser privileges.

    Returns the current configuration values for IP filtering.
    Note: Configuration changes require application restart to take effect.
    """
    return service.get_ip_filter_config()


@router.patch(
    "/admin/config",
    response_model=IPFilterConfig,
    dependencies=[Depends(get_current_superuser)],
)
async def update_ip_filter_config(
    config_update: IPFilterConfigUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],
    service: SecurityServiceDep,
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
    return await service.update_ip_filter_config(config_update, actor_id=current_user.id)


# ============================================================================
# Admin Endpoints (Auto-ban Management)
# ============================================================================


@router.get(
    "/admin/auto-ban/config",
    response_model=AutoBanConfig,
    dependencies=[Depends(get_current_superuser)],
)
async def get_auto_ban_config(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],  # noqa: ARG001
    service: SecurityServiceDep,
) -> AutoBanConfig:
    """Get current auto-ban configuration.

    Requires superuser privileges.

    Returns the current auto-ban settings including threshold,
    duration, and escalation settings.
    """
    return service.get_auto_ban_config()


@router.patch(
    "/admin/auto-ban/config",
    response_model=AutoBanConfig,
    dependencies=[Depends(get_current_superuser)],
)
async def update_auto_ban_config(
    config_update: AutoBanConfigUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],
    service: SecurityServiceDep,
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
    return await service.update_auto_ban_config(config_update, actor_id=current_user.id)


@router.get(
    "/admin/auto-ban/banned",
    response_model=BannedIPsResponse,
    dependencies=[Depends(get_current_superuser)],
)
async def list_banned_ips(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],  # noqa: ARG001
    service: SecurityServiceDep,
) -> BannedIPsResponse:
    """Get all currently banned IP addresses.

    Requires superuser privileges.

    Returns a list of all IPs currently banned by the auto-ban system,
    including ban reason and time remaining.
    """
    return await service.list_banned_ips()


@router.post(
    "/admin/auto-ban/ban",
    response_model=BanActionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_superuser)],
)
async def manually_ban_ip(
    request: IPAddressRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],
    service: SecurityServiceDep,
) -> BanActionResponse:
    """Manually ban an IP address.

    Requires superuser privileges.

    This endpoint allows manual banning of an IP address outside of
    the automatic rate limit violation system.

    - **ip_address**: IPv4 or IPv6 address to ban
    - **reason**: Optional reason for the ban
    """
    success = await service.ban_ip(
        request.ip_address,
        reason=request.reason or f"Manual ban by admin {current_user.id}",
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
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],  # noqa: ARG001
    service: SecurityServiceDep,
) -> BanActionResponse:
    """Remove ban from an IP address.

    Requires superuser privileges.

    - **ip_address**: IPv4 or IPv6 address to unban
    """
    if not service.is_valid_ip(ip_address):
        return BanActionResponse(
            success=False,
            ip_address=ip_address,
            action="unbanned",
            message=f"Invalid IP address format: {ip_address}",
        )

    success = await service.unban_ip(ip_address)

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
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],  # noqa: ARG001
    service: SecurityServiceDep,
) -> ViolationStats:
    """Get violation statistics.

    Requires superuser privileges.

    Returns aggregate statistics about rate limit violations across
    all tracked IP addresses.
    """
    return await service.get_violation_statistics()


@router.get(
    "/admin/auto-ban/stats/{ip_address}",
    response_model=IPViolationStats,
    dependencies=[Depends(get_current_superuser)],
)
async def get_ip_violation_stats(
    ip_address: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],  # noqa: ARG001
    service: SecurityServiceDep,
) -> IPViolationStats:
    """Get violation statistics for a specific IP.

    Requires superuser privileges.

    - **ip_address**: IPv4 or IPv6 address to check

    Returns violation count and ban status for the specified IP.
    """
    return await service.get_ip_violation_stats(ip_address)
