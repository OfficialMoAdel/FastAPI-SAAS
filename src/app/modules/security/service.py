"""Security module service layer.

The security module is an admin-only surface: IP whitelist/blacklist
administration, runtime configuration of the IP filter and the auto-ban
system, and a legacy passkey convenience shim. Until F1 it had no
service layer at all — ``router.py`` reached straight into the core
singletons (``ip_filter_service``, ``ban_ip``, ``get_violation_stats``,
``unban_ip``) and reshaped their raw dictionaries into response schemas
inline.

This module exists to fix that shape:

* **The core singletons are reached from ONE place.** The router no
  longer imports ``src.app.core.guard.ip_rules`` or
  ``src.app.core.security.auto_ban``; it calls the service. The core
  seam stays the same, but it is now a service-to-core edge rather than
  a router-to-core edge, which is the direction the layered architecture
  allows.
* **The raw-dict reshaping lives here, once.** ``IPListEntry`` and
  ``BannedIPEntry`` are built from the core dictionaries in one place
  instead of two list comprehensions per endpoint in the router.
* **The runtime-config write and its audit are one operation.** The
  router used to call ``apply_and_store_runtime_config`` and then
  ``audit`` as two sibling statements in the handler body. They are
  inseparable — an unaudited config change is a compliance hole — so
  they are one service method now.
* **The passkey list is paginated in the DATABASE.** The old code
  fetched every credential for the user and sliced the list in RAM. The
  page is now selected with ``LIMIT``/``OFFSET`` and the total comes
  from the same round trip, so the read cost tracks the page size
  rather than the user's whole credential set.

The passkey collaborator is injected, so the passkey list can be exercised
without the auth module's factory. The core collaborators (IP rules, auto-ban,
runtime config, audit, settings) are module-scope imports: they are
infrastructure seams with their own test doubles, and threading all of them
through the constructor would add arguments without adding a seam. The
request context (``ctx.db``) stays out of the business logic; the db is passed
per call. ``get_security_service`` is the DI factory, following the pattern
used by ``user_preferences/service.py``.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.guard.ip_rules import ip_filter_service
from src.app.core.pagination import build_paginated_response
from src.app.core.security import runtime_config
from src.app.core.security.auto_ban import (
    ban_ip,
    get_all_banned_ips,
    get_violation_stats,
    unban_ip,
)
from src.app.modules.audit_logs import AuditGuarantee, audit
from src.app.modules.auth import PasskeyService, get_passkey_service
from src.app.modules.security.schemas import (
    AutoBanConfig,
    AutoBanConfigUpdate,
    BannedIPEntry,
    BannedIPsResponse,
    IPFilterConfig,
    IPFilterConfigUpdate,
    IPListEntry,
    IPListsResponse,
    IPViolationStats,
    ViolationStats,
)

__all__ = ["SecurityService", "get_security_service"]


def _is_valid_ip(ip_address: str) -> bool:
    """Whether ``ip_address`` parses as an IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(ip_address)
    except ValueError:
        return False
    return True


