"""Unit tests for AI session limit enforcement.

Tests T134: Unit test for concurrent session limit (100 per tenant)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def mock_user():
    """Create a mock user context."""
    return {"id": 1, "email": "test@example.com"}


@pytest.fixture
def mock_tenant():
    """Create a mock tenant context."""
    tenant = MagicMock()
    tenant.id = 1
    return tenant


class TestSessionLimit:
    """Test suite for session limit enforcement."""

    @pytest.mark.asyncio
    async def test_create_session_under_limit(self, mock_db_session, mock_user, mock_tenant):
        """Test creating a session when under the limit succeeds."""
        from src.app.modules.ai.schemas import ChatSessionCreate

        session_data = ChatSessionCreate(title="Test Session", model="gemma3")
        mock_session = {
            "id": 1,
            "user_id": mock_user["id"],
            "tenant_id": mock_tenant.id,
            "title": "Test Session",
            "model": "gemma3",
        }

        with patch(
            "src.app.modules.ai.service.get_current_tenant",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.count",
                new_callable=AsyncMock,
                return_value=50,  # Under the 100 limit
            ):
                with patch(
                    "src.app.modules.ai.crud.crud_chat_sessions.create",
                    new_callable=AsyncMock,
                    return_value=mock_session,
                ):
                    # create_session re-reads the committed row to satisfy ChatSessionRead
                    with patch(
                        "src.app.modules.ai.crud.crud_chat_sessions.get",
                        new_callable=AsyncMock,
                        return_value={**mock_session, "created_at": "2026-01-01T00:00:00Z"},
                    ):
                        from src.app.modules.ai.service import ChatSessionService

                        service = ChatSessionService(mock_db_session)
                        result = await service.create_session(mock_user["id"], session_data)

                        assert result["id"] == 1
                        assert result["title"] == "Test Session"

    @pytest.mark.asyncio
    async def test_create_session_at_limit_fails(self, mock_db_session, mock_user, mock_tenant):
        """Test creating a session when at the limit raises error."""
        from src.app.modules.ai.schemas import ChatSessionCreate
        from src.app.modules.ai.unified_errors import SessionLimitExceededError

        session_data = ChatSessionCreate(title="Test Session", model="gemma3")

        with patch(
            "src.app.modules.ai.service.get_current_tenant",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.count",
                new_callable=AsyncMock,
                return_value=100,  # At the limit
            ):
                from src.app.modules.ai.service import ChatSessionService

                service = ChatSessionService(mock_db_session)

                with pytest.raises(SessionLimitExceededError) as exc_info:
                    await service.create_session(mock_user["id"], session_data)

                assert exc_info.value.tenant_id == mock_tenant.id
                assert exc_info.value.limit == 100
                assert exc_info.value.current_count == 100

    @pytest.mark.asyncio
    async def test_create_session_over_limit_fails(self, mock_db_session, mock_user, mock_tenant):
        """Test creating a session when over the limit raises error."""
        from src.app.modules.ai.schemas import ChatSessionCreate
        from src.app.modules.ai.unified_errors import SessionLimitExceededError

        session_data = ChatSessionCreate(title="Test Session", model="gemma3")

        with patch(
            "src.app.modules.ai.service.get_current_tenant",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.count",
                new_callable=AsyncMock,
                return_value=150,  # Over the limit
            ):
                from src.app.modules.ai.service import ChatSessionService

                service = ChatSessionService(mock_db_session)

                with pytest.raises(SessionLimitExceededError) as exc_info:
                    await service.create_session(mock_user["id"], session_data)

                assert exc_info.value.limit == 100
                assert exc_info.value.current_count == 150

    @pytest.mark.asyncio
    async def test_create_session_just_under_limit_succeeds(self, mock_db_session, mock_user, mock_tenant):
        """Test creating a session when at 99 sessions succeeds."""
        from src.app.modules.ai.schemas import ChatSessionCreate

        session_data = ChatSessionCreate(title="Test Session", model="gemma3")
        mock_session = {
            "id": 100,
            "user_id": mock_user["id"],
            "tenant_id": mock_tenant.id,
            "title": "Test Session",
            "model": "gemma3",
        }

        with patch(
            "src.app.modules.ai.service.get_current_tenant",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.count",
                new_callable=AsyncMock,
                return_value=99,  # Just under the limit
            ):
                with patch(
                    "src.app.modules.ai.crud.crud_chat_sessions.create",
                    new_callable=AsyncMock,
                    return_value=mock_session,
                ):
                    # create_session re-reads the committed row to satisfy ChatSessionRead
                    with patch(
                        "src.app.modules.ai.crud.crud_chat_sessions.get",
                        new_callable=AsyncMock,
                        return_value={**mock_session, "created_at": "2026-01-01T00:00:00Z"},
                    ):
                        from src.app.modules.ai.service import ChatSessionService

                        service = ChatSessionService(mock_db_session)
                        result = await service.create_session(mock_user["id"], session_data)

                        assert result["id"] == 100


class TestSessionLimitExceededError:
    """Test suite for SessionLimitExceededError exception."""

    def test_exception_message(self):
        """Test that exception has correct message format."""
        from src.app.modules.ai.unified_errors import SessionLimitExceededError

        error = SessionLimitExceededError(tenant_id=1, limit=100, current_count=100)

        assert "100" in str(error)
        assert "Session limit exceeded" in str(error)
        assert error.tenant_id == 1
        assert error.limit == 100
        assert error.current_count == 100

    def test_exception_default_limit(self):
        """Test exception uses default limit of 100."""
        from src.app.modules.ai.unified_errors import SessionLimitExceededError

        error = SessionLimitExceededError(tenant_id=5)

        assert error.limit == 100
        assert error.tenant_id == 5
        assert error.current_count is None


class TestSessionLimitConstant:
    """Test suite for MAX_SESSIONS_PER_TENANT constant."""

    def test_constant_value(self):
        """Test that MAX_SESSIONS_PER_TENANT is set to 100."""
        from src.app.modules.ai.service import MAX_SESSIONS_PER_TENANT

        assert MAX_SESSIONS_PER_TENANT == 100
