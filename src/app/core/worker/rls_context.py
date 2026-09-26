"""RLS context management for ARQ background jobs.

Provides context managers for setting RLS context in background jobs:
- job_tenant_context: Set tenant context for tenant-scoped jobs
- job_bypass_context: Bypass RLS for system jobs with audit logging
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.core.logger import get_logger
from src.app.core.worker.audit import require_bypass_recorder

logger = get_logger(__name__)


@asynccontextmanager
async def job_tenant_context(
    tenant_id: int | str | UUID,
    user_id: int | str | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Set RLS context for background job execution.

    Args:
        tenant_id: Tenant ID to set as current tenant
        user_id: Optional user ID to set as current user

    Yields:
        AsyncSession: Database session with RLS context set

    Example:
        async def deliver_webhook_task(ctx, delivery_id, tenant_id):
            async with job_tenant_context(tenant_id) as db:
                await deliver(db, delivery_id)
    """
    from src.app.core.db.rls import rls_context
    from src.app.core.tenancy.lifecycle import bind_tenant, fabricate_job_tenant, preserved_tenant

    # A6: save/set/restore and the minimal-context fabrication live behind
    # the tenancy lifecycle seam — this adapter only owns the DB session.
    with preserved_tenant():
        bind_tenant(fabricate_job_tenant(tenant_id))

        async with local_session() as session:
            # Use rls_context so tenant variables survive transactions and commits.
            user_id_str = str(user_id) if user_id else None
            async with rls_context(session, str(tenant_id), user_id_str):
                logger.debug("Job RLS context set via rls_context | tenant_id=%s, user_id=%s", tenant_id, user_id_str)
                yield session


def _validate_bypass_reason(reason: str) -> str:
    """Validate the RLS-bypass reason before any bypass is granted.

    Runs BEFORE entering ``bypass_rls`` so a validation failure can never
    fire mid-context (bypass granted but audit record lost).
    """
    if not reason or not reason.strip():
        raise ValueError("Reason is required for RLS bypass (audit trail)")
    return reason


async def _record_bypass_audit(reason: str) -> None:
    """Record-then-proceed: attempt the bypass audit without gating the job.

    The audit trail must never block system jobs on audit infra, and a lost
    trail must never be silent: any failure (delivery or validation — the
    BEST_EFFORT policy only swallows delivery) is caught here and surfaced
    as the loud ``audit_bypass_unaudited`` error event (alert on it, count
    it) instead of propagating.
    """
    # The recorder is supplied by the audit module; see core/worker/audit.py.
    record_bypass = require_bypass_recorder()

    try:
        await record_bypass(reason)
    except Exception as exc:  # noqa: BLE001 — intentional: jobs never block on audit
        logger.error(
            "audit_bypass_unaudited",
            reason=reason,
            error_type=type(exc).__name__,
        )


@asynccontextmanager
async def job_bypass_context(reason: str) -> AsyncGenerator[AsyncSession, None]:
    """Bypass RLS for system jobs with audit logging.

    CRITICAL: All RLS bypasses are logged for security audit.

    Record-then-proceed: the ``system.rls_bypass`` record is attempted BEFORE
    the bypass is granted, and the grant is unconditional — a failed record
    surfaces via the ``audit_bypass_unaudited`` error event, never via raise.

    Args:
        reason: Required reason for bypassing RLS (for audit trail)

    Yields:
        AsyncSession: Database session with RLS bypass enabled

    Example:
        async def flush_audit_logs_task(ctx):
            async with job_bypass_context("Flushing audit logs") as db:
                await flush_audit_log_buffer(db)
    """
    _validate_bypass_reason(reason)

    from src.app.core.db.rls import bypass_rls

    # Record-then-proceed: the bypass grant below is unconditional. A failed
    # audit record surfaces via ``audit_bypass_unaudited``, never via raise.
    await _record_bypass_audit(reason)

    async with local_session() as session:
        # Use bypass_rls so the bypass flag survives transactions and commits.
        async with bypass_rls(session, reason):
            logger.warning(
                "Job RLS bypass enabled via bypass_rls | reason=%s | security_event=rls_bypass",
                reason,
            )
            yield session


__all__ = [
    "job_tenant_context",
    "job_bypass_context",
]
