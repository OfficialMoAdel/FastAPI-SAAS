"""Audit logging service for tracking system actions."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import UUID

import orjson
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.db.database import get_db_context
from src.app.core.db.tenant_crud import tenant_scope
from src.app.core.utils import cache as redis_cache
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.audit_logs.schemas import AuditLogEventData
from src.app.modules.audit_logs.trail import SYSTEM_TENANT_ID

if TYPE_CHECKING:
    pass


# A13: export ceiling lives with the service that enforces it (was in router).
EXPORT_MAX_RECORDS = 100_000


class _AuditFilter(NamedTuple):
    action: str | None
    user_id: int | None
    entity_type: str | None
    start_date: datetime | None
    end_date: datetime | None


def _compose_filters(stmt, tenant_id, f: _AuditFilter):
    """A13: single source of truth for audit-log list/export filters."""
    stmt = stmt.where(tenant_scope(AuditLog, tenant_id))
    if f.action:
        stmt = stmt.where(AuditLog.action == f.action)
    if f.user_id:
        stmt = stmt.where(AuditLog.user_id == f.user_id)
    if f.entity_type:
        stmt = stmt.where(AuditLog.entity_type == f.entity_type)
    if f.start_date:
        stmt = stmt.where(AuditLog.created_at >= f.start_date)
    if f.end_date:
        stmt = stmt.where(AuditLog.created_at <= f.end_date)
    return stmt


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

    async def list_actions(self, tenant_id) -> list[str]:
        """A13: unique action types for a tenant (was inline in router)."""
        result = await self.db.execute(
            select(AuditLog.action).where(tenant_scope(AuditLog, tenant_id)).distinct()
        )
        return sorted(result.scalars().all())

    async def get_log(self, log_id: UUID, tenant_id: UUID) -> AuditLog | None:
        """Get a single audit log entry by ID scoped to tenant."""
        stmt = select(AuditLog).where(
            AuditLog.id == log_id,
            tenant_scope(AuditLog, tenant_id),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
    async def export_count(self, tenant_id, f: _AuditFilter) -> int:
        """A13: count of rows matching the export filter."""
        stmt = _compose_filters(select(func.count()).select_from(AuditLog), tenant_id, f)
        result = await self.db.execute(stmt)
        count: int = result.scalar_one()
        return int(count)

    def export_stmt(self, tenant_id, f: _AuditFilter):
        """P2-2: export SELECT statement; keeps query assembly in the service layer.

        The router orders/executes the returned statement but never composes
        filter criteria itself.
        """
        return _compose_filters(select(AuditLog), tenant_id, f)

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

    async def query_logs(
        self,
        tenant_id: UUID,
        user_id: int | None = None,  # FIX: Changed from UUID to int to match model
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        """Query audit logs with filters.

        P2-5: Uses joinedload to prevent N+1 queries when accessing user relationship.

        Args:
            tenant_id: Tenant ID (required for multi-tenancy)
            user_id: Filter by user ID (int, matches user.id FK)
            action: Filter by action name
            entity_type: Filter by entity type
            entity_id: Filter by entity ID
            start_date: Filter logs after this date
            end_date: Filter logs before this date
            limit: Maximum number of results
            offset: Number of results to skip

        Returns
        -------
            List of audit log entries with eager-loaded user data
        """
        # AuditLogRead does not project nested user fields; avoid unnecessary LEFT OUTER JOIN
        query = select(AuditLog).where(tenant_scope(AuditLog, tenant_id))

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

        # P2-5: tie-breaker on id so equal created_at values order deterministically
        query = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.unique().scalars().all())

    async def query_logs_with_total(
        self,
        tenant_id: UUID,
        user_id: int | None = None,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[AuditLog], int]:
        """R1-04: fetch a page of logs and the total count in ONE query.

        ``COUNT(*) OVER ()`` is evaluated over the filtered set before
        LIMIT/OFFSET, so the page and the total come back in a single
        round-trip. This replaces the previous count_logs + query_logs pair;
        running those concurrently via asyncio.gather on one AsyncSession is
        not safe, so the window function is the correct consolidation.
        """
        total_col = func.count().over().label("total")
        query = select(AuditLog, total_col).where(tenant_scope(AuditLog, tenant_id))

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

        # P2-5: tie-breaker on id so equal created_at values order deterministically
        query = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).offset(offset)

        result = await self.db.execute(query)
        rows = result.unique().all()
        if rows:
            total = int(rows[0][1])
            logs = [row[0] for row in rows]
            return logs, total
        if offset == 0:
            return [], 0
        # Out-of-range page: window function yields no rows, so total is
        # unavailable. Fall back to a cheap COUNT so pagination metadata stays
        # truthful (total must reflect the filtered set even when the page is empty).
        count_query = select(func.count()).select_from(AuditLog).where(tenant_scope(AuditLog, tenant_id))
        if user_id:
            count_query = count_query.where(AuditLog.user_id == user_id)
        if action:
            count_query = count_query.where(AuditLog.action == action)
        if entity_type:
            count_query = count_query.where(AuditLog.entity_type == entity_type)
        if entity_id:
            count_query = count_query.where(AuditLog.entity_id == entity_id)
        if start_date:
            count_query = count_query.where(AuditLog.created_at >= start_date)
        if end_date:
            count_query = count_query.where(AuditLog.created_at <= end_date)
        count_result = await self.db.execute(count_query)
        return [], count_result.scalar_one()

    async def get_audit_logs_for_tenant_async(
        self,
        tenant_id: UUID,
        user_id: int | None = None,  # FIX: Changed from UUID to int to match model
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        """Get audit logs for a specific tenant in background context."""
        async with get_db_context() as background_db:
            bg_service = AuditLogService(background_db)
            return await bg_service.query_logs(
                tenant_id=tenant_id,
                user_id=user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                offset=offset,
            )


async def buffer_audit_log(log_data: dict[str, Any], db: AsyncSession | None = None) -> None:
    """Buffer an audit log entry for async batch insert.

    Uses Redis LPUSH for O(1) buffering instead of immediate DB write.
    Falls back to direct insert if Redis is unavailable.

    Args:
        log_data: Audit log data to buffer
        db: Optional DB session for fallback direct insert
    """
    client = redis_cache.client
    if client is None:
        logger.warning("audit_log_redis_unavailable", action="direct_insert")
        await _direct_insert_log(log_data, db)
        return

    try:
        # BUG FIX: Push new items to the right side (tail) of the buffer list.
        # This converts the batch processor's execution behavior to strict FIFO, securely
        # preventing older events from starving endlessly natively under LPUSH + LTRIM bounds.
        await client.rpush(AUDIT_LOG_BUFFER_KEY, orjson.dumps(log_data))  # type: ignore[misc]
    except RedisError as e:
        logger.warning("audit_log_buffer_failed", error=str(e), action="fallback_direct")
        await _direct_insert_log(log_data, db)

async def _direct_insert_log(log_data: dict[str, Any], db: AsyncSession | None = None) -> None:
    """Fallback: directly insert audit log when Redis unavailable.

    Args:
        log_data: Log data
        db: Optional DB session. If None, creates a new one.
    """
    tenant_id_val = SYSTEM_TENANT_ID
    if log_data.get("tenant_id"):
        try:
            tenant_id_val = UUID(str(log_data["tenant_id"]))
        except (ValueError, TypeError):
            # P2-2: garbage tenant IDs fall back to SYSTEM (audit must never
            # crash the request), but log loudly so misconfig surfaces.
            logger.warning("audit_tenant_id_unparseable", raw_tenant_id=str(log_data["tenant_id"])[:64])
            tenant_id_val = SYSTEM_TENANT_ID

    user_id_val = None
    if log_data.get("user_id"):
        try:
            user_id_val = int(log_data["user_id"])
        except (ValueError, TypeError):
            user_id_val = None

    if db:
        # Use provided session
        try:
            service = AuditLogService(db)
            await service.create_log(
                tenant_id=tenant_id_val,
                action=log_data["action"],
                entity_type=log_data.get("entity_type"),
                entity_id=str(log_data["entity_id"]) if log_data.get("entity_id") is not None else None,
                user_id=user_id_val,
                details=log_data.get("details", {}),
                ip_address=log_data.get("ip_address"),
                user_agent=log_data.get("user_agent"),
            )
        except Exception as e:
            logger.error("audit_log_direct_insert_failed", error=str(e))
        return

    # Fallback to creating new session
    from src.app.core.db.database import get_db_context

    async with get_db_context() as db:
        try:
            service = AuditLogService(db)
            await service.create_log(
                tenant_id=tenant_id_val,
                action=log_data["action"],
                entity_type=log_data.get("entity_type"),
                entity_id=str(log_data["entity_id"]) if log_data.get("entity_id") is not None else None,
                user_id=user_id_val,
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
    client = redis_cache.client
    if client is None:
        return 0

    if max_flush_per_run is None:
        max_flush_per_run = AUDIT_LOG_MAX_FLUSH_PER_RUN

    # Determine how many logs to retrieve (min of batch size and max flush limit)
    flush_limit = min(AUDIT_LOG_BATCH_SIZE, max_flush_per_run)

    # Atomically read and trim the bounded buffer window in a single pipeline.
    async with client.pipeline() as pipe:
        pipe.lrange(AUDIT_LOG_BUFFER_KEY, 0, flush_limit - 1)
        pipe.ltrim(AUDIT_LOG_BUFFER_KEY, flush_limit, -1)
        results = await pipe.execute()
    logs_data = results[0] if results else []
    if not logs_data:
        return 0

    if not isinstance(logs_data, list):
        logs_data = [logs_data]

    # Parse and create log entries
    log_dicts = []
    for raw_data in logs_data:
        try:
            data = orjson.loads(raw_data)
            tenant_id_val = SYSTEM_TENANT_ID
            if data.get("tenant_id"):
                try:
                    tenant_id_val = UUID(str(data["tenant_id"]))
                except (ValueError, TypeError):
                    tenant_id_val = SYSTEM_TENANT_ID

            user_id_val = None
            if data.get("user_id"):
                try:
                    user_id_val = int(data["user_id"])
                except (ValueError, TypeError):
                    user_id_val = None

            log_dict = {
                "tenant_id": tenant_id_val,
                "action": data["action"],
                "entity_type": data.get("entity_type"),
                "entity_id": str(data["entity_id"]) if data.get("entity_id") is not None else None,
                "user_id": user_id_val,
                "details": data.get("details", {}),
                "ip_address": data.get("ip_address"),
                "user_agent": data.get("user_agent"),
            }
            if data.get("created_at"):
                log_dict["created_at"] = datetime.fromisoformat(data["created_at"])
            log_dicts.append(log_dict)
        except Exception as e:
            logger.error("audit_log_parse_failed", error=str(e), raw_data=raw_data[:100])

    if not log_dicts:
        return 0

    # Helper to perform bulk insert
    async def _perform_bulk_insert(session: AsyncSession) -> bool:
        try:
            from sqlalchemy import insert

            await session.execute(insert(AuditLog).values(log_dicts))
            await session.commit()
            logger.info("audit_logs_flushed", count=len(log_dicts))
            return True
        except Exception as e:
            logger.error("audit_log_flush_failed", error=str(e), count=len(log_dicts))
            await session.rollback()
            return False

    inserted = False

    if db:
        inserted = await _perform_bulk_insert(db)
    else:
        from src.app.core.db.database import get_db_context

        async with get_db_context() as session:
            inserted = await _perform_bulk_insert(session)

    if not inserted:
        # Re-push failed entries back to Redis to prevent data loss
        try:
            async with client.pipeline() as repush_pipe:
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

    # Log if we hit the flush limit (compare against flush_limit, not max_flush_per_run;
    # log_entries.len can never exceed flush_limit = min(BATCH_SIZE, max_flush_per_run))
    # BUG FIX: Previously compared against max_flush_per_run, so the warning would never
    # fire when AUDIT_LOG_BATCH_SIZE < max_flush_per_run.
    if len(log_dicts) >= flush_limit:
        remaining: int = await client.llen(AUDIT_LOG_BUFFER_KEY)  # type: ignore[misc]
        logger.warning(
            "audit_log_flush_limit_reached",
            flushed=len(log_dicts),
            limit=max_flush_per_run,
            remaining=remaining,
        )

    return len(log_dicts)


# Event handler for audit logs
async def handle_audit_log_event(data: dict[str, Any]) -> None:
    """Handle audit log events from the event bus.

    Buffers logs in Redis for batch insert instead of immediate DB write.
    This reduces DB IOPS by ~99% at scale (1000 events = 10 batches vs 1000 writes).

    Args:
        data: Event payload containing audit log details
    """
    try:
        validated = AuditLogEventData.model_validate(data)
        log_data = {
            "tenant_id": str(validated.tenant_id),
            "action": validated.action,
            "entity_type": validated.entity_type,
            "entity_id": validated.entity_id,
            "user_id": validated.user_id,
            "details": validated.details,
            "ip_address": validated.ip_address,
            "user_agent": validated.user_agent,
            "created_at": datetime.now(UTC).isoformat(),
        }
    except Exception as parse_err:
        logger.warning("audit_log_schema_validation_failed", error=str(parse_err), raw_data=str(data)[:200])
        tenant_val = data.get("tenant_id")
        try:
            tenant_uuid = str(UUID(str(tenant_val))) if tenant_val and str(tenant_val) != "0" else str(SYSTEM_TENANT_ID)
        except (ValueError, TypeError):
            tenant_uuid = str(SYSTEM_TENANT_ID)

        entity_type = data.get("entity_type") or data.get("resource_type")
        entity_id_val = data.get("entity_id") if data.get("entity_id") is not None else data.get("resource_id")

        log_data = {
            "tenant_id": tenant_uuid,
            "action": data.get("action", "unknown"),
            "entity_type": entity_type,
            "entity_id": str(entity_id_val) if entity_id_val is not None else None,
            "user_id": str(data["user_id"]) if data.get("user_id") is not None else None,
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
        # Fallback inline mechanism to ensure log survives Redis failures
        from src.app.core.db.database import get_db_context

        try:
            async with get_db_context() as db:
                await _direct_insert_log(log_data, db)
        except Exception as fallback_error:
            logger.critical("audit_log_event_fallback_failed", error=str(fallback_error))

