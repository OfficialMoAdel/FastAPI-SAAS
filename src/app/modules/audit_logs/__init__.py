"""Audit logging module for tracking system actions."""

from src.app.core.worker.audit import register_bypass_recorder
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.audit_logs.schemas import (
    AuditLogCreate,
    AuditLogEventData,
    AuditLogList,
    AuditLogResponse,
)
from src.app.modules.audit_logs.service import AuditLogService
from src.app.modules.audit_logs.trail import SYSTEM_TENANT_ID, AuditGuarantee, audit, system_tenant_id

__all__ = [
    "AuditGuarantee",
    "AuditLog",
    "AuditLogCreate",
    "AuditLogEventData",
    "AuditLogList",
    "AuditLogResponse",
    "AuditLogService",
    "SYSTEM_TENANT_ID",
    "audit",
    "system_tenant_id",
]


async def _record_rls_bypass(reason: str) -> None:
    """Record a ``system.rls_bypass`` event for the worker RLS seam.

    Supplied to ``core.worker.audit`` at import so a core worker can audit an
    RLS bypass without core importing this module (H-5). The worker keeps the
    record-then-proceed policy; the event shape and the BEST_EFFORT delivery
    guarantee live here.
    """
    await audit(
        action="system.rls_bypass",
        entity_type="system",
        entity_id="rls_context",
        details={"reason": reason},
        guarantee=AuditGuarantee.BEST_EFFORT,
    )


register_bypass_recorder(_record_rls_bypass)
