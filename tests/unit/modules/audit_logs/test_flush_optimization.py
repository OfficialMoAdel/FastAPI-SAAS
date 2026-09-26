"""Tests for audit log flush optimization.

Tests cover:
- Redis pipeline batching
- Error handling
- Rate limiting
- Buffer management
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.app.modules.ai.models  # noqa: F401

# Import models needed to ensure SQLAlchemy mapper initialization completes.
# Tenant model has a relationship to StripeSettings which must be imported
# before any session triggers full mapper initialization.
import src.app.modules.billing.models  # noqa: F401


def _acm(pipe):
    """Make a pipeline mock support 'async with' returning itself."""

    async def __aenter__(*a):
        return pipe

    async def __aexit__(*a):
        return False

    pipe.__aenter__ = __aenter__
    pipe.__aexit__ = __aexit__
    return pipe


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
        _acm(mock_pipe)
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
        _acm(mock_pipe)
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
        assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_flush_handles_db_error(self):
        """Test flush handles database errors gracefully."""
        import json

        mock_redis_cache = MagicMock()
        mock_redis_cache.client = MagicMock()

        items = [json.dumps({"tenant_id": "00000000-0000-0000-0000-000000000001", "action": "user.login"}).encode()]
        mock_pipe = MagicMock()
        _acm(mock_pipe)
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
        mock_redis_cache.client.rpush = AsyncMock()
        mock_redis_cache.client.lpush = AsyncMock()
        mock_redis_cache.client.ltrim = AsyncMock()  # F15: quarantine restore trims the DLQ

        mock_pipe = MagicMock()
        _acm(mock_pipe)
        mock_pipe.lrange.return_value = mock_pipe
        mock_pipe.ltrim.return_value = mock_pipe
        mock_pipe.execute = AsyncMock(return_value=[[b"invalid json"]])
        mock_redis_cache.client.pipeline.return_value = mock_pipe

        with patch("src.app.modules.audit_logs.service.redis_cache", mock_redis_cache):
            from src.app.modules.audit_logs import service as audit_service

            result = await audit_service.flush_audit_log_buffer()

        assert result == 0


@contextmanager
def _registered_collaborators():
    """Wire the worker collaborator seam, as the worker process does.

    ``flush_audit_logs_task`` reaches the flush helper through the registered
    collaborators (core/worker/registry.py) rather than a lazy feature import,
    so a direct call in a test must register them. Done per-module, not in
    conftest: a module-level import there runs before the event loop each
    integration test builds.
    """
    import src.app.worker  # noqa: F401 — the import is the registration
    yield


class TestFlushAuditLogsTaskFunction:
    """Tests for flush_audit_logs_task worker function."""

    @pytest.mark.asyncio
    async def test_task_respects_max_flush_per_run(self):
        """Test task stops after max flushes per run."""
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = AsyncMock()
        mock_ctx.__aexit__.return_value = None

        with (
            _registered_collaborators(),
            patch(
                "src.app.core.worker.functions.require_feature_collaborators"
            ) as mock_collabs,
            patch("src.app.core.worker.functions.job_bypass_context", return_value=mock_ctx),
        ):
            mock_flush = AsyncMock()
            mock_collabs.return_value.flush_audit_log_buffer = mock_flush
            mock_collabs.return_value.audit_log_max_flush_per_run = 5
            mock_flush.return_value = 5

            from src.app.core.worker.functions import flush_audit_logs_task

            result = await flush_audit_logs_task({})

        assert result is not None

    @pytest.mark.asyncio
    async def test_task_stops_when_buffer_drained(self):
        """Test task stops when buffer is empty."""
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = AsyncMock()
        mock_ctx.__aexit__.return_value = None

        with (
            _registered_collaborators(),
            patch(
                "src.app.core.worker.functions.require_feature_collaborators"
            ) as mock_collabs,
            patch("src.app.core.worker.functions.job_bypass_context", return_value=mock_ctx),
        ):
            mock_flush = AsyncMock()
            mock_collabs.return_value.flush_audit_log_buffer = mock_flush
            mock_collabs.return_value.audit_log_max_flush_per_run = 5
            mock_flush.return_value = 0

            from src.app.core.worker.functions import flush_audit_logs_task

            result = await flush_audit_logs_task({})

        # Task returns a string like "Flushed 0 audit logs."
        assert result == "Flushed 0 audit logs."
