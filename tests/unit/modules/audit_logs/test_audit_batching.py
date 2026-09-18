"""P1-2: Audit Log Batching Tests.

Verifies that audit logs are buffered in Redis and batch-flushed.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import UUID

import pytest
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.audit_logs.service import buffer_audit_log
from src.app.modules.audit_logs.trail import AuditGuarantee, audit


class TestAuditLogBatching:
    """Tests for audit log Redis buffering and batch flush."""

    @pytest.mark.asyncio
    async def test_buffer_uses_rpush(self) -> None:
        """Verify audit log buffering uses Redis RPUSH (bounded via pipeline LTRIM)."""
        from src.app.modules.audit_logs.service import (
            AUDIT_LOG_BUFFER_KEY,
            AUDIT_LOG_BUFFER_MAXLEN,
            buffer_audit_log,
        )

        mock_redis = MagicMock()
        # P2: service pipelines rpush + ltrim in one RTT; the pipe commands
        # are sync queue calls, only execute() is awaited.
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True])
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.app.modules.audit_logs.service.redis_cache") as mock_cache:
            mock_cache.client = mock_redis
            log_data = {
                "tenant_id": "00000000-0000-0000-0000-000000000001",
                "action": "user.created",
                "entity_type": "user",
                "user_id": "456",
            }

            await buffer_audit_log(log_data)

            # Verify RPUSH was called with buffer key
            mock_pipe.rpush.assert_called_once()
            call_args = mock_pipe.rpush.call_args
            assert call_args[0][0] == AUDIT_LOG_BUFFER_KEY
            # P2: same RTT trims the buffer to the newest MAXLEN entries
            mock_pipe.ltrim.assert_called_once_with(AUDIT_LOG_BUFFER_KEY, -AUDIT_LOG_BUFFER_MAXLEN, -1)
            mock_pipe.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fallback_on_redis_error(self) -> None:
        """Verify fallback to direct insert when Redis fails."""
        from redis.exceptions import RedisError

        from src.app.modules.audit_logs.service import buffer_audit_log

        mock_redis = MagicMock()
        # P2: failure surfaces at pipeline execute(); pipe commands themselves queue.
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=RedisError("Connection failed"))
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with (
            patch("src.app.modules.audit_logs.service.redis_cache") as mock_cache,
            patch("src.app.modules.audit_logs.service._direct_insert_log", new_callable=AsyncMock) as mock_direct,
        ):
            mock_cache.client = mock_redis
            log_data: dict[str, Any] = {
                "tenant_id": "00000000-0000-0000-0000-000000000001",
                "action": "user.created",
            }

            await buffer_audit_log(log_data)

            # Verify fallback was called
            mock_direct.assert_called_once_with(log_data)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("redis_available", [False, True])
    @pytest.mark.parametrize("guarantee", list(AuditGuarantee))
    async def test_fallback_commits_owned_session_preserving_tenant_and_time(
        self, redis_available: bool, guarantee: AuditGuarantee
    ) -> None:
        tenant_id = UUID("11111111-1111-1111-1111-111111111111")
        created_at = datetime(2025, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
        db = MagicMock(spec=AsyncSession)
        pipe = MagicMock()
        pipe.execute = AsyncMock(side_effect=RedisError("unavailable"))
        client = MagicMock()
        client.pipeline.return_value = pipe

        with (
            patch("src.app.modules.audit_logs.service.redis_cache") as cache,
            patch("src.app.core.db.database.local_session") as session_factory,
            patch("src.app.modules.audit_logs.trail.datetime") as clock,
        ):
            cache.client = client if redis_available else None
            context = session_factory.return_value
            context.__aenter__.return_value = db
            clock.now.return_value = created_at

            await audit(
                action="user.updated",
                tenant_id=tenant_id,
                user_id=42,
                entity_type="user",
                entity_id=42,
                details={"changed_field": "name"},
                ip_address="203.0.113.10",
                user_agent="TestAgent/1.0",
                guarantee=guarantee,
            )

        clock.now.assert_called_once_with(UTC)
        session_factory.assert_called_once_with()
        context.__aenter__.assert_awaited_once()
        context.__aexit__.assert_awaited_once_with(None, None, None)
        db.add.assert_called_once()
        row = db.add.call_args.args[0]
        assert row.tenant_id == tenant_id
        assert row.created_at == created_at
        assert row.user_id == 42
        assert row.action == "user.updated"
        assert row.entity_type == "user"
        assert row.entity_id == "42"
        assert row.details == {"changed_field": "name"}
        assert row.ip_address == "203.0.113.10"
        assert row.user_agent == "TestAgent/1.0"
        db.commit.assert_awaited_once_with()
        assert db.mock_calls == [call.add(row), call.commit()]
        if redis_available:
            pipe.execute.assert_awaited_once()
        else:
            client.pipeline.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("redis_available", [False, True])
    @pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
    async def test_fallback_commit_failure_propagates_without_retry(
        self, redis_available: bool, failure_type: type[BaseException]
    ) -> None:
        error = failure_type("commit failed")
        db = MagicMock(spec=AsyncSession)
        db.commit.side_effect = error
        client = MagicMock()
        client.pipeline.return_value.execute = AsyncMock(side_effect=RedisError("unavailable"))
        payload = {
            "tenant_id": str(UUID(int=1)),
            "user_id": "42",
            "action": "user.updated",
            "entity_type": "user",
            "entity_id": "42",
            "details": {},
            "ip_address": None,
            "user_agent": None,
            "created_at": "2025-01-02T03:04:05+00:00",
        }

        with (
            patch("src.app.modules.audit_logs.service.redis_cache") as cache,
            patch("src.app.core.db.database.local_session") as session_factory,
        ):
            cache.client = client if redis_available else None
            context = session_factory.return_value
            context.__aenter__.return_value = db
            context.__aexit__.return_value = False

            with pytest.raises(failure_type) as exc_info:
                await buffer_audit_log(payload)

        assert exc_info.value is error
        session_factory.assert_called_once_with()
        db.add.assert_called_once()
        db.commit.assert_awaited_once_with()
        context.__aexit__.assert_awaited_once()
        assert context.__aexit__.await_args.args[1] is error

    @pytest.mark.asyncio
    async def test_cancelled_redis_write_does_not_fall_back(self) -> None:
        error = asyncio.CancelledError()
        client = MagicMock()
        client.pipeline.return_value.execute = AsyncMock(side_effect=error)
        with (
            patch("src.app.modules.audit_logs.service.redis_cache") as cache,
            patch("src.app.modules.audit_logs.service._direct_insert_log", new_callable=AsyncMock) as direct,
        ):
            cache.client = client
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await buffer_audit_log({"action": "user.updated"})

        assert exc_info.value is error
        client.pipeline.return_value.execute.assert_awaited_once()
        direct.assert_not_awaited()

    def test_flush_function_uses_pipeline(self) -> None:
        """Verify flush function uses Redis pipeline pattern."""
        import inspect

        from src.app.modules.audit_logs import service

        source = inspect.getsource(service)

        # Verify pipeline pattern is used
        assert "pipeline()" in source
        assert "lrange" in source
        assert "ltrim" in source

    def test_batch_insert_uses_bulk_insert(self) -> None:
        """Verify batch insert uses execute(insert().values()) for efficiency."""
        import inspect

        from src.app.modules.audit_logs import service

        source = inspect.getsource(service)

        # Verify bulk insert is used instead of add_all
        assert "session.execute(insert(" in source or "await session.execute(insert(" in source

    def test_buffer_key_constant_exists(self) -> None:
        """Verify buffer key constant is defined."""
        from src.app.modules.audit_logs.service import AUDIT_LOG_BUFFER_KEY

        assert AUDIT_LOG_BUFFER_KEY == "audit_log_buffer"

    def test_batch_size_constant_exists(self) -> None:
        """Verify batch size constant is defined."""
        from src.app.modules.audit_logs.service import AUDIT_LOG_BATCH_SIZE

        assert AUDIT_LOG_BATCH_SIZE == 100

    @pytest.mark.asyncio
    async def test_legacy_event_without_action_uses_unknown(self) -> None:
        from src.app.modules.audit_logs.service import handle_audit_log_event

        with patch("src.app.modules.audit_logs.service.audit", new_callable=AsyncMock) as writer:
            await handle_audit_log_event({"tenant_id": str(UUID(int=1))})
        assert writer.await_args.kwargs["action"] == "unknown"
        assert writer.await_args.kwargs["guarantee"] == AuditGuarantee.BEST_EFFORT

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("user_id", "expected"), [("legacy-user", None), ("0", 0), ("42", 42), (None, None)])
    async def test_fallback_coerces_legacy_user_id(self, user_id, expected) -> None:
        db = MagicMock(spec=AsyncSession)
        with (
            patch("src.app.modules.audit_logs.service.redis_cache") as cache,
            patch("src.app.core.db.database.local_session") as sessions,
        ):
            cache.client = None
            sessions.return_value.__aenter__.return_value = db
            await audit("test.action", tenant_id=UUID(int=1), user_id=user_id, guarantee=AuditGuarantee.REQUIRED)
        assert db.add.call_args.args[0].user_id == expected
        assert db.add.call_args.args[0].tenant_id == UUID(int=1)
        db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("db_fails", [False, True])
async def test_flush_quarantines_bad_records_and_restores_fifo(db_fails) -> None:
    import orjson

    from src.app.modules.audit_logs.service import (
        AUDIT_LOG_BUFFER_KEY,
        AUDIT_LOG_DEAD_LETTER_KEY,
        flush_audit_log_buffer,
    )

    good = [orjson.dumps({"tenant_id": str(UUID(int=1)), "action": name, "user_id": 0}) for name in ("first", "second")]
    bad = [b"invalid json", b"[]", orjson.dumps({"tenant_id": "invalid", "action": "bad"})]
    window = [good[0], *bad, good[1]]
    queue = window + [b"newer"]
    dead_letter = []
    client = MagicMock()
    pipe = MagicMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    client.pipeline.return_value = pipe

    async def execute():
        if pipe.lpush.called:
            _, *entries = pipe.lpush.call_args.args
            for entry in entries:
                queue.insert(0, entry)
            return [len(queue)]
        del queue[:len(window)]
        return [window, True]

    async def quarantine(key, *entries):
        assert key == AUDIT_LOG_DEAD_LETTER_KEY
        dead_letter.extend(entries)

    pipe.execute = AsyncMock(side_effect=execute)
    client.rpush = AsyncMock(side_effect=quarantine)
    client.llen = AsyncMock(return_value=1)
    db = MagicMock(spec=AsyncSession)
    if db_fails:
        db.execute.side_effect = RuntimeError("database unavailable")
    with patch("src.app.modules.audit_logs.service.redis_cache") as cache:
        cache.client = client
        count = await flush_audit_log_buffer(max_flush_per_run=len(window), db=db)
    assert dead_letter == bad
    assert count == (0 if db_fails else 2)
    assert queue == (good + [b"newer"] if db_fails else [b"newer"])
    params = db.execute.call_args.args[0].compile().params
    assert params["user_id_m0"] == 0
    assert params["tenant_id_m0"] == UUID(int=1)
    pipe.ltrim.assert_called_once_with(AUDIT_LOG_BUFFER_KEY, len(window), -1)
    if db_fails:
        db.rollback.assert_awaited_once()
        pipe.lpush.assert_called_once_with(AUDIT_LOG_BUFFER_KEY, *reversed(good))
    else:
        db.commit.assert_awaited_once()
        pipe.lpush.assert_not_called()
