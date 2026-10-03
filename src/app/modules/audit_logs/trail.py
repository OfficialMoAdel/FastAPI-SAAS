"""Deep audit-trail interface with typed schemas, auto-tenant stamping, and log sanitization.

This module is the single, canonical interface for publishing audit events across the application.
It replaces raw, hand-built event_bus.publish("audit.log", {...}) payloads.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401  (type re-export for callers)
from sqlalchemy.sql.elements import ColumnElement
from structlog import get_logger

from src.app.core.config import settings
from src.app.core.db.scoped import criterion
from src.app.core.observability.log_sanitizer import LogSanitizer
from src.app.core.tenancy.context import get_current_tenant
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.audit_logs.schemas import AuditLogEventData

logger = get_logger(__name__)

SYSTEM_TENANT_ID = UUID(int=0)


class AuditGuarantee(StrEnum):
    """Delivery policy for the awaited audit writer, not a durability guarantee."""

    BEST_EFFORT = "best_effort"
    REQUIRED = "required"


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


# The audit_events.entity_id column is String(36) — sized for a UUID.
ENTITY_ID_MAX_LENGTH = 36


def _coerce_entity_id(entity_id: Any) -> str | None:
    """Coerce entity_id to string, bounded to the column width.

    S-27: this was a bare ``str(entity_id)`` with no bound, and no
    ``max_length`` exists anywhere in ``audit_logs/schemas.py`` — so an
    over-length id was accepted from any caller (e.g.
    ``SSOSettingsCreate.provider``, an uncapped free-text field) and only
    failed at INSERT time. In the buffered flush path a statement error aborts
    the whole multi-VALUES insert, so one poison row takes the entire batch
    with it, and the flush loop re-selects the same rows — a self-sustaining
    retry of an unresolvable row.

    Truncating (rather than rejecting) keeps the audit write best-effort, which
    is what `audit()` promises; the warning makes the truncation visible so the
    caller can be fixed at source.
    """
    if entity_id is None:
        return None
    coerced = str(entity_id)
    if len(coerced) <= ENTITY_ID_MAX_LENGTH:
        return coerced

    logger.warning(
        "audit_entity_id_truncated",
        original_length=len(coerced),
        max_length=ENTITY_ID_MAX_LENGTH,
    )
    return coerced[:ENTITY_ID_MAX_LENGTH]


async def audit(
    action: str,
    entity_type: str | None = None,
    entity_id: Any = None,
    tenant_id: UUID | None = None,
    user_id: Any = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    *,
    guarantee: AuditGuarantee | None = None,
    resource_type: str | None = None,
    resource_id: Any = None,
    use_tenant_context: bool = True,
) -> None:
    """Validate, sanitize, stamp, and deliver an audit event.

    REQUIRED awaits Redis buffer acceptance or an independent DB fallback commit;
    it does not promise crash-proof persistence or atomicity with business writes.
    There is no shutdown flush: entries still sitting in the Redis buffer when the
    process exits are delivered only by a later worker run. Paths that must persist
    an entry (compliance-critical writes) should call
    ``AuditLogService.create_log`` directly instead of relying on the buffer.
    BEST_EFFORT uses the same writer but logs delivery failures without raising.
    Validation errors and cancellation propagate under either policy. If omitted,
    guarantee follows settings.auth.AUDIT_LOG_FAILURE_MODE (unknown values fail
    closed to REQUIRED with a warning, never silently downgrade to BEST_EFFORT).

    Args:
        action: Dot-delimited action name (e.g. 'auth.login', 'user.created', 'system.rls_bypass')
        entity_type: Type of the affected entity (e.g. 'user', 'api_key', 'mfa')
        entity_id: Identifier of the affected entity
        tenant_id: Tenant UUID. If omitted, resolved from tenant context or SYSTEM_TENANT_ID
        user_id: ID of the user performing the action (int, str, or model/dict)
        details: Additional context metadata dictionary (automatically sanitized for sensitive data)
        ip_address: Client IP address
        user_agent: Client User-Agent string
        guarantee: Named delivery policy; None uses the configured failure mode.
        resource_type: Alias for entity_type for backward compatibility
        resource_id: Alias for entity_id for backward compatibility
        use_tenant_context: When False, a missing tenant_id falls back to the
            system sentinel WITHOUT consulting ambient tenant context. Auth-door
            (login/SSO) events must pass False so a stale request context can
            never misattribute them; the fallback reason is preserved in the
            details ``_tenant_fallback`` stamp.
    """
    resolved_entity_type = entity_type or resource_type
    resolved_entity_id = _coerce_entity_id(entity_id if entity_id is not None else resource_id)
    if tenant_id is not None:
        resolved_tenant_id = tenant_id
    elif use_tenant_context:
        resolved_tenant_id = _resolve_tenant_id(None)
    else:
        # P2: auth-door attribution is authoritative-or-system — ambient
        # context must never leak into login/SSO rows.
        resolved_tenant_id = system_tenant_id(reason="audit.auth_door_missing_tenant")
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
    payload["created_at"] = datetime.now(UTC).isoformat()
    if guarantee is None:
        # P3: unknown modes fail closed — an unrecognized value must never
        # silently downgrade to BEST_EFFORT (fail open).
        mode = settings.auth.AUDIT_LOG_FAILURE_MODE
        if mode == "fail_closed":
            guarantee = AuditGuarantee.REQUIRED
        elif mode == "fail_open":
            guarantee = AuditGuarantee.BEST_EFFORT
        else:
            logger.warning("audit_unknown_failure_mode", mode=mode)
            guarantee = AuditGuarantee.REQUIRED
    guarantee = AuditGuarantee(guarantee)

    from src.app.modules.audit_logs.service import buffer_audit_log

    try:
        await buffer_audit_log(payload)
    except Exception as exc:
        # Never log payloads, exception messages, or tracebacks containing inputs.
        logger.warning(
            "audit_delivery_failed", action=action, guarantee=guarantee.value, error_type=type(exc).__name__
        )
        if guarantee == AuditGuarantee.REQUIRED:
            raise

# =============================================================================
# Login-activity READ contract (I9)
# =============================================================================
# Single owner of the login-audit shape shared by the writer
# (audit_logs.service login audit, AuthService auth-door events) and every
# reader (dashboard aggregation). Previously the dashboard module owned its
# own copy of the action tuple and the `details.success` JSONB extraction,
# so a writer-side rename silently zeroed dashboard metrics.
#
# Writer contract: login rows use these action names with a JSONB boolean
# `details.success`. `->>` text extraction covers both a JSON boolean false
# and a string "false"; coalesce keeps rows with missing details from
# silently flipping the count.
LOGIN_AUDIT_ACTIONS: tuple[str, str] = ("auth.login", "auth.sso_login")


def login_activity_subquery(
    tenant_id: str,
    start: datetime | ColumnElement[datetime],
    end: datetime,
    *,
    metric: Literal["logins", "active_users", "failed_auth"] = "logins",
) -> ColumnElement[int]:
    """Scalar subquery over this tenant's audit login rows (no I/O).

    Args:
        tenant_id: Tenant for row isolation (criterion / RLS).
        start: Range start — a datetime literal or a SQL expression
            (e.g. the dashboard's COALESCE'd min-created subquery).
        end: Range end (datetime literal).
        metric: "logins" counts login rows; "active_users" counts distinct
            login actors; "failed_auth" counts failed password logins only
            (SSO rows carry their own provider-side failure semantics).

    Returns:
        A scalar subquery usable inside ``select(...)`` column lists.
    """
    base = [criterion(AuditLog, tenant_id), AuditLog.created_at >= start, AuditLog.created_at <= end]
    if metric == "active_users":
        stmt = select(func.count(func.distinct(AuditLog.user_id))).where(
            *base,
            AuditLog.action.in_(LOGIN_AUDIT_ACTIONS),
        )
    elif metric == "failed_auth":
        success_text = func.coalesce(AuditLog.details["success"].astext, "true")
        stmt = select(func.count(AuditLog.id)).where(
            *base,
            AuditLog.action == LOGIN_AUDIT_ACTIONS[0],
            success_text == "false",
        )
    else:  # "logins"
        stmt = select(func.count(AuditLog.id)).where(*base, AuditLog.action.in_(LOGIN_AUDIT_ACTIONS))
    return stmt.scalar_subquery()


__all__ = [
    "LOGIN_AUDIT_ACTIONS",
    "SYSTEM_TENANT_ID",
    "AuditGuarantee",
    "audit",
    "login_activity_subquery",
    "system_tenant_id",
]
