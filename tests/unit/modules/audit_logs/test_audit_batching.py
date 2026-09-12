"""P1-2: Audit Log Batching Tests.

Verifies that audit logs are buffered in Redis and batch-flushed.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
            mock_direct.assert_called_once_with(log_data, None)

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
