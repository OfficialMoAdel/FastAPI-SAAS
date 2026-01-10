"""Authentication audit logging utilities.

Provides centralized audit logging for all authentication-related events.
Implements FR-027 (Success Logging) and FR-028 (Failed Attempt Logging).

Authentication Events:
- auth.magic_link.sent: Magic link sent to email
- auth.magic_link.verified: Magic link successfully verified
- auth.magic_link.failed: Magic link verification failed
- auth.sso.initiated: SSO authentication started
- auth.sso.completed: SSO authentication successful
- auth.sso.failed: SSO authentication failed
- auth.sso.connected: SSO provider connected to account
- auth.sso.disconnected: SSO provider disconnected
- auth.passkey.registered: New passkey registered
- auth.passkey.authenticated: Passkey authentication successful
- auth.passkey.failed: Passkey authentication failed
- auth.passkey.deleted: Passkey credential deleted
- auth.login.success: General login success
- auth.login.failed: General login failure
- auth.logout: User logout
"""

from typing import Any

from structlog import get_logger

from src.app.core.events import EventPriority, event_bus

logger = get_logger(__name__)


class AuthAuditLogger:
    """Centralized authentication audit logger.

    Provides structured logging for all authentication events.
    Logs are persisted via the event bus and audit log service.
    """

    @staticmethod
    async def log_event(
        action: str,
        tenant_id: int | None,
        user_id: int | None = None,
        details: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log an authentication event.

        Args:
            action: Event action (e.g., "auth.magic_link.sent")
            tenant_id: Tenant ID (required for multi-tenancy)
            user_id: User ID if known
            details: Additional event details
            ip_address: Client IP address
            user_agent: Client user agent string
        """
        event_data = {
            "action": action,
            "tenant_id": str(tenant_id) if tenant_id else None,
            "user_id": str(user_id) if user_id else None,
            "entity_type": "auth",
            "details": details or {},
            "ip_address": ip_address,
            "user_agent": user_agent,
        }

        # Log to structured logger
        logger.info(
            "auth_event",
            action=action,
            tenant_id=tenant_id,
            user_id=user_id,
            **event_data.get("details", {}),
        )

        # Publish to event bus for async persistence (if tenant_id is known)
        if tenant_id:
            try:
                await event_bus.publish(
                    "audit.log",
                    event_data,
                    priority=EventPriority.NORMAL,
                )
            except Exception as e:
                logger.error("audit_publish_failed", error=str(e), action=action)

    # Magic Link Events

    @classmethod
    async def log_magic_link_sent(
        cls,
        tenant_id: int | None,
        email: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log magic link sent event."""
        await cls.log_event(
            action="auth.magic_link.sent",
            tenant_id=tenant_id,
            details={"email": email},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    async def log_magic_link_verified(
        cls,
        tenant_id: int | None,
        user_id: int,
        email: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log magic link verified successfully."""
        await cls.log_event(
            action="auth.magic_link.verified",
            tenant_id=tenant_id,
            user_id=user_id,
            details={"email": email},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    async def log_magic_link_failed(
        cls,
        tenant_id: int | None,
        reason: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log magic link verification failure."""
        await cls.log_event(
            action="auth.magic_link.failed",
            tenant_id=tenant_id,
            details={"reason": reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    # SSO Events

    @classmethod
    async def log_sso_initiated(
        cls,
        tenant_id: int | None,
        provider: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log SSO authentication flow started."""
        await cls.log_event(
            action="auth.sso.initiated",
            tenant_id=tenant_id,
            details={"provider": provider},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    async def log_sso_completed(
        cls,
        tenant_id: int | None,
        user_id: int,
        provider: str,
        is_new_user: bool = False,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log SSO authentication successful."""
        await cls.log_event(
            action="auth.sso.completed",
            tenant_id=tenant_id,
            user_id=user_id,
            details={
                "provider": provider,
                "is_new_user": is_new_user,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    async def log_sso_failed(
        cls,
        tenant_id: int | None,
        provider: str,
        reason: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log SSO authentication failure."""
        await cls.log_event(
            action="auth.sso.failed",
            tenant_id=tenant_id,
            details={
                "provider": provider,
                "reason": reason,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    async def log_sso_connected(
        cls,
        tenant_id: int | None,
        user_id: int,
        provider: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log SSO provider connected to account."""
        await cls.log_event(
            action="auth.sso.connected",
            tenant_id=tenant_id,
            user_id=user_id,
            details={"provider": provider},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    async def log_sso_disconnected(
        cls,
        tenant_id: int | None,
        user_id: int,
        provider: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log SSO provider disconnected from account."""
        await cls.log_event(
            action="auth.sso.disconnected",
            tenant_id=tenant_id,
            user_id=user_id,
            details={"provider": provider},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    # Passkey Events

    @classmethod
    async def log_passkey_registered(
        cls,
        tenant_id: int | None,
        user_id: int,
        device_type: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log passkey registration."""
        await cls.log_event(
            action="auth.passkey.registered",
            tenant_id=tenant_id,
            user_id=user_id,
            details={"device_type": device_type},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    async def log_passkey_authenticated(
        cls,
        tenant_id: int | None,
        user_id: int,
        credential_id: int,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log passkey authentication success."""
        await cls.log_event(
            action="auth.passkey.authenticated",
            tenant_id=tenant_id,
            user_id=user_id,
            details={"credential_id": credential_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    async def log_passkey_failed(
        cls,
        tenant_id: int | None,
        reason: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log passkey authentication failure."""
        await cls.log_event(
            action="auth.passkey.failed",
            tenant_id=tenant_id,
            details={"reason": reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    async def log_passkey_deleted(
        cls,
        tenant_id: int | None,
        user_id: int,
        credential_id: int,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log passkey deletion."""
        await cls.log_event(
            action="auth.passkey.deleted",
            tenant_id=tenant_id,
            user_id=user_id,
            details={"credential_id": credential_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    # General Auth Events

    @classmethod
    async def log_login_success(
        cls,
        tenant_id: int | None,
        user_id: int,
        method: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log successful login."""
        await cls.log_event(
            action="auth.login.success",
            tenant_id=tenant_id,
            user_id=user_id,
            details={"method": method},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    async def log_login_failed(
        cls,
        tenant_id: int | None,
        method: str,
        reason: str,
        email: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log failed login attempt."""
        await cls.log_event(
            action="auth.login.failed",
            tenant_id=tenant_id,
            details={
                "method": method,
                "reason": reason,
                "email": email,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    async def log_logout(
        cls,
        tenant_id: int | None,
        user_id: int,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log user logout."""
        await cls.log_event(
            action="auth.logout",
            tenant_id=tenant_id,
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )


# Global instance for convenience
auth_audit = AuthAuditLogger()
