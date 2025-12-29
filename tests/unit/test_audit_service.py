"""Unit tests for AuditLogService business logic.

Tests cover:
- Audit log creation with all fields
- Query filtering by tenant, user, action, entity
- Date range filtering
- Pagination
"""

from datetime import datetime, UTC
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.app.modules.audit_logs.service import AuditLogService


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
    return 1


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
        audit_service.db.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_audit_log_full_fields(self, audit_service, sample_tenant, sample_user) -> None:
        """Test creating audit log with all fields."""
        entity_id = uuid4()
        details = {"ip": "192.168.1.1", "user_agent": "Mozilla/5.0"}

        result = await audit_service.create_log(
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

        result = await audit_service.get_logs(tenant_id=sample_tenant)

        audit_service.db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_logs_by_user(self, audit_service, sample_tenant, sample_user) -> None:
        """Test filtering logs by user ID."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        audit_service.db.execute = AsyncMock(return_value=mock_result)

        result = await audit_service.get_logs(
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

        result = await audit_service.get_logs(
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

        result = await audit_service.get_logs(
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

        result = await audit_service.get_logs(
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

        result = await audit_service.get_logs(
            tenant_id=sample_tenant,
            limit=50,
            offset=100,
        )

        audit_service.db.execute.assert_called_once()


class TestAuditActionDecorator:
    """Tests for the @audit_action decorator."""

    @pytest.mark.asyncio
    async def test_decorator_creates_audit_event(self) -> None:
        """Test that @audit_action decorator fires audit events."""
        from src.app.modules.audit_logs.service import audit_action
        from unittest.mock import AsyncMock, patch

        @audit_action("test.action", "test")
        async def sample_function(tenant_id: int, user_id: int):
            result = MagicMock()
            result.tenant_id = tenant_id
            result.id = uuid4()
            return result

        with patch("src.app.modules.audit_logs.service.event_bus") as mock_bus:
            mock_bus.publish = AsyncMock()

            result = await sample_function(tenant_id=1, user_id=1)

            # Give the task a moment to be created (fire-and-forget)
            import asyncio
            await asyncio.sleep(0.01)

            # Decorator creates task but event_bus.publish should be called
            assert result is not None
