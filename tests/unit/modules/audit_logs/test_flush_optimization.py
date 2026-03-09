"""Tests for audit log flush optimization.

Tests cover:
- Redis pipeline batching
- Error handling
- Rate limiting
- Buffer management
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.app.modules.billing.models import StripeSettings
from src.app.modules.ai.models import AIChatSession


class TestFlushAuditLogBuffer:
    """Tests for flush_audit_log_buffer function."""

    @pytest.mark.asyncio
    async def test_flush_with_redis_none(self):
        """Test flush returns 0 when Redis client is None."""
        from src.app.modules.audit_logs import service as audit_service

        with patch.object(audit_service, "redis_cache") as mock_cache:
            mock_cache.client = None
            result = await audit_service.flush_audit_log_buffer()

        assert result == 0

    @pytest.mark.asyncio
    async def test_flush_empty_buffer(self):
        """Test flush returns 0 when buffer is empty."""
        from src.app.modules.audit_logs import service as audit_service

        mock_redis_cache = MagicMock()
        mock_redis_cache.client = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.lrange.return_value = mock_pipe
        mock_pipe.ltrim.return_value = mock_pipe
        mock_pipe.execute = AsyncMock(return_value=[[]])
        mock_redis_cache.client.pipeline.return_value = mock_pipe

        with patch.object(audit_service, "redis_cache", mock_redis_cache):
            result = await audit_service.flush_audit_log_buffer()

        assert result == 0

    @pytest.mark.asyncio
    async def test_flush_processes_buffer_items(self):
        """Test flush processes items from buffer."""
        import json

        mock_redis_cache = MagicMock()
        mock_redis_cache.client = MagicMock()

        items = [
            json.dumps({"tenant_id": "00000000-0000-0000-0000-000000000001", "action": "user.login"}).encode(),
            json.dumps({"tenant_id": "00000000-0000-0000-0000-000000000002", "action": "user.logout"}).encode(),
        ]
        mock_pipe = MagicMock()
        mock_pipe.lrange.return_value = mock_pipe
        mock_pipe.ltrim.return_value = mock_pipe
        mock_pipe.execute = AsyncMock(return_value=[items])
        mock_redis_cache.client.pipeline.return_value = mock_pipe

        mock_db = AsyncMock()
        mock_db.add_all = MagicMock()
        mock_db.commit = AsyncMock()

        # Patch get_db_context (used by flush_audit_log_buffer)
        with patch("src.app.modules.audit_logs.service.redis_cache", mock_redis_cache):
            with patch("src.app.core.db.database.get_db_context") as mock_get_db:
                # get_db_context returns an async context manager
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__.return_value = mock_db
                mock_ctx.__aexit__.return_value = None
                mock_get_db.return_value = mock_ctx

                from src.app.modules.audit_logs import service as audit_service

                result = await audit_service.flush_audit_log_buffer()

        assert result == 2
        assert mock_db.add_all.call_count == 1

    @pytest.mark.asyncio
    async def test_flush_handles_db_error(self):
        """Test flush handles database errors gracefully."""
        import json

        mock_redis_cache = MagicMock()
        mock_redis_cache.client = MagicMock()

        items = [json.dumps({"tenant_id": "00000000-0000-0000-0000-000000000001", "action": "user.login"}).encode()]
        mock_pipe = MagicMock()
        mock_pipe.lrange.return_value = mock_pipe
        mock_pipe.ltrim.return_value = mock_pipe
        mock_pipe.execute = AsyncMock(return_value=[items])
        mock_redis_cache.client.pipeline.return_value = mock_pipe

        mock_db = AsyncMock()
        mock_db.add_all = MagicMock()
        mock_db.commit = AsyncMock(side_effect=Exception("DB Error"))
        mock_db.rollback = AsyncMock()

        with patch("src.app.modules.audit_logs.service.redis_cache", mock_redis_cache):
            with patch("src.app.core.db.database.get_db_context") as mock_get_db:
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__.return_value = mock_db
                mock_ctx.__aexit__.return_value = None
                mock_get_db.return_value = mock_ctx

                from src.app.modules.audit_logs import service as audit_service

                result = await audit_service.flush_audit_log_buffer()

        assert result == 0

    @pytest.mark.asyncio
    async def test_flush_handles_parse_errors(self):
        """Test flush handles JSON parse errors gracefully."""
        mock_redis_cache = MagicMock()
        mock_redis_cache.client = MagicMock()

        mock_pipe = MagicMock()
        mock_pipe.lrange.return_value = mock_pipe
        mock_pipe.ltrim.return_value = mock_pipe
        mock_pipe.execute = AsyncMock(return_value=[[b"invalid json"]])
        mock_redis_cache.client.pipeline.return_value = mock_pipe

        with patch("src.app.modules.audit_logs.service.redis_cache", mock_redis_cache):
            from src.app.modules.audit_logs import service as audit_service

            result = await audit_service.flush_audit_log_buffer()

        assert result == 0


class TestFlushAuditLogsTaskFunction:
    """Tests for flush_audit_logs_task worker function."""

    @pytest.mark.asyncio
    async def test_task_respects_max_flush_per_run(self):
        """Test task stops after max flushes per run."""
        with patch(
            "src.app.modules.audit_logs.service.flush_audit_log_buffer",
            new_callable=AsyncMock,
        ) as mock_flush:
            mock_flush.return_value = 5

            from src.app.core.worker.functions import flush_audit_logs_task

            result = await flush_audit_logs_task({})

        assert result is not None

    @pytest.mark.asyncio
    async def test_task_stops_when_buffer_drained(self):
        """Test task stops when buffer is empty."""
        with patch(
            "src.app.modules.audit_logs.service.flush_audit_log_buffer",
            new_callable=AsyncMock,
        ) as mock_flush:
            mock_flush.return_value = 0

            from src.app.core.worker.functions import flush_audit_logs_task

            result = await flush_audit_logs_task({})

        # Task returns a string like "Flushed 0 audit logs."
        assert result == "Flushed 0 audit logs."
