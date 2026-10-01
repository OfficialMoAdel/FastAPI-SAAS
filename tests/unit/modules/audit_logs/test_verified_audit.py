"""Verified audit fixes: F14 loud validation, F15 DLQ bound, F16 export-id capture."""

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.audit_logs.schemas import AuditExportCriteria
from src.app.modules.audit_logs.service import (
    AUDIT_LOG_BUFFER_MAXLEN,
    AUDIT_LOG_DEAD_LETTER_KEY,
    AuditLogService,
    _direct_insert_log,
    flush_audit_log_buffer,
)


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


class TestF14LoudValidation:
    @pytest.mark.asyncio
    async def test_missing_tenant_id_raises_value_error_not_key_error(self) -> None:
        """F14: a malformed payload fails as a loud ValueError, never KeyError."""
        with patch("src.app.core.db.database.local_session") as session_factory:
            # AsyncSession-spec'd: the fallback opens its session inside
            # bypass_rls(...), which awaits session.connection().
            session = MagicMock(spec=AsyncSession)
            session.connection = AsyncMock()
            session_factory.return_value.__aenter__.return_value = session
            with pytest.raises(ValueError, match="tenant_id"):
                await _direct_insert_log({"action": "user.created"})


class TestF15DlqBound:
    @pytest.mark.asyncio
    async def test_quarantine_trims_dlq(self) -> None:
        """F15: rejected payloads are quarantined AND the DLQ list is bounded."""
        client = MagicMock()
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=[[b"not-json{"]])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=pipe)
        ctx.__aexit__ = AsyncMock(return_value=False)
        client.pipeline = MagicMock(return_value=ctx)
        client.rpush = AsyncMock()
        client.ltrim = AsyncMock()

        with patch("src.app.modules.audit_logs.service.redis_cache") as cache:
            cache.client = client
            flushed = await flush_audit_log_buffer()

        assert flushed == 0
        client.rpush.assert_awaited_once()
        assert client.rpush.await_args.args[0] == AUDIT_LOG_DEAD_LETTER_KEY
        client.ltrim.assert_awaited_once_with(
            AUDIT_LOG_DEAD_LETTER_KEY, -AUDIT_LOG_BUFFER_MAXLEN, -1
        )


class TestF16ExportIdCapture:
    @pytest.mark.asyncio
    async def test_exclusion_uses_captured_id_value(self, mock_db: AsyncMock) -> None:
        """F16: the WHERE clause binds the export id as a plain value read while
        the session was open — compile the captured statement to prove it."""
        export_id = uuid4()
        svc = AuditLogService(mock_db)
        svc.export_count = AsyncMock(return_value=0)
        svc.create_log = AsyncMock(return_value=SimpleNamespace(id=export_id))

        async def scalars() -> AsyncGenerator[SimpleNamespace]:
            return
            yield  # pragma: no cover - empty stream

        result = MagicMock()
        result.scalars.side_effect = scalars
        result.close = AsyncMock()
        mock_db.stream.return_value = result

        chunks = await svc.stream_export(
            AuditExportCriteria(tenant_id=uuid4(), format="jsonl"), actor=7
        )
        try:
            [chunk async for chunk in chunks]
        finally:
            await chunks.aclose()

        stmt = mock_db.stream.await_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert str(export_id) in compiled
