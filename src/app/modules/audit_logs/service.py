"""Audit logging service for tracking system actions."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any
from uuid import UUID

import orjson
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.events import EventPriority, event_bus
from src.app.core.utils import cache as redis_cache
from src.app.modules.audit_logs.models import AuditLog

logger = get_logger(__name__)

# Audit log batching configuration
AUDIT_LOG_BUFFER_KEY = "audit_log_buffer"
AUDIT_LOG_BATCH_SIZE = 100
AUDIT_LOG_MAX_FLUSH_PER_RUN = 1000


class AuditLogService:
    """Service for creating and querying audit logs."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize audit log service.

        Args:
            db: Database session
        """
        self.db = db

    async def create_log(
        self,
        tenant_id: UUID,
        action: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        user_id: int | None = None,
        details: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog:
        """Create an audit log entry.

        Args:
            tenant_id: Tenant ID (UUID FK)
            action: Action performed (e.g., "user.created", "post.updated")
            entity_type: Type of entity affected (e.g., "user", "post")
            entity_id: ID of the affected entity
            user_id: ID of user who performed the action (integer FK)
            details: Additional context as JSON
            ip_address: IP address of the request
            user_agent: User agent string

        Returns
        -------
            Created audit log entry
        """
        log_entry = AuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        self.db.add(log_entry)
        await self.db.commit()
        # Note: refresh() removed - caller discards return value, no server-generated fields needed

        logger.info(
            "audit_log_created",
            audit_id=str(log_entry.id),
            tenant_id=str(tenant_id),
            action=action,
            entity_type=entity_type,
        )

        return log_entry

    async def get_logs(
        self,
        tenant_id: UUID,
        user_id: UUID | None = None,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        """Query audit logs with filters.

        Args:
            tenant_id: Tenant ID (required for multi-tenancy)
            user_id: Filter by user ID
            action: Filter by action name
            entity_type: Filter by entity type
            entity_id: Filter by entity ID
            start_date: Filter logs after this date
            end_date: Filter logs before this date
            limit: Maximum number of results
            offset: Number of results to skip

        Returns
        -------
            List of audit log entries
        """
        query = select(AuditLog).where(AuditLog.tenant_id == tenant_id)

        if user_id:
            query = query.where(AuditLog.user_id == user_id)
        if action:
            query = query.where(AuditLog.action == action)
        if entity_type:
            query = query.where(AuditLog.entity_type == entity_type)
        if entity_id:
            query = query.where(AuditLog.entity_id == entity_id)
        if start_date:
            query = query.where(AuditLog.created_at >= start_date)
        if end_date:
            query = query.where(AuditLog.created_at <= end_date)

        query = query.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())


async def buffer_audit_log(log_data: dict[str, Any], db: AsyncSession | None = None) -> None:
    """Buffer an audit log entry for async batch insert.

    Uses Redis LPUSH for O(1) buffering instead of immediate DB write.
    Falls back to direct insert if Redis is unavailable.

    Args:
        log_data: Audit log data to buffer
        db: Optional DB session for fallback direct insert
    """
    if redis_cache.client is None:
        logger.warning("audit_log_redis_unavailable", action="direct_insert")
        await _direct_insert_log(log_data, db)
        return

    try:
        lpush_result = redis_cache.client.lpush(AUDIT_LOG_BUFFER_KEY, orjson.dumps(log_data))
        if hasattr(lpush_result, "__await__"):
            await lpush_result
    except RedisError as e:
        logger.warning("audit_log_buffer_failed", error=str(e), action="fallback_direct")
        await _direct_insert_log(log_data, db)


async def _direct_insert_log(log_data: dict[str, Any], db: AsyncSession | None = None) -> None:
    """Fallback: directly insert audit log when Redis unavailable.

    Args:
        log_data: Log data
        db: Optional DB session. If None, creates a new one.
    """
    if db:
        # Use provided session
        try:
            service = AuditLogService(db)
            await service.create_log(
                tenant_id=UUID(log_data["tenant_id"]),
                action=log_data["action"],
                entity_type=log_data.get("entity_type"),
                entity_id=log_data.get("entity_id"),
                user_id=int(log_data["user_id"]) if log_data.get("user_id") else None,
                details=log_data.get("details", {}),
                ip_address=log_data.get("ip_address"),
                user_agent=log_data.get("user_agent"),
            )
        except Exception as e:
            logger.error("audit_log_direct_insert_failed", error=str(e))
        return

    # Fallback to creating new session
    from src.app.core.db.database import async_get_db

    async with async_get_db() as db:
        try:
            service = AuditLogService(db)
            await service.create_log(
                tenant_id=UUID(log_data["tenant_id"]),
                action=log_data["action"],
                entity_type=log_data.get("entity_type"),
                entity_id=log_data.get("entity_id"),
                user_id=int(log_data["user_id"]) if log_data.get("user_id") else None,
                details=log_data.get("details", {}),
                ip_address=log_data.get("ip_address"),
                user_agent=log_data.get("user_agent"),
            )
        except Exception as e:
            logger.error("audit_log_direct_insert_failed", error=str(e))


async def flush_audit_log_buffer(max_flush_per_run: int | None = None, db: AsyncSession | None = None) -> int:
    """Flush buffered audit logs to database in batch.

    Atomically retrieves and removes logs from Redis, then bulk inserts to DB.
    Should be called periodically by a background worker.

    Args:
        max_flush_per_run: Maximum number of logs to flush in this run.
                           If None, uses AUDIT_LOG_MAX_FLUSH_PER_RUN config.
        db: Database session to use. If None, creates a new one.
            CRITICAL: Pass the session from job_bypass_context to ensure RLS bypass.

    Returns:
        Number of logs flushed
    """
    if redis_cache.client is None:
        return 0

    if max_flush_per_run is None:
        max_flush_per_run = AUDIT_LOG_MAX_FLUSH_PER_RUN

    # Determine how many logs to retrieve (min of batch size and max flush limit)
    flush_limit = min(AUDIT_LOG_BATCH_SIZE, max_flush_per_run)

    # Atomically get and remove up to flush_limit logs
    pipe = redis_cache.client.pipeline()
    pipe.lrange(AUDIT_LOG_BUFFER_KEY, 0, flush_limit - 1)
    pipe.ltrim(AUDIT_LOG_BUFFER_KEY, flush_limit, -1)
    results = await pipe.execute()

    logs_data = results[0]
    if not logs_data:
        return 0

    # Parse and create log entries
    log_entries = []
    for raw_data in logs_data:
        try:
            data = orjson.loads(raw_data)
            log_entry = AuditLog(
                tenant_id=UUID(data["tenant_id"]),
                action=data["action"],
                entity_type=data.get("entity_type"),
                entity_id=data.get("entity_id"),
                user_id=int(data["user_id"]) if data.get("user_id") else None,
                details=data.get("details", {}),
                ip_address=data.get("ip_address"),
                user_agent=data.get("user_agent"),
            )
            if data.get("created_at"):
                object.__setattr__(log_entry, "created_at", datetime.fromisoformat(data["created_at"]))
            log_entries.append(log_entry)
        except Exception as e:
            logger.error("audit_log_parse_failed", error=str(e), raw_data=raw_data[:100])

    if not log_entries:
        return 0

    # Helper to perform bulk insert
    async def _perform_bulk_insert(session: AsyncSession) -> bool:
        try:
            session.add_all(log_entries)
            await session.commit()
            logger.info("audit_logs_flushed", count=len(log_entries))
            return True
        except Exception as e:
            logger.error("audit_log_flush_failed", error=str(e), count=len(log_entries))
            await session.rollback()
            return False

    inserted = False

    if db:
        inserted = await _perform_bulk_insert(db)
    else:
        from src.app.core.db.database import async_get_db

        async with async_get_db() as session:
            try:
                inserted = await _perform_bulk_insert(session)
            finally:
                await session.close()

    if not inserted:
        # Re-push failed entries back to Redis to prevent data loss
        try:
            repush_pipe = redis_cache.client.pipeline()
            for entry_data in logs_data:
                repush_pipe.rpush(AUDIT_LOG_BUFFER_KEY, entry_data)
            await repush_pipe.execute()
            logger.warning(
                "audit_logs_repushed_after_failure",
                count=len(logs_data),
            )
        except Exception as repush_err:
            logger.critical(
                "audit_log_repush_failed_data_loss",
                error=str(repush_err),
                lost_count=len(logs_data),
            )
        return 0

    # Log if we hit the flush limit
    if len(log_entries) >= max_flush_per_run:
        remaining_result = redis_cache.client.llen(AUDIT_LOG_BUFFER_KEY)
        remaining = await remaining_result if hasattr(remaining_result, "__await__") else remaining_result
        logger.warning(
            "audit_log_flush_limit_reached",
            flushed=len(log_entries),
            limit=max_flush_per_run,
            remaining=remaining,
        )

    return len(log_entries)


# Event handler for audit logs
@event_bus.subscribe("audit.log")
async def handle_audit_log_event(data: dict[str, Any]) -> None:
    """Handle audit log events from the event bus.

    Buffers logs in Redis for batch insert instead of immediate DB write.
    This reduces DB IOPS by ~99% at scale (1000 events = 10 batches vs 1000 writes).

    Args:
        data: Event payload containing audit log details
    """
    log_data = {
        "tenant_id": data["tenant_id"],
        "action": data["action"],
        "entity_type": data.get("entity_type"),
        "entity_id": str(data["entity_id"]) if data.get("entity_id") else None,
        "user_id": data.get("user_id"),
        "details": data.get("details", {}),
        "ip_address": data.get("ip_address"),
        "user_agent": data.get("user_agent"),
        "created_at": datetime.now(UTC).isoformat(),
    }

    try:
        await buffer_audit_log(log_data)
    except Exception as e:
        # Broad exception handling is appropriate here to prevent
        # audit logging failures from crashing the main application flow
        logger.error("audit_log_event_failed", error=str(e), exc_info=True)


def audit_action(action: str, entity_type: str) -> Callable:
    """Decorator to automatically audit actions.

    Args:
        action: Action name (e.g., "post.created")
        entity_type: Entity type (e.g., "post")

    Returns
    -------
        Decorator function

    Example:
        @audit_action("post.created", "post")
        async def create_post(db: AsyncSession, tenant_id: UUID, data: PostCreate):
            post = Post(**data.model_dump(), tenant_id=tenant_id)
            db.add(post)
            await db.commit()
            return post
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Execute original function
            result = await func(*args, **kwargs)

            # Extract context from result and kwargs
            tenant_id = kwargs.get("tenant_id") or getattr(result, "tenant_id", None)
            user_id = kwargs.get("current_user_id") or kwargs.get("user_id")
            entity_id = getattr(result, "id", None)

            # Resolve tenant_id from context if missing
            if not tenant_id:
                try:
                    from src.app.core.tenancy.context import get_tenant_context

                    tenant = get_tenant_context()
                    if tenant:
                        tenant_id = tenant.id
                except ImportError:
                    pass

            # Publish audit event with error handling
            if tenant_id:

                async def _publish_audit_event() -> None:
                    """Publish audit event with error handling."""
                    try:
                        await event_bus.publish(
                            "audit.log",
                            {
                                "action": action,
                                "entity_type": entity_type,
                                "entity_id": str(entity_id) if entity_id else None,
                                "tenant_id": str(tenant_id),
                                "user_id": str(user_id) if user_id else None,
                                "details": {"result_id": str(getattr(result, "id", ""))},
                            },
                            priority=EventPriority.NORMAL,
                        )
                    except Exception as e:
                        logger.error("audit_publish_failed", error=str(e), action=action)

                asyncio.create_task(_publish_audit_event())

            return result

        return wrapper

    return decorator
