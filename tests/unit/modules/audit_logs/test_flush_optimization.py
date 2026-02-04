"""Unit tests for audit log batch flush optimization with max_flush_per_run parameter.

Tests verify that flush_audit_log_buffer() properly implements the max_flush_per_run
limit to prevent long-running tasks during high load.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.audit_logs.service import (
    AUDIT_LOG_BATCH_SIZE,
    AUDIT_LOG_BUFFER_KEY,
    AUDIT_LOG_MAX_FLUSH_PER_RUN,
    flush_audit_log_buffer,
)


@pytest.fixture
def mock_redis_client():
    """Create a mock Redis client."""
    redis = AsyncMock()
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=([], 0))
    redis.pipeline = MagicMock(return_value=pipe)
    redis.llen = AsyncMock(return_value=0)
    return redis


@pytest.fixture
def sample_log_data():
    """Create sample audit log data."""
    return {
        "tenant_id": 1,
        "action": "user.login",
        "entity_type": "user",
        "entity_id": "123e4567-e89b-12d3-a456-426614174000",
        "user_id": 1,
        "details": {"ip": "127.0.0.1"},
        "ip_address": "127.0.0.1",
        "user_agent": "Mozilla/5.0",
        "created_at": "2026-02-04T12:00:00",
    }


class TestFlushAuditLogBuffer:
    """Tests for flush_audit_log_buffer function."""

    @pytest.mark.asyncio
    async def test_flush_with_redis_none(self, monkeypatch):
        """Test that flush returns 0 when redis_client is None."""
        monkeypatch.setattr("src.app.modules.audit_logs.service.redis_client", None)
        result = await flush_audit_log_buffer(max_flush_per_run=100)
        assert result == 0

    @pytest.mark.asyncio
    async def test_flush_empty_buffer(self, mock_redis_client, monkeypatch):
        """Test that flush returns 0 when buffer is empty."""
        monkeypatch.setattr("src.app.modules.audit_logs.service.redis_client", mock_redis_client)
        result = await flush_audit_log_buffer(max_flush_per_run=100)
        assert result == 0

    @pytest.mark.asyncio
    async def test_flush_handles_db_error(self, mock_redis_client, sample_log_data, monkeypatch):
        """Test that flush handles database errors gracefully."""
        logs = [sample_log_data for _ in range(AUDIT_LOG_BATCH_SIZE)]
        serialized_logs = [str(log) for log in logs]
        mock_redis_client.pipeline.return_value.execute = AsyncMock(return_value=(serialized_logs, 0))

        monkeypatch.setattr("src.app.modules.audit_logs.service.redis_client", mock_redis_client)

        async def mock_async_get_db():
            db = AsyncMock()
            db.add_all = MagicMock()
            db.commit = AsyncMock(side_effect=Exception("DB connection failed"))
            db.rollback = AsyncMock()
            db.close = AsyncMock()
            yield db

        monkeypatch.setattr("src.app.core.db.database.async_get_db", mock_async_get_db)

        with patch("src.app.modules.audit_logs.service.logger") as mock_logger:
            result = await flush_audit_log_buffer(max_flush_per_run=100)
            assert result == 0
            assert mock_logger.error.called

    @pytest.mark.asyncio
    async def test_flush_handles_parse_errors(self, mock_redis_client, monkeypatch):
        """Test that flush handles malformed log data gracefully."""
        # Create valid log entry as JSON
        import orjson

        log = {
            "tenant_id": 1,
            "action": "user.login",
            "entity_type": "user",
            "entity_id": "123",
            "user_id": 1,
            "details": {},
            "ip_address": "127.0.0.1",
            "user_agent": "test",
            "created_at": "2026-02-04T12:00:00",
        }

        valid_log = orjson.dumps(log).decode()
        invalid_log = "invalid json data"
        mixed_logs = [valid_log, invalid_log, valid_log]
        mock_redis_client.pipeline.return_value.execute = AsyncMock(return_value=(mixed_logs, 0))

        monkeypatch.setattr("src.app.modules.audit_logs.service.redis_client", mock_redis_client)

        async def mock_async_get_db():
            db = AsyncMock()
            db.add_all = MagicMock()
            db.commit = AsyncMock()
            db.rollback = AsyncMock()
            db.close = AsyncMock()
            yield db

        monkeypatch.setattr("src.app.core.db.database.async_get_db", mock_async_get_db)

        with patch("src.app.modules.audit_logs.service.logger") as mock_logger:
            result = await flush_audit_log_buffer(max_flush_per_run=100)
            # Should flush 2 valid logs (skip 1 invalid)
            assert result == 2
            # Error should be logged for invalid data
            assert mock_logger.error.called


class TestFlushAuditLogsTask:
    """Tests for flush_audit_logs_task worker function."""

    @pytest.mark.asyncio
    async def test_task_respects_max_flush_per_run(self, monkeypatch):
        """Test that worker task respects AUDIT_LOG_MAX_FLUSH_PER_RUN limit."""
        call_count = [0]

        async def mock_flush(max_flush_per_run=None):
            call_count[0] += 1
            return AUDIT_LOG_BATCH_SIZE

        # Patch at the actual import location (inside flush_audit_logs_task)
        monkeypatch.setattr("src.app.modules.audit_logs.service.flush_audit_log_buffer", mock_flush)

        # Mock job_bypass_context as a context manager
        class MockBypassContext:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        monkeypatch.setattr("src.app.core.worker.functions.job_bypass_context", lambda reason: MockBypassContext())

        from src.app.core.worker.functions import flush_audit_logs_task

        await flush_audit_logs_task({})
        expected_calls = AUDIT_LOG_MAX_FLUSH_PER_RUN // AUDIT_LOG_BATCH_SIZE
        assert call_count[0] == expected_calls

    @pytest.mark.asyncio
    async def test_task_stops_when_buffer_drained(self, monkeypatch):
        """Test that worker task stops when buffer is drained (< BATCH_SIZE returned)."""
        flush_returns = [100, 100, 50, 0]
        call_index = [0]

        async def mock_flush(max_flush_per_run=None):
            result = flush_returns[call_index[0]]
            call_index[0] += 1
            return result

        # Patch at the actual import location (inside flush_audit_logs_task)
        monkeypatch.setattr("src.app.modules.audit_logs.service.flush_audit_log_buffer", mock_flush)

        # Mock job_bypass_context as a context manager
        class MockBypassContext:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        monkeypatch.setattr("src.app.core.worker.functions.job_bypass_context", lambda reason: MockBypassContext())

        from src.app.core.worker.functions import flush_audit_logs_task

        await flush_audit_logs_task({})
        assert call_index[0] == 3

    def test_config_constants(self):
        """Test that configuration constants are properly defined."""
        assert AUDIT_LOG_BATCH_SIZE == 100
        assert AUDIT_LOG_MAX_FLUSH_PER_RUN == 1000
        assert AUDIT_LOG_BUFFER_KEY == "audit_log_buffer"