class SecurityService:
    """Business logic for the security module.

    The service is stateless: every method takes the session (or nothing
    at all, for the Redis-backed IP lists) and returns a response
    schema. There is no instance state to carry between calls, so the
    factory hands out a fresh instance per request.
    """

    def __init__(self, passkey_service: PasskeyService | None = None) -> None:
        """Bind the collaborators the service needs.

        Args:
            passkey_service: The passkey collaborator. Defaults to the
                auth module's own singleton, which is the same
                collaborator the router used directly.
        """
        if passkey_service is None:
            passkey_service = get_passkey_service()
        self.passkey_service = passkey_service

    # ========================================================================
    # Passkeys (legacy convenience shim)
    # ========================================================================

    async def list_passkeys(
        self,
        db: AsyncSession,
        user_id: int,
        *,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        """List a user's passkey credentials, one page at a time.

        The page is selected in the database. The previous
        implementation loaded every credential the user had and sliced
        the list in RAM, so the read cost grew with the user's whole
        credential set no matter how small the requested page was.

        Args:
            db: The request-scoped session.
            user_id: The user whose credentials are listed.
            offset: The row offset of the requested page.
            limit: The maximum number of rows in the page.

        Returns:
            The paginated envelope: ``items``, ``total``, ``page``,
            ``per_page``, ``has_next``, ``has_previous``.
        """
        page = await self.passkey_service.list_credentials_page(
            db=db,
            user_id=user_id,
            offset=offset,
            limit=limit,
        )
        total = int(page["total_count"])
        # The caller knows the page number and the page size; the service
        # needs the page number to build the envelope, and it derives it
        # from the offset so the two can never disagree.
        per_page = limit
        current_page = (offset // per_page) + 1 if per_page else 1
        return build_paginated_response(
            items=page["data"],
            total=total,
            page=current_page,
            per_page=per_page,
        )

    async def delete_passkey(self, db: AsyncSession, user_id: int, credential_id: int) -> None:
        """Delete one of a user's passkey credentials.

        The collaborator keeps the soft-404 semantics: a credential that
        belongs to another user is reported as not found, never as
        forbidden, so credential existence is not enumerable.
        """
        await self.passkey_service.delete_credential(db=db, user_id=user_id, credential_id=credential_id)

    # ========================================================================
    # IP lists
    # ========================================================================

    async def add_to_whitelist(
        self,
        ip_address: str,
        *,
        reason: str | None,
        expires_at: Any | None,
        added_by: int,
    ) -> bool:
        """Add an IP to the whitelist. Returns whether it was added."""
        return await ip_filter_service.add_to_whitelist(
            ip_address=ip_address,
            reason=reason,
            expires_at=expires_at,
            added_by=added_by,
        )

    async def remove_from_whitelist(self, ip_address: str) -> bool:
        """Remove an IP from the whitelist. Returns whether it was there."""
        return await ip_filter_service.remove_from_whitelist(ip_address)

    async def add_to_blacklist(
        self,
        ip_address: str,
        *,
        reason: str | None,
        expires_at: Any | None,
        added_by: int,
    ) -> bool:
        """Add an IP to the blacklist. Returns whether it was added."""
        return await ip_filter_service.add_to_blacklist(
            ip_address=ip_address,
            reason=reason,
            expires_at=expires_at,
            added_by=added_by,
        )

    async def remove_from_blacklist(self, ip_address: str) -> bool:
        """Remove an IP from the blacklist. Returns whether it was there."""
        return await ip_filter_service.remove_from_blacklist(ip_address)

    async def get_ip_lists(self) -> IPListsResponse:
        """Return both IP lists with the current filter configuration."""
        whitelist_raw = await ip_filter_service.get_whitelist()
        blacklist_raw = await ip_filter_service.get_blacklist()

        return IPListsResponse(
            whitelist=[self._to_list_entry(entry) for entry in whitelist_raw],
            blacklist=[self._to_list_entry(entry) for entry in blacklist_raw],
            whitelist_enabled=settings.security.IP_WHITELIST_ENABLED,
            blacklist_enabled=settings.security.IP_BLACKLIST_ENABLED,
            whitelist_bypass_rate_limit=settings.security.IP_WHITELIST_BYPASS_RATE_LIMIT,
        )

    @staticmethod
    def _to_list_entry(entry: dict[str, Any]) -> IPListEntry:
        """Shape one raw IP-list dictionary into its response schema."""
        return IPListEntry(
            ip_address=entry["ip_address"],
            added_at=entry["added_at"],
            reason=entry.get("reason"),
            expires_at=entry.get("expires_at"),
            added_by=entry.get("added_by"),
        )

    # ========================================================================
    # Runtime configuration
    # ========================================================================

    def get_ip_filter_config(self) -> IPFilterConfig:
        """Return the current IP filter configuration."""
        return IPFilterConfig(
            whitelist_enabled=settings.security.IP_WHITELIST_ENABLED,
            blacklist_enabled=settings.security.IP_BLACKLIST_ENABLED,
            whitelist_bypass_rate_limit=settings.security.IP_WHITELIST_BYPASS_RATE_LIMIT,
        )

    async def update_ip_filter_config(
        self,
        config_update: IPFilterConfigUpdate,
        *,
        actor_id: int,
    ) -> IPFilterConfig:
        """Apply an IP filter configuration change and audit it.

        The write and the audit are one operation: a configuration change
        that is not recorded in the audit trail is a compliance hole, and
        the two were sibling statements in the router where a future
        early return between them would have skipped the audit.

        Args:
            config_update: The requested change; unset fields are left alone.
            actor_id: The superuser who made the change.

        Returns:
            The configuration as it stands after the change.
        """
        updates: dict[str, Any] = {}
        if config_update.whitelist_enabled is not None:
            updates["IP_WHITELIST_ENABLED"] = config_update.whitelist_enabled
        if config_update.blacklist_enabled is not None:
            updates["IP_BLACKLIST_ENABLED"] = config_update.blacklist_enabled
        if config_update.whitelist_bypass_rate_limit is not None:
            updates["IP_WHITELIST_BYPASS_RATE_LIMIT"] = config_update.whitelist_bypass_rate_limit

        await runtime_config.apply_and_store_runtime_config(updates)

        await audit(
            action="security.ip_filter_config_updated",
            entity_type="security_config",
            entity_id="ip_filter",
            tenant_id=None,
            user_id=actor_id,
            details={"updates": updates},
            guarantee=AuditGuarantee.BEST_EFFORT,
        )

        return self.get_ip_filter_config()

    def get_auto_ban_config(self) -> AutoBanConfig:
        """Return the current auto-ban configuration."""
        return AutoBanConfig(
            enabled=settings.security.AUTO_BAN_ENABLED,
            threshold=settings.security.AUTO_BAN_THRESHOLD,
            duration=settings.security.AUTO_BAN_DURATION,
            escalation=settings.security.AUTO_BAN_ESCALATION,
        )

    async def update_auto_ban_config(
        self,
        config_update: AutoBanConfigUpdate,
        *,
        actor_id: int,
    ) -> AutoBanConfig:
        """Apply an auto-ban configuration change and audit it.

        Args:
            config_update: The requested change; unset fields are left alone.
            actor_id: The superuser who made the change.

        Returns:
            The configuration as it stands after the change.
        """
        updates: dict[str, Any] = {}
        if config_update.enabled is not None:
            updates["AUTO_BAN_ENABLED"] = config_update.enabled
        if config_update.threshold is not None:
            updates["AUTO_BAN_THRESHOLD"] = config_update.threshold
        if config_update.duration is not None:
            updates["AUTO_BAN_DURATION"] = config_update.duration
        if config_update.escalation is not None:
            updates["AUTO_BAN_ESCALATION"] = config_update.escalation

        await runtime_config.apply_and_store_runtime_config(updates)

        await audit(
            action="security.auto_ban_config_updated",
            entity_type="security_config",
            entity_id="auto_ban",
            tenant_id=None,
            user_id=actor_id,
            details={"updates": updates},
            guarantee=AuditGuarantee.BEST_EFFORT,
        )

        return self.get_auto_ban_config()

    # ========================================================================
    # Auto-ban
    # ========================================================================

    async def list_banned_ips(self) -> BannedIPsResponse:
        """Return every currently banned IP with its ban metadata."""
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
        return BannedIPsResponse(banned_ips=banned, total_count=len(banned))

    async def ban_ip(self, ip_address: str, *, reason: str) -> bool:
        """Ban an IP manually. Returns whether the ban was applied."""
        return await ban_ip(ip_address, reason=reason)

    async def unban_ip(self, ip_address: str) -> bool:
        """Remove a ban. Returns whether the IP was actually banned."""
        return await unban_ip(ip_address)

    async def get_violation_statistics(self) -> ViolationStats:
        """Return aggregate violation statistics across all tracked IPs."""
        stats = await get_violation_stats()
        if isinstance(stats, dict):
            return ViolationStats(
                total_ips=stats.get("total_ips", 0),
                total_violations=stats.get("total_violations", 0),
            )
        return ViolationStats(total_ips=0, total_violations=0)

    async def get_ip_violation_stats(self, ip_address: str) -> IPViolationStats:
        """Return the violation statistics for one IP.

        An unparseable address yields zeroed statistics rather than an
        error: the endpoint is a read, and the caller gets a well-formed
        response either way.
        """
        if not _is_valid_ip(ip_address):
            return IPViolationStats(ip=ip_address, violations=0, total_ips=0, total_violations=0)

        stats = await get_violation_stats(ip_address)
        violations = stats.get("violations", 0) if isinstance(stats, dict) else 0

        return IPViolationStats(
            ip=ip_address,
            violations=violations,
            total_ips=1,
            total_violations=violations,
        )

    @staticmethod
    def is_valid_ip(ip_address: str) -> bool:
        """Whether ``ip_address`` parses as an IPv4 or IPv6 address."""
        return _is_valid_ip(ip_address)


def get_security_service() -> SecurityService:
    """DI factory for :class:`SecurityService`.

    The service is stateless, so a fresh instance per request costs
    nothing and keeps the collaborators explicit.
    """
    return SecurityService()
