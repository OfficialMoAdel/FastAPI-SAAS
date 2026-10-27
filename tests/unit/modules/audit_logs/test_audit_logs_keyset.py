"""Keyset pagination for audit logs (Phase 3.2).

The offset endpoint (``query_logs_with_total``) stays for page-jump UIs; this
is its deep-traversal sibling. What these tests pin:

* the fixed ``created_at DESC, id DESC`` order and the STRICT seek clause
  (``created_at < c OR (created_at = c AND id < i)``) — a ``<=`` would repeat
  the cursor row on every page;
* that a UUID id column is bound with a ``uuid.UUID`` (the cursor carries a
  string, and the column refuses it);
* the envelope: no ``total``, no page number, ``next_cursor`` only on a full
  page;
* that filters and the tenant predicate compose with the seek clause.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from src.app.core.pagination import decode_keyset_cursor, encode_keyset_cursor
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.audit_logs.service import AuditLogService

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _log(created_at: datetime, row_id: UUID | None = None) -> MagicMock:
    row = MagicMock(spec=AuditLog)
    row.id = row_id or uuid4()
    row.created_at = created_at
    row.tenant_id = TENANT_ID
    row.action = "user.login"
    return row


def _result(rows: list) -> MagicMock:
    result = MagicMock()
    result.unique.return_value.scalars.return_value.all.return_value = rows
    return result


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


class TestKeysetStatementContract:
    @pytest.mark.asyncio
    async def test_the_first_page_is_descending_and_limited(self) -> None:
        service = AuditLogService(AsyncMock())
        with patch(
            "src.app.modules.audit_logs.service.execute_scoped",
            new=AsyncMock(return_value=_result([])),
        ) as execute:
            await service.query_logs_keyset(TENANT_ID, limit=5)

        stmt = execute.await_args.args[0]
        sql = _compiled(stmt)
        assert "ORDER BY audit_logs.created_at DESC, audit_logs.id DESC" in sql
        assert "LIMIT 5" in sql
        assert "audit_logs.created_at <" not in sql, "page one must not seek"

    @pytest.mark.asyncio
    async def test_the_seek_clause_is_strict_on_created_at_and_id(self) -> None:
        service = AuditLogService(AsyncMock())
        cursor_id = uuid4()
        cursor = encode_keyset_cursor(T0, cursor_id)

        with patch(
            "src.app.modules.audit_logs.service.execute_scoped",
            new=AsyncMock(return_value=_result([])),
        ) as execute:
            await service.query_logs_keyset(TENANT_ID, cursor=cursor, limit=5)

        sql = _compiled(execute.await_args.args[0])
        # Strict on BOTH halves: <= would re-serve the cursor row forever.
        assert "audit_logs.created_at <" in sql
        assert "audit_logs.created_at =" in sql
        assert "audit_logs.id <" in sql
        # The UUID is bound as a UUID literal, not the raw cursor string.
        assert str(cursor_id) in sql

    @pytest.mark.asyncio
    async def test_the_tenant_predicate_is_applied_by_the_scoped_executor(self) -> None:
        service = AuditLogService(AsyncMock())
        with patch(
            "src.app.modules.audit_logs.service.execute_scoped",
            new=AsyncMock(return_value=_result([])),
        ) as execute:
            await service.query_logs_keyset(TENANT_ID, limit=5)

        scope = execute.await_args.kwargs["scope"]
        assert scope.tenant_id == TENANT_ID
        assert execute.await_args.kwargs["model"] is AuditLog

    @pytest.mark.asyncio
    async def test_filters_compose_with_the_seek_clause(self) -> None:
        service = AuditLogService(AsyncMock())
        with patch(
            "src.app.modules.audit_logs.service.execute_scoped",
            new=AsyncMock(return_value=_result([])),
        ) as execute:
            await service.query_logs_keyset(
                TENANT_ID,
                action="user.login",
                user_id=7,
                entity_type="user",
                start_date=T0,
                end_date=T0,
                cursor=encode_keyset_cursor(T0, uuid4()),
                limit=5,
            )

        sql = _compiled(execute.await_args.args[0])
        assert "audit_logs.action = 'user.login'" in sql
        assert "audit_logs.user_id = 7" in sql
        assert "audit_logs.entity_type = 'user'" in sql
        assert "audit_logs.created_at >=" in sql
        assert "audit_logs.created_at <=" in sql
        assert "audit_logs.id <" in sql, "the seek clause survives the filters"


class TestKeysetEnvelope:
    @pytest.mark.asyncio
    async def test_a_full_page_carries_a_next_cursor(self) -> None:
        rows = [_log(T0), _log(T0.replace(minute=59))]
        service = AuditLogService(AsyncMock())

        with patch(
            "src.app.modules.audit_logs.service.execute_scoped",
            new=AsyncMock(return_value=_result(rows)),
        ):
            page = await service.query_logs_keyset(TENANT_ID, limit=2)

        assert page["items"] == rows
        assert page["has_next"] is True
        assert page["limit"] == 2
        assert page["next_cursor"] is not None
        # The cursor points at the LAST row of the page. The id comes back as a
        # string because the cursor is untyped; the seek clause coerces it to
        # the column's own type.
        decoded_created_at, decoded_id = decode_keyset_cursor(page["next_cursor"])
        assert decoded_created_at == rows[-1].created_at
        assert str(decoded_id) == str(rows[-1].id)

    @pytest.mark.asyncio
    async def test_a_short_page_is_the_end_of_the_listing(self) -> None:
        service = AuditLogService(AsyncMock())

        with patch(
            "src.app.modules.audit_logs.service.execute_scoped",
            new=AsyncMock(return_value=_result([_log(T0)])),
        ):
            page = await service.query_logs_keyset(TENANT_ID, limit=10)

        assert page["has_next"] is False
        assert page["next_cursor"] is None

    @pytest.mark.asyncio
    async def test_an_empty_page_has_no_cursor(self) -> None:
        service = AuditLogService(AsyncMock())

        with patch(
            "src.app.modules.audit_logs.service.execute_scoped",
            new=AsyncMock(return_value=_result([])),
        ):
            page = await service.query_logs_keyset(TENANT_ID, limit=10)

        assert page == {"items": [], "next_cursor": None, "has_next": False, "limit": 10}

    @pytest.mark.asyncio
    async def test_no_total_and_no_page_number(self) -> None:
        service = AuditLogService(AsyncMock())

        with patch(
            "src.app.modules.audit_logs.service.execute_scoped",
            new=AsyncMock(return_value=_result([_log(T0)])),
        ):
            page = await service.query_logs_keyset(TENANT_ID, limit=10)

        assert set(page) == {"items", "next_cursor", "has_next", "limit"}

    @pytest.mark.asyncio
    async def test_a_malformed_cursor_raises_value_error(self) -> None:
        """Core raises ValueError; the router turns it into a 422."""
        service = AuditLogService(AsyncMock())

        with patch("src.app.modules.audit_logs.service.execute_scoped", new=AsyncMock()) as execute:
            with pytest.raises(ValueError):
                await service.query_logs_keyset(TENANT_ID, cursor="!!!not-base64!!!", limit=5)

        execute.assert_not_awaited()  # a bad cursor must not reach the database
