"""Unit tests for AuditLogService business logic.

Tests cover:
- Audit log creation with all fields
- Query filtering by tenant, user, action, entity
- Date range filtering
- Pagination
"""

import asyncio
import csv
import io
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.audit_logs.schemas import AuditExportCriteria
from src.app.modules.audit_logs.service import AuditLogService
from src.app.shared.exceptions import ValidationError


@pytest.fixture
def mock_db():
    """Mock database session."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def audit_service(mock_db):
    """AuditLogService instance with mock database."""
    return AuditLogService(mock_db)


@pytest.fixture
def sample_tenant():
    """Sample tenant ID for testing."""
    return uuid4()  # FIX: Changed from int to UUID


@pytest.fixture
def sample_user():
    """Sample user ID for testing."""
    return 1


class TestAuditLogServiceCreation:
    """Tests for audit log creation."""

    @pytest.mark.asyncio
    async def test_create_audit_log_minimal(self, audit_service, sample_tenant) -> None:
        """Test creating audit log with minimal fields."""
        await audit_service.create_log(
            tenant_id=sample_tenant,
            action="user.login",
        )

        audit_service.db.add.assert_called_once()
        audit_service.db.commit.assert_called_once()
        # Note: refresh() was intentionally removed from service - no server-generated fields needed

    @pytest.mark.asyncio
    async def test_create_audit_log_full_fields(self, audit_service, sample_tenant, sample_user) -> None:
        """Test creating audit log with all fields."""
        entity_id = uuid4()
        details = {"ip": "192.168.1.1", "user_agent": "Mozilla/5.0"}

        await audit_service.create_log(
            tenant_id=sample_tenant,
            action="post.created",
            entity_type="post",
            entity_id=entity_id,
            user_id=sample_user,
            details=details,
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
        )

        # Verify db operations were called
        audit_service.db.add.assert_called_once()
        audit_service.db.commit.assert_called_once()

        # Verify the log entry was created with correct fields
        added_log = audit_service.db.add.call_args[0][0]
        assert added_log.tenant_id == sample_tenant
        assert added_log.action == "post.created"
        assert added_log.entity_type == "post"
        assert added_log.entity_id == entity_id
        assert added_log.user_id == sample_user
        assert added_log.details == details
        assert added_log.ip_address == "192.168.1.1"
        assert added_log.user_agent == "Mozilla/5.0"


class TestAuditLogServiceQueries:
    """Tests for audit log querying."""

    @pytest.mark.asyncio
    async def test_get_logs_by_tenant(self, audit_service, sample_tenant) -> None:
        """Test filtering logs by tenant ID."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        audit_service.db.execute = AsyncMock(return_value=mock_result)

        await audit_service.query_logs(tenant_id=sample_tenant)  # FIX: Changed from get_logs to query_logs

        audit_service.db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_logs_by_user(self, audit_service, sample_tenant, sample_user) -> None:
        """Test filtering logs by user ID."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        audit_service.db.execute = AsyncMock(return_value=mock_result)

        await audit_service.query_logs(  # FIX: Changed from get_logs to query_logs
            tenant_id=sample_tenant,
            user_id=sample_user,
        )

        audit_service.db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_logs_by_action(self, audit_service, sample_tenant) -> None:
        """Test filtering logs by action type."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        audit_service.db.execute = AsyncMock(return_value=mock_result)

        await audit_service.query_logs(  # FIX: Changed from get_logs to query_logs
            tenant_id=sample_tenant,
            action="user.login",
        )

        audit_service.db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_logs_by_entity_type(self, audit_service, sample_tenant) -> None:
        """Test filtering logs by entity type."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        audit_service.db.execute = AsyncMock(return_value=mock_result)

        await audit_service.query_logs(  # FIX: Changed from get_logs to query_logs
            tenant_id=sample_tenant,
            entity_type="post",
        )

        audit_service.db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_logs_with_date_range(self, audit_service, sample_tenant) -> None:
        """Test filtering logs by date range."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        audit_service.db.execute = AsyncMock(return_value=mock_result)

        start_date = datetime(2024, 1, 1, tzinfo=UTC)
        end_date = datetime(2024, 12, 31, tzinfo=UTC)

        await audit_service.query_logs(  # FIX: Changed from get_logs to query_logs
            tenant_id=sample_tenant,
            start_date=start_date,
            end_date=end_date,
        )

        audit_service.db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_logs_pagination(self, audit_service, sample_tenant) -> None:
        """Test pagination in log queries."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        audit_service.db.execute = AsyncMock(return_value=mock_result)

        await audit_service.query_logs(  # FIX: Changed from get_logs to query_logs
            tenant_id=sample_tenant,
            limit=50,
            offset=100,
        )

        audit_service.db.execute.assert_called_once()


EXPORT_COLUMNS = [
    "id", "action", "user_id", "entity_type", "entity_id", "details", "ip_address", "user_agent", "created_at",
]


@pytest.fixture
def export_rows() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=UUID(int=1), action="user.created", user_id=42, entity_type="user", entity_id="abc",
            details={"message": 'مرحبا, 世界 "quoted"\nnext', "nested": {"ok": True}},
            ip_address="127.0.0.1", user_agent='Browser, "世界"\nnext',
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=UUID(int=2), action="user.deleted", user_id=None, entity_type=None, entity_id=None,
            details=None, ip_address=None, user_agent=None, created_at=None,
        ),
        SimpleNamespace(
            id=UUID(int=3), action="user.updated", user_id=0, entity_type="", entity_id="",
            details={}, ip_address="", user_agent="", created_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    ]


def mock_export_result(mock_db: AsyncMock, rows: list[SimpleNamespace]) -> MagicMock:
    async def scalars() -> AsyncGenerator[SimpleNamespace, None]:
        for row in rows:
            yield row

    result = MagicMock()
    result.scalars.side_effect = scalars
    result.close = AsyncMock()
    mock_db.stream.return_value = result
    return result


@pytest.fixture
def export_service(audit_service: AuditLogService) -> AuditLogService:
    audit_service.export_count = AsyncMock(return_value=0)
    audit_service.create_log = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    return audit_service


async def export_bytes(chunks: AsyncGenerator[bytes, None]) -> bytes:
    try:
        parts = [chunk async for chunk in chunks]
        assert all(isinstance(chunk, bytes) for chunk in parts)
        return b"".join(parts)
    finally:
        await chunks.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("export_format", ["csv", "json", "jsonl"])
@pytest.mark.parametrize("empty", [True, False])
async def test_stream_export_formats(
    export_service, mock_db, sample_tenant, export_rows, export_format, empty
) -> None:
    rows = [] if empty else export_rows
    result = mock_export_result(mock_db, rows)
    export_service.export_count.return_value = len(rows)
    chunks = await export_service.stream_export(
        AuditExportCriteria(tenant_id=sample_tenant, format=export_format), actor=7,
    )
    mock_db.stream.assert_not_awaited()
    body = await export_bytes(chunks)
    result.close.assert_awaited_once_with()
    mock_db.stream.assert_awaited_once()

    if export_format == "csv":
        reader = csv.DictReader(io.StringIO(body.decode("utf-8"), newline=""))
        records = list(reader)
        assert reader.fieldnames == EXPORT_COLUMNS
        if not empty:
            assert records == [
                {
                    "id": str(UUID(int=1)), "action": "user.created", "user_id": "42",
                    "entity_type": "user", "entity_id": "abc", "details": records[0]["details"],
                    "ip_address": "127.0.0.1", "user_agent": 'Browser, "世界"\nnext',
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
                {**dict.fromkeys(EXPORT_COLUMNS, ""), "id": str(UUID(int=2)), "action": "user.deleted"},
                {
                    **dict.fromkeys(EXPORT_COLUMNS, ""), "id": str(UUID(int=3)), "action": "user.updated",
                    "user_id": "0", "created_at": "2026-01-02T00:00:00+00:00",
                },
            ]
            assert json.loads(records[0]["details"]) == export_rows[0].details
    else:
        records = json.loads(body) if export_format == "json" else [json.loads(line) for line in body.splitlines()]
        if export_format == "jsonl":
            assert body == b"" if empty else body.endswith(b"\n")
            assert len(body.splitlines()) == len(rows)
        if not empty:
            assert records == [
                {
                    "id": str(UUID(int=1)), "action": "user.created", "user_id": "42",
                    "entity_type": "user", "entity_id": "abc", "details": export_rows[0].details,
                    "ip_address": "127.0.0.1", "user_agent": 'Browser, "世界"\nnext',
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
                {**dict.fromkeys(EXPORT_COLUMNS), "id": str(UUID(int=2)), "action": "user.deleted"},
                {
                    "id": str(UUID(int=3)), "action": "user.updated", "user_id": "0", "entity_type": "",
                    "entity_id": None, "details": {}, "ip_address": "", "user_agent": "",
                    "created_at": "2026-01-02T00:00:00+00:00",
                },
            ]
    assert len(records) == len(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
async def test_stream_export_csv_sanitizes_text_fields(
    export_service, mock_db, sample_tenant, export_rows, prefix,
) -> None:
    row = export_rows[0]
    # Include every externally supplied text projection, even an unvalidated legacy IP value.
    text_fields = ["action", "entity_type", "entity_id", "ip_address", "user_agent"]
    value = prefix + 'text,"世界"\nnext'
    for field in text_fields:
        setattr(row, field, value)
    # Negative integer IDs are also formula-prefixed after string projection.
    row.user_id = -42
    row.details = {"message": value}
    mock_export_result(mock_db, [row])
    chunks = await export_service.stream_export(AuditExportCriteria(tenant_id=sample_tenant), actor=7)
    records = list(csv.DictReader(io.StringIO((await export_bytes(chunks)).decode(), newline="")))
    assert len(records) == 1
    for field in text_fields:
        assert records[0][field] == "'" + value
    assert records[0]["user_id"] == "'-42"
    assert records[0]["id"] == str(row.id)
    assert records[0]["created_at"] == row.created_at.isoformat()
    assert json.loads(records[0]["details"]) == {"message": value}


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 100_000, 100_001])
async def test_stream_export_preflight_limit_and_actor(export_service, mock_db, sample_tenant, count) -> None:
    export_service.export_count.return_value = count
    criteria = AuditExportCriteria(tenant_id=sample_tenant, format="jsonl", user_id=42)
    if count > 100_000:
        with pytest.raises(ValidationError, match="maximum of 100000 records.*Found 100001"):
            await export_service.stream_export(criteria, actor=7)
        export_service.create_log.assert_not_awaited()
    else:
        result = mock_export_result(mock_db, [])
        chunks = await export_service.stream_export(criteria, actor=7)
        export_service.create_log.assert_awaited_once_with(
            tenant_id=sample_tenant, user_id=7, action="audit_logs.export", entity_type="audit_log",
            entity_id=None, details={"format": "jsonl", "record_count": count},
        )
        mock_db.stream.assert_not_awaited()
        await export_bytes(chunks)
        result.close.assert_awaited_once()
        stmt = mock_db.stream.await_args.args[0]
        assert stmt.get_execution_options()["yield_per"] == 1000
        assert stmt.compile().params["param_1"] == 100_000
        assert "ORDER BY audit_logs.created_at ASC, audit_logs.id ASC" in str(stmt)
    export_service.export_count.assert_awaited_once()
    tenant_id, filters = export_service.export_count.await_args.args
    assert tenant_id == sample_tenant
    assert filters.user_id == 42
    if count > 100_000:
        mock_db.stream.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["count", "audit"])
async def test_stream_export_preflight_failure(export_service, mock_db, sample_tenant, stage) -> None:
    error = RuntimeError(f"{stage} failed")
    target = export_service.export_count if stage == "count" else export_service.create_log
    target.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        await export_service.stream_export(AuditExportCriteria(tenant_id=sample_tenant), actor=7)
    assert caught.value is error
    mock_db.stream.assert_not_awaited()
    if stage == "count":
        export_service.create_log.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("export_format", ["csv", "json", "jsonl"])
@pytest.mark.parametrize("failure", ["iteration", "serialization", "early_close", "close", "cancelled"])
async def test_stream_export_closes_result(
    export_service, mock_db, sample_tenant, export_rows, export_format, failure
) -> None:
    result = mock_export_result(mock_db, export_rows)
    error = RuntimeError("stream failed")
    if failure == "iteration":
        async def failing_rows() -> AsyncGenerator[SimpleNamespace, None]:
            yield export_rows[0]
            raise error
        result.scalars.side_effect = failing_rows
    elif failure == "serialization":
        export_rows[0].details = {"unsupported": object()}
    elif failure == "close":
        result.close.side_effect = error
    chunks = await export_service.stream_export(
        AuditExportCriteria(tenant_id=sample_tenant, format=export_format), actor=7,
    )
    if failure == "cancelled":
        await anext(chunks)
        with pytest.raises(asyncio.CancelledError):
            await chunks.athrow(asyncio.CancelledError())
    elif failure == "early_close":
        await anext(chunks)
        result.close.assert_not_awaited()
        await chunks.aclose()
        await chunks.aclose()
    else:
        with pytest.raises(TypeError if failure == "serialization" else RuntimeError) as caught:
            await export_bytes(chunks)
        if failure != "serialization":
            assert caught.value is error
    result.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_stream_export_open_failure_is_lazy(export_service, mock_db, sample_tenant) -> None:
    mock_db.stream.side_effect = RuntimeError("open failed")
    chunks = await export_service.stream_export(AuditExportCriteria(tenant_id=sample_tenant), actor=7)
    mock_db.stream.assert_not_awaited()
    with pytest.raises(RuntimeError, match="open failed"):
        await export_bytes(chunks)


@pytest.mark.asyncio
async def test_stream_export_unstarted_generator_opens_no_cursor(export_service, mock_db, sample_tenant) -> None:
    chunks = await export_service.stream_export(AuditExportCriteria(tenant_id=sample_tenant), actor=7)
    await chunks.aclose()
    mock_db.stream.assert_not_awaited()
    export_service.create_log.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_name", [None, "action", "user_id", "entity_type", "start_date", "end_date", "all"])
async def test_stream_export_sqlite_filters_order_and_own_event(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, filter_name: str | None,
) -> None:
    from src.app.core.tenancy.context import get_current_tenant, set_current_tenant

    tenant_id, other_tenant = uuid4(), uuid4()
    start, end = datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC)
    filters = {
        "action": "audit_logs.export", "user_id": 42, "entity_type": "audit_log",
        "start_date": start, "end_date": end,
    }
    criteria = AuditExportCriteria(
        tenant_id=tenant_id, format="json",
        **(filters if filter_name == "all" else {filter_name: filters[filter_name]} if filter_name else {}),
    )
    # Each decoy violates exactly one predicate; inclusive date boundaries and tied IDs are explicit.
    values = [
        (8, tenant_id, "audit_logs.export", 42, "audit_log", end),
        (3, tenant_id, "audit_logs.export", 42, "audit_log", start),
        (1, tenant_id, "audit_logs.export", 42, "audit_log", start),
        (4, tenant_id, "other.action", 42, "audit_log", start),
        (5, tenant_id, "audit_logs.export", 7, "audit_log", start),
        (6, tenant_id, "audit_logs.export", 42, "other", start),
        (7, tenant_id, "audit_logs.export", 42, "audit_log", datetime(2026, 1, 1, tzinfo=UTC)),
        (9, tenant_id, "audit_logs.export", 42, "audit_log", datetime(2026, 1, 4, tzinfo=UTC)),
        (2, other_tenant, "audit_logs.export", 42, "audit_log", start),
    ]
    expected_ids = {
        None: [7, 1, 3, 4, 5, 6, 8, 9], "action": [7, 1, 3, 5, 6, 8, 9],
        "user_id": [7, 1, 3, 4, 6, 8, 9], "entity_type": [7, 1, 3, 4, 5, 8, 9],
        "start_date": [1, 3, 4, 5, 6, 8, 9], "end_date": [7, 1, 3, 4, 5, 6, 8], "all": [1, 3, 8],
    }[filter_name]
    previous = get_current_tenant()
    set_current_tenant(None)
    # Keep real SQL/ORM and create_log, but flush instead of committing test rows.
    monkeypatch.setattr(db_session, "commit", AsyncMock(side_effect=db_session.flush))
    try:
        for id_value, tenant, action, user_id, entity_type, created_at in values:
            row = AuditLog(
                tenant_id=tenant, action=action, user_id=user_id, entity_type=entity_type,
                entity_id=None, ip_address=None, user_agent=None,
            )
            row.id, row.created_at = UUID(int=id_value), created_at
            db_session.add(row)
        await db_session.flush()
        service = AuditLogService(db_session)
        chunks = await service.stream_export(criteria, actor=7)
        # Accepted export is written during preflight, before opening its result stream.
        events = (await db_session.execute(select(AuditLog).where(
            AuditLog.tenant_id == tenant_id, AuditLog.id.not_in([UUID(int=v[0]) for v in values]),
        ))).scalars().all()
        assert len(events) == 1
        event = events[0]
        assert event.user_id == 7
        assert event.action == "audit_logs.export"
        assert event.entity_type == "audit_log"
        assert event.details == {"format": "json", "record_count": len(expected_ids)}
        records = json.loads(await export_bytes(chunks))
        assert [record["id"] for record in records] == [str(UUID(int=i)) for i in expected_ids]
        assert str(event.id) not in [record["id"] for record in records]
        assert await db_session.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.tenant_id == tenant_id,
        )) == 9
    finally:
        await db_session.rollback()
        set_current_tenant(previous)

