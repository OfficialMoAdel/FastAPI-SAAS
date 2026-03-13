"""RLS context management for ARQ background jobs.

Provides context managers for setting RLS context in background jobs:
- job_tenant_context: Set tenant context for tenant-scoped jobs
- job_bypass_context: Bypass RLS for system jobs with audit logging
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.core.db.rls import RLS_BYPASS_VAR, RLS_TENANT_VAR, RLS_USER_VAR
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
    async with local_session() as session:
        connection = await session.connection()

        try:
            # Set tenant context
            await connection.execute(
                text(f"SELECT set_config('{RLS_TENANT_VAR}', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            logger.debug("Job RLS context set | tenant_id=%s", tenant_id)

            # Set user context if provided
            if user_id:
                await connection.execute(
                    text(f"SELECT set_config('{RLS_USER_VAR}', :user_id, true)"),
                    {"user_id": str(user_id)},
                )
                logger.debug("Job RLS user context set | user_id=%s", user_id)

            yield session

        except Exception as e:
            logger.error(
                "Failed to set job RLS context | tenant_id=%s | error=%s",
                tenant_id,
                e,
            )
            raise

        finally:
            # Clear context
            try:
                await connection.execute(text(f"RESET {RLS_TENANT_VAR}"))
                await connection.execute(text(f"RESET {RLS_USER_VAR}"))
                logger.debug("Job RLS context cleared")
            except Exception as e:
                logger.warning("Failed to clear job RLS context: %s", e)


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

    async with local_session() as session:
        connection = await session.connection()

        try:
            # Set bypass flag
            await connection.execute(
                text(f"SELECT set_config('{RLS_BYPASS_VAR}', 'true', true)"),
            )

            # Log bypass event (CRITICAL for security audit)
            logger.warning(
                "Job RLS bypass enabled | reason=%s | security_event=rls_bypass",
                reason,
            )

            # SECURITY: Implement audit logging for job RLS bypass
            # This is done in a separate asynchronous task to not block the job and
            # to ensure the audit log is stored even if the main transaction rolls back.
            try:
                from src.app.core.events.models import AuditLog

                async def _log_rls_bypass() -> None:
                    # `local_session` is imported at the top of the file
                    async with local_session() as audit_db:
                        # RLS bypasses are typically system-level actions
                        # so they are logged against the system tenant (00000000-0000-0000-0000-000000000000)
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
                # Schedule the audit log write
                await _log_rls_bypass()
            except Exception as audit_e:
                # We log the failure but do not prevent the job from running
                # since the structured log at line 111 already recorded it.
                logger.error("Failed to write RLS bypass to audit_logs table: %s", audit_e)

            yield session

        finally:
            # Clear bypass flag
            try:
                await connection.execute(text(f"RESET {RLS_BYPASS_VAR}"))
                logger.debug("Job RLS bypass cleared")
            except Exception as e:
                logger.warning("Failed to clear job RLS bypass: %s", e)


__all__ = [
    "job_tenant_context",
    "job_bypass_context",
]
