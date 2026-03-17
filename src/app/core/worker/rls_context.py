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

            # SECURITY: Implement application-level audit logging for job RLS bypass
            # This is done in a separate asynchronous task to not block the job and
            # to ensure the audit log is stored even if the main transaction rolls back.
            try:
                from src.app.core.events.models import AuditLog

                async def _log_rls_bypass() -> None:
                    async with local_session() as audit_db:
                        from uuid import UUID
                        audit_record = AuditLog(
                            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
                            user_id=None,
                            action="security.rls_bypass",
                            entity_type="system",
                            entity_id="job",
                            details={"reason": reason},
                            ip_address="127.0.0.1",
                            user_agent="system_worker"
                        )
                        audit_db.add(audit_record)
                        await audit_db.commit()

                await _log_rls_bypass()
            except Exception as audit_e:
                logger.error("Failed to write RLS bypass to audit_logs table: %s", audit_e)

            yield session


__all__ = [
    "job_tenant_context",
    "job_bypass_context",
]
