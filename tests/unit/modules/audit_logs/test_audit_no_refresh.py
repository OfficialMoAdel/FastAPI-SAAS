"""Tests for audit log refresh() removal optimization.

Tests verify:
- create_log does NOT call session.refresh()
- Only INSERT is executed, no subsequent SELECT
- Fields are set correctly without refresh
- Performance improvement from avoiding extra DB round-trip
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.audit_logs.service import AuditLogService


class TestAuditLogNoRefresh:
    """Tests verifying refresh() is not called after create_log."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_create_log_does_not_call_refresh(self, mock_db):
        """Should not call session.refresh() after commit."""
        service = AuditLogService(mock_db)

        await service.create_log(
            tenant_id=1,
            action="user.created",
            entity_type="user",
            entity_id="123",
            user_id=1,
        )

        # Verify refresh was NOT called
        mock_db.refresh.assert_not_called()
        # But add and commit should be called
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_log_only_executes_insert(self, mock_db):
        """Should only execute INSERT, no SELECT after."""
        service = AuditLogService(mock_db)

        await service.create_log(
            tenant_id=1,
            action="post.updated",
        )

        # Get all calls to the mock
        all_calls = mock_db.method_calls

        # Should have: add(), commit()
        # Should NOT have: refresh(), execute() for SELECT
        call_names = [c[0] for c in all_calls]
        assert "add" in call_names
        assert "commit" in call_names
        assert "refresh" not in call_names

    @pytest.mark.asyncio
    async def test_create_log_returns_log_without_refresh(self, mock_db):
        """Should return the log entry without needing refresh."""
        service = AuditLogService(mock_db)

        result = await service.create_log(
            tenant_id=1,
            action="file.uploaded",
            entity_type="file",
            entity_id="file-456",
            user_id=2,
            details={"size": 1024},
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
        )

        # Result should have all fields set (pre-assigned, not from DB)
        assert result.tenant_id == 1
        assert result.action == "file.uploaded"
        assert result.entity_type == "file"
        assert result.entity_id == "file-456"
        assert result.user_id == 2
        assert result.details == {"size": 1024}
        assert result.ip_address == "192.168.1.1"
        assert result.user_agent == "Mozilla/5.0"

    @pytest.mark.asyncio
    async def test_create_log_with_minimal_fields(self, mock_db):
        """Should work with only required fields."""
        service = AuditLogService(mock_db)

        result = await service.create_log(
            tenant_id=1,
            action="system.started",
        )

        assert result.tenant_id == 1
        assert result.action == "system.started"
        assert result.entity_type is None
        assert result.entity_id is None
        assert result.user_id is None
        assert result.details == {}
        mock_db.refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_log_details_defaults_to_empty_dict(self, mock_db):
        """Should default details to empty dict, not None."""
        service = AuditLogService(mock_db)

        result = await service.create_log(
            tenant_id=1,
            action="test.action",
            details=None,
        )

        # Details should be empty dict, not None
        assert result.details == {}
        mock_db.refresh.assert_not_called()


class TestAuditLogPerformanceOptimization:
    """Tests verifying performance characteristics."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session with call tracking."""
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        db._call_order = []

        # Track call order
        original_add = db.add
        original_commit = db.commit
        original_refresh = db.refresh

        def track_add(*args, **kwargs):
            db._call_order.append("add")
            return original_add(*args, **kwargs)

        async def track_commit(*args, **kwargs):
            db._call_order.append("commit")
            return await original_commit(*args, **kwargs)

        async def track_refresh(*args, **kwargs):
            db._call_order.append("refresh")
            return await original_refresh(*args, **kwargs)

        db.add = MagicMock(side_effect=track_add)
        db.commit = AsyncMock(side_effect=track_commit)
        db.refresh = AsyncMock(side_effect=track_refresh)

        return db

    @pytest.mark.asyncio
    async def test_only_two_db_operations(self, mock_db):
        """Should only perform add() and commit() - no refresh."""
        service = AuditLogService(mock_db)

        await service.create_log(tenant_id=1, action="test")

        # Should only have 2 operations: add, commit
        assert len(mock_db._call_order) == 2
        assert mock_db._call_order == ["add", "commit"]

    @pytest.mark.asyncio
    async def test_call_order_is_add_then_commit(self, mock_db):
        """Should call add() before commit()."""
        service = AuditLogService(mock_db)

        await service.create_log(tenant_id=1, action="test")

        # Verify order
        assert mock_db._call_order.index("add") < mock_db._call_order.index("commit")

    @pytest.mark.asyncio
    async def test_no_extra_queries_after_commit(self, mock_db):
        """Should not execute any queries after commit."""
        service = AuditLogService(mock_db)

        await service.create_log(tenant_id=1, action="test")

        # After commit, nothing else should be called
        commit_index = mock_db._call_order.index("commit")
        operations_after_commit = mock_db._call_order[commit_index + 1 :]
        assert len(operations_after_commit) == 0


class TestAuditLogLogging:
    """Tests for audit log logging behavior."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_logs_audit_creation(self, mock_db):
        """Should log when audit entry is created."""
        service = AuditLogService(mock_db)

        with patch("src.app.modules.audit_logs.service.logger") as mock_logger:
            await service.create_log(
                tenant_id=1,
                action="user.login",
                entity_type="user",
            )

            mock_logger.info.assert_called_once()
            call_kwargs = mock_logger.info.call_args[1]
            assert call_kwargs["action"] == "user.login"
            assert call_kwargs["entity_type"] == "user"
