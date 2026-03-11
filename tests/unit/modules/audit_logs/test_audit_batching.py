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
        """Verify audit log buffering uses Redis RPUSH."""
        from src.app.modules.audit_logs.service import (
            AUDIT_LOG_BUFFER_KEY,
            buffer_audit_log,
        )

        mock_redis = MagicMock()
        # BUG FIX: service now directly awaits rpush(), so mock must be AsyncMock
        mock_redis.rpush = AsyncMock(return_value=1)

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
            mock_redis.rpush.assert_called_once()
            call_args = mock_redis.rpush.call_args
            assert call_args[0][0] == AUDIT_LOG_BUFFER_KEY

    @pytest.mark.asyncio
    async def test_fallback_on_redis_error(self) -> None:
        """Verify fallback to direct insert when Redis fails."""
        from redis.exceptions import RedisError

        from src.app.modules.audit_logs.service import buffer_audit_log

        mock_redis = MagicMock()
        mock_redis.rpush = AsyncMock(side_effect=RedisError("Connection failed"))

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

    def test_batch_insert_uses_add_all(self) -> None:
        """Verify batch insert uses add_all for efficiency."""
        import inspect

        from src.app.modules.audit_logs import service

        source = inspect.getsource(service)

        # Verify add_all is used for batch insert
        assert "add_all" in source

    def test_buffer_key_constant_exists(self) -> None:
        """Verify buffer key constant is defined."""
        from src.app.modules.audit_logs.service import AUDIT_LOG_BUFFER_KEY

        assert AUDIT_LOG_BUFFER_KEY == "audit_log_buffer"

    def test_batch_size_constant_exists(self) -> None:
        """Verify batch size constant is defined."""
        from src.app.modules.audit_logs.service import AUDIT_LOG_BATCH_SIZE

        assert AUDIT_LOG_BATCH_SIZE == 100
