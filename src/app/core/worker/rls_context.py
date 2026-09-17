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

logger = get_logger(__name__)

# Keep a strong reference to background tasks to prevent garbage collection mid-execution
_background_tasks = set()


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
                service = WebhookService(db)
                await service.deliver_webhook(delivery_id)
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


@asynccontextmanager
async def job_bypass_context(reason: str) -> AsyncGenerator[AsyncSession, None]:
    """Bypass RLS for system jobs with audit logging.

    CRITICAL: All RLS bypasses are logged for security audit.

    Args:
        reason: Required reason for bypassing RLS (for audit trail)

    Yields:
        AsyncSession: Database session with RLS bypass enabled

    Example:
        async def flush_audit_logs_task(ctx):
            async with job_bypass_context("Flushing audit logs") as db:
                await flush_audit_log_buffer(db)
    """
    if not reason:
        raise ValueError("Reason is required for RLS bypass (audit trail)")

    from src.app.core.db.rls import bypass_rls

    async with local_session() as session:
        # Use bypass_rls so the bypass flag survives transactions and commits.
        async with bypass_rls(session, reason):
            logger.warning(
                "Job RLS bypass enabled via bypass_rls | reason=%s | security_event=rls_bypass",
                reason,
            )

            # SECURITY: App-level audit logging for job RLS bypass
            # We publish a system-level audit event to ensure a durable trail
            # Use a background task to avoid blocking the current transaction
            import asyncio

            from src.app.modules.audit_logs.trail import audit
            task = asyncio.create_task(
                audit(
                    action="system.rls_bypass",
                    entity_type="system",
                    entity_id="rls_context",
                    details={"reason": reason},
                )
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

            yield session


__all__ = [
    "job_tenant_context",
    "job_bypass_context",
]
