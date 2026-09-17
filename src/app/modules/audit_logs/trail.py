"""Deep audit-trail interface with typed schemas, auto-tenant stamping, and log sanitization.

This module is the single, canonical interface for publishing audit events across the application.
It replaces raw, hand-built event_bus.publish("audit.log", {...}) payloads.
"""

from typing import Any
from uuid import UUID

from structlog import get_logger

from src.app.core.config import settings
from src.app.core.events import EventPriority, event_bus
from src.app.core.observability.log_sanitizer import LogSanitizer
from src.app.core.tenancy.context import get_current_tenant
from src.app.modules.audit_logs.schemas import AuditLogEventData

logger = get_logger(__name__)

SYSTEM_TENANT_ID = UUID(int=0)


def system_tenant_id(*, reason: str) -> UUID:
    """Name the tenant-less fallback at the call site.

    Tenant-less system events (RLS bypass, worker bootstrap) attribute to the
    ``SYSTEM_TENANT_ID`` sentinel (``UUID(int=0)``), but LOUDLY: every call
    logs, so silent cross-tenant attribution can never hide. Callers with a
    real tenant must pass it explicitly or run under an active tenant
    context — the sentinel is never a quiet default.

    Args:
        reason: Short stable label for the fallback site (e.g.
            ``"audit.resolve_tenant"`` or ``"audit.unparseable_tenant_id"``).

    Returns:
        The ``SYSTEM_TENANT_ID`` sentinel.
    """
    logger.warning("audit_tenant_fallback_to_system", action=reason)
    return SYSTEM_TENANT_ID


def _resolve_tenant_id(tenant_id: UUID | None = None) -> UUID:
    """Resolve tenant ID from explicit argument or context.

    P2-C: UUID is the sole tenant type — no str/int coercion.

    Args:
        tenant_id: Optional explicit tenant UUID (or None)

    Returns:
        Explicit UUID, context tenant UUID, or SYSTEM_TENANT_ID (logged via
        :func:`system_tenant_id`).
    """
    if tenant_id is not None:
        return tenant_id

    ctx_tenant = get_current_tenant()
    if ctx_tenant and ctx_tenant.tenant_id:
        return ctx_tenant.tenant_id

    return system_tenant_id(reason="audit.resolve_tenant")


def _coerce_user_id(user_id: Any) -> str | None:
    """Safely coerce user_id from int, str, User model, or dict."""
    if user_id is None:
        return None
    if isinstance(user_id, int | str):
        val = str(user_id).strip()
        return val if val else None
    if isinstance(user_id, dict) and "id" in user_id:
        return str(user_id["id"])
    if not isinstance(user_id, dict) and hasattr(user_id, "id"):
        return str(user_id.id)
    return str(user_id)


def _coerce_entity_id(entity_id: Any) -> str | None:
    """Safely coerce entity_id to string."""
    if entity_id is None:
        return None
    return str(entity_id)


async def audit(
    action: str,
    entity_type: str | None = None,
    entity_id: Any = None,
    tenant_id: UUID | None = None,
    user_id: Any = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    priority: EventPriority = EventPriority.NORMAL,
    *,
    resource_type: str | None = None,
    resource_id: Any = None,
) -> None:
    """Publish a typed, sanitized audit log event.

    Args:
        action: Dot-delimited action name (e.g. 'auth.login', 'user.created', 'system.rls_bypass')
        entity_type: Type of the affected entity (e.g. 'user', 'api_key', 'mfa')
        entity_id: Identifier of the affected entity
        tenant_id: Tenant UUID. If omitted, resolved from tenant context or SYSTEM_TENANT_ID
        user_id: ID of the user performing the action (int, str, or model/dict)
        details: Additional context metadata dictionary (automatically sanitized for sensitive data)
        ip_address: Client IP address
        user_agent: Client User-Agent string
        priority: Event publishing priority (default EventPriority.NORMAL)
        resource_type: Alias for entity_type for backward compatibility
        resource_id: Alias for entity_id for backward compatibility
    """
    resolved_entity_type = entity_type or resource_type
    resolved_entity_id = _coerce_entity_id(entity_id if entity_id is not None else resource_id)
    resolved_tenant_id = _resolve_tenant_id(tenant_id)
    if resolved_tenant_id == SYSTEM_TENANT_ID and tenant_id is None:
        # Sentinel attribution: enrich details so the row is greppable and
        # the fallback reason survives downstream (buffer → flush → insert).
        details = {**(details or {}), "_tenant_fallback": "system"}
    resolved_user_id = _coerce_user_id(user_id)
    # Sanitize details before publishing so secrets/tokens never reach Redis/bus
    sanitized_details = LogSanitizer.sanitize_dict(details or {})

    # Validate against canonical event schema
    event_data = AuditLogEventData(
        tenant_id=resolved_tenant_id,
        user_id=resolved_user_id,
        action=action,
        entity_type=resolved_entity_type,
        entity_id=resolved_entity_id,
        details=sanitized_details,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    payload = event_data.model_dump(mode="json")

    try:
        await event_bus.publish(
            "audit.log",
            payload,
            priority=priority,
        )
    except Exception as e:
        logger.warning("audit_publish_failed", action=action, error=str(e))
        fail_mode = getattr(getattr(settings, "auth", None), "AUDIT_LOG_FAILURE_MODE", "fail_closed")
        if fail_mode == "fail_closed":
            raise

__all__ = [
    "SYSTEM_TENANT_ID",
    "audit",
    "system_tenant_id",
]
