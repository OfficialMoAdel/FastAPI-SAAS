"""Audit logging service for tracking system actions."""

import csv
import io
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import UUID

import orjson
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.db.database import get_db_context
from src.app.core.db.scoped import criterion
from src.app.core.json_utils import json_dumps
from src.app.core.utils import cache as redis_cache
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.audit_logs.schemas import AuditExportCriteria
from src.app.modules.audit_logs.trail import AuditGuarantee, audit, system_tenant_id
from src.app.shared.exceptions import ValidationError

if TYPE_CHECKING:
    pass


# A13: export ceiling lives with the service that enforces it (was in router).
EXPORT_MAX_RECORDS = 100_000
_EXPORT_COLUMNS = (
    "id", "action", "user_id", "entity_type", "entity_id", "details", "ip_address", "user_agent", "created_at",
)


def _export_record(log: AuditLog) -> dict[str, Any]:
    return {
        "id": str(log.id),
        "action": log.action,
        "user_id": str(log.user_id) if log.user_id is not None else None,
        "entity_type": log.entity_type,
        "entity_id": str(log.entity_id) if log.entity_id else None,
        "details": log.details,
        "ip_address": log.ip_address,
        "user_agent": log.user_agent,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def _csv_cell(value: str | None) -> str:
    if not value:
        return ""
    # Inspect significant text without removing whitespace from the exported cell.
    trigger = value.lstrip()[:1]
    unsafe = value[0] in ("\t", "\r") or trigger in ("=", "+", "-", "@", "|", "%")
    return f"'{value}" if unsafe else value


class _AuditFilter(NamedTuple):
    action: str | None
    user_id: int | None
    entity_type: str | None
    start_date: datetime | None
    end_date: datetime | None


def _compose_filters(stmt, tenant_id, f: _AuditFilter):
    """A13: single source of truth for audit-log list/export filters."""
    stmt = stmt.where(criterion(AuditLog, tenant_id))
    if f.action is not None:
        stmt = stmt.where(AuditLog.action == f.action)
    if f.user_id is not None:
        stmt = stmt.where(AuditLog.user_id == f.user_id)
    if f.entity_type is not None:
        stmt = stmt.where(AuditLog.entity_type == f.entity_type)
    if f.start_date:
        stmt = stmt.where(AuditLog.created_at >= f.start_date)
    if f.end_date:
        stmt = stmt.where(AuditLog.created_at <= f.end_date)
    return stmt


logger = get_logger(__name__)

# Audit log batching configuration
AUDIT_LOG_BUFFER_KEY = "audit_log_buffer"
AUDIT_LOG_DEAD_LETTER_KEY = "audit_log_dead_letter"
AUDIT_LOG_BATCH_SIZE = 100
AUDIT_LOG_MAX_FLUSH_PER_RUN = 1000
# P2: bound the Redis buffer so a stalled flusher cannot grow it unbounded.
# LTRIM keeps the newest MAXLEN entries (tail, where buffer_audit_log appends);
# the flusher's head-side window (:414) is the opposite end, so both coexist.
AUDIT_LOG_BUFFER_MAXLEN = 10000


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
            select(AuditLog.action).where(criterion(AuditLog, tenant_id)).distinct()
        )
        return sorted(result.scalars().all())

    async def get_log(self, log_id: UUID, tenant_id: UUID) -> AuditLog | None:
        """Get a single audit log entry by ID scoped to tenant."""
        stmt = select(AuditLog).where(
            AuditLog.id == log_id,
            criterion(AuditLog, tenant_id),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
    async def export_count(self, tenant_id, f: _AuditFilter) -> int:
        """A13: count of rows matching the export filter."""
        stmt = _compose_filters(select(func.count()).select_from(AuditLog), tenant_id, f)
        result = await self.db.execute(stmt)
        count: int = result.scalar_one()
        return int(count)

    async def stream_export(self, criteria: AuditExportCriteria, actor: int) -> AsyncGenerator[bytes, None]:
        """Validate and record an export before returning its lazy byte stream.

        The caller must keep the session open while consuming the stream and
        close the generator if consumption stops early. The export event records
        an accepted request, not successful download completion.

        The tenant is resolved ONCE here (request context wins; a mismatching
        criteria.tenant_id raises) and that single value scopes the count, the
        export audit event, and the streamed rows — never three independent
        resolutions. ``record_count`` is the preflight count, not a snapshot
        guarantee: concurrent writes can change the streamed rows.

        Rows that fail mid-stream serialization yield a format-appropriate
        ``_export_error`` sentinel row before the original error propagates, so
        a truncated export is always self-describing and never a silent
        success. Exports are best-effort snapshots, not transactional dumps.

        P2: a long export pins one DB connection/cursor for the whole download;
        run exports with a statement timeout and avoid concurrent exports per
        worker. The yield_per=1000 server-side cursor is intentional — it keeps
        memory bounded while preserving a consistent read.
        """
        from src.app.core.tenancy.context import get_current_tenant

        # P1: single effective tenant — criterion() lets an active context
        # silently win over an explicit argument, so resolve explicitly and
        # fail loudly on mismatch instead of scoping count/event/stream apart.
        effective_tenant = criteria.tenant_id
        ctx_tenant = get_current_tenant()
        ctx_id = ctx_tenant.tenant_id if ctx_tenant and ctx_tenant.tenant_id else None
        if ctx_id is not None:
            if ctx_id != criteria.tenant_id:
                raise ValidationError(
                    f"Export tenant mismatch: criteria {criteria.tenant_id} != context {ctx_id}",
                )
            effective_tenant = ctx_id
        filters = _AuditFilter(
            action=criteria.action,
            user_id=criteria.user_id,
            entity_type=criteria.entity_type,
            start_date=criteria.start_date,
            end_date=criteria.end_date,
        )
        count = await self.export_count(effective_tenant, filters)
        if count > EXPORT_MAX_RECORDS:
            raise ValidationError(
                f"Export exceeds maximum of {EXPORT_MAX_RECORDS} records. "
                f"Found {count} records. Use date filtering to reduce the export size.",
            )

        export_log = await self.create_log(
            tenant_id=effective_tenant,
            user_id=actor,
            action="audit_logs.export",
            entity_type="audit_log",
            entity_id=None,
            details={"format": criteria.format, "record_count": count},
        )
        stmt = (
            _compose_filters(select(AuditLog), effective_tenant, filters)
            .where(AuditLog.id != export_log.id)
            .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
            .limit(EXPORT_MAX_RECORDS)
            .execution_options(yield_per=1000)
        )
        export_format = criteria.format

        async def generate() -> AsyncGenerator[bytes, None]:
            result = await self.db.stream(stmt)
            try:
                output = io.StringIO()
                writer = csv.writer(output)
                if export_format == "csv":
                    writer.writerow(_EXPORT_COLUMNS)
                    yield output.getvalue().encode("utf-8")
                    output.seek(0)
                    output.truncate(0)
                elif export_format == "json":
                    yield b"[\n"
                first = True
                async for log in result.scalars():
                    try:
                        record = _export_record(log)
                        if export_format == "csv":
                            record["details"] = json_dumps(log.details, as_str=True) if log.details else ""
                            writer.writerow([_csv_cell(record[column]) for column in _EXPORT_COLUMNS])
                            yield output.getvalue().encode("utf-8")
                            output.seek(0)
                            output.truncate(0)
                        elif export_format == "jsonl":
                            yield json_dumps(record) + b"\n"
                        else:
                            yield (b"" if first else b",\n") + json_dumps(record)
                    except Exception as exc:
                        # P2: a mid-stream serialization failure must never read
                        # as a clean export — emit a sentinel row describing the
                        # skipped record, then propagate so the truncation is
                        # loud (the finally below still closes the cursor).
                        logger.warning(
                            "audit_export_row_failed",
                            error_type=type(exc).__name__,
                            log_action=getattr(log, "action", None),
                        )
                        marker = {"_export_error": True}
                        if export_format == "csv":
                            output.seek(0)
                            output.truncate(0)
                            writer.writerow(["", "_export_error", "", "", "", '{"_export_error":true}', "", "", ""])
                            yield output.getvalue().encode("utf-8")
                        elif export_format == "jsonl":
                            yield json_dumps(marker) + b"\n"
                        else:
                            yield (b"" if first else b",\n") + json_dumps(marker)
                        raise
                    first = False
                if export_format == "json":
                    yield b"\n]"
            finally:
                await result.close()

        return generate()

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
        query = select(AuditLog).where(criterion(AuditLog, tenant_id))

        if user_id is not None:
            query = query.where(AuditLog.user_id == user_id)
        if action is not None:
            query = query.where(AuditLog.action == action)
        if entity_type is not None:
            query = query.where(AuditLog.entity_type == entity_type)
        if entity_id is not None:
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
        query = select(AuditLog, total_col).where(criterion(AuditLog, tenant_id))

        if user_id is not None:
            query = query.where(AuditLog.user_id == user_id)
        if action is not None:
            query = query.where(AuditLog.action == action)
        if entity_type is not None:
            query = query.where(AuditLog.entity_type == entity_type)
        if entity_id is not None:
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
        count_query = select(func.count()).select_from(AuditLog).where(criterion(AuditLog, tenant_id))
        if user_id is not None:
            count_query = count_query.where(AuditLog.user_id == user_id)
        if action is not None:
            count_query = count_query.where(AuditLog.action == action)
        if entity_type is not None:
            count_query = count_query.where(AuditLog.entity_type == entity_type)
        if entity_id is not None:
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


async def buffer_audit_log(log_data: dict[str, Any]) -> None:
    """Await buffer acceptance, falling back once to an independent DB commit.

    Only canonical, validated audit payloads should reach this writer. Neither
    this function nor its fallback uses or commits a caller's business session.
    """
    client = redis_cache.client
    if client is None:
        logger.warning("audit_log_redis_unavailable", action="direct_insert")
        await _direct_insert_log(log_data)
        return

    try:
        pipe = client.pipeline()
        pipe.rpush(AUDIT_LOG_BUFFER_KEY, orjson.dumps(log_data))
        pipe.ltrim(AUDIT_LOG_BUFFER_KEY, -AUDIT_LOG_BUFFER_MAXLEN, -1)
        await pipe.execute()  # type: ignore[misc]
    except RedisError as exc:
        logger.warning("audit_log_buffer_failed", error_type=type(exc).__name__, action="fallback_direct")
        await _direct_insert_log(log_data)


async def _direct_insert_log(log_data: dict[str, Any]) -> None:
    """Commit a validated audit payload in an owned session; propagate failures."""
    from src.app.core.db.database import local_session

    # Use the session context directly: get_db_context logs exception tracebacks
    # that can include SQL parameters. Audit failures are logged safely at audit().
    async with local_session() as db:
        try:
            user_id = int(log_data["user_id"]) if log_data["user_id"] is not None else None
        except (ValueError, TypeError):
            user_id = None
        log_entry = AuditLog(
            tenant_id=UUID(log_data["tenant_id"]),
            user_id=user_id,
            action=log_data["action"],
            entity_type=log_data["entity_type"],
            entity_id=log_data["entity_id"],
            details=log_data["details"],
            ip_address=log_data["ip_address"],
            user_agent=log_data["user_agent"],
        )
        log_entry.created_at = datetime.fromisoformat(log_data["created_at"])
        db.add(log_entry)
        await db.commit()


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
    if flush_limit <= 0:
        return 0

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

    # Keep raw payloads until they are inserted or accepted by quarantine.
    log_dicts = []
    valid_payloads = []
    rejected_payloads = []
    for raw_data in logs_data:
        try:
            data = orjson.loads(raw_data)
            if not isinstance(data, dict):
                raise ValueError("Audit payload must be an object")
            tenant_id_val = (
                UUID(str(data["tenant_id"]))
                if data.get("tenant_id") is not None
                else system_tenant_id(reason="audit.flush_missing_tenant_id")
            )

            user_id_val = None
            if data.get("user_id") is not None:
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
            valid_payloads.append(raw_data)
        except (ValueError, TypeError, KeyError) as exc:
            rejected_payloads.append(raw_data)
            logger.error("audit_log_parse_failed", error_type=type(exc).__name__)

    if rejected_payloads:
        try:
            await client.rpush(AUDIT_LOG_DEAD_LETTER_KEY, *rejected_payloads)  # type: ignore[misc]
        except RedisError:
            # Quarantine did not accept the records: restore the entire window.
            await client.lpush(AUDIT_LOG_BUFFER_KEY, *reversed(logs_data))  # type: ignore[misc]
            raise
        logger.error("audit_logs_quarantined", count=len(rejected_payloads), key=AUDIT_LOG_DEAD_LETTER_KEY)

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
                # LPUSH reverses arguments; reverse the batch to restore FIFO.
                # Never trim here: every entry has already been accepted.
                repush_pipe.lpush(AUDIT_LOG_BUFFER_KEY, *reversed(valid_payloads))
                await repush_pipe.execute()
            logger.warning(
                "audit_logs_repushed_after_failure",
                count=len(valid_payloads),
            )
        except Exception as repush_err:
            logger.critical(
                "audit_log_repush_failed_data_loss",
                error=str(repush_err),
                lost_count=len(valid_payloads),
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
    """Adapt legacy bus payloads through canonical validation and delivery.

    The adapter is best-effort; bus publication itself is not a REQUIRED audit.
    """
    await audit(
        action=data.get("action") or "unknown",
        tenant_id=data.get("tenant_id"),
        user_id=data.get("user_id"),
        entity_type=data.get("entity_type"),
        entity_id=data.get("entity_id"),
        resource_type=data.get("resource_type"),
        resource_id=data.get("resource_id"),
        details=data.get("details"),
        ip_address=data.get("ip_address"),
        user_agent=data.get("user_agent"),
        guarantee=AuditGuarantee.BEST_EFFORT,
    )


async def handle_login_issued_event(data: dict[str, Any]) -> None:
    """Adapt successful login facts without changing their tenant attribution."""
    channel = data.get("channel")
    user_id = data.get("user_id")
    if not channel or user_id is None:
        logger.warning("login_issued_event_invalid_payload", has_channel=bool(channel), has_user_id=user_id is not None)
        return

    details: dict[str, Any] = {
        "username": data.get("username"),
        "email": data.get("email"),
        "channel": channel,
        "success": True,
    }
    if data.get("provider"):
        details["provider"] = data["provider"]

    await audit(
        action="auth.sso_login" if channel == "sso" else "auth.login",
        tenant_id=data.get("tenant_id"),
        user_id=user_id,
        entity_type="user",
        entity_id=user_id,
        details=details,
        ip_address=data.get("ip_address"),
        user_agent=data.get("user_agent"),
        guarantee=AuditGuarantee.BEST_EFFORT,
    )
