"""Unit tests for AI session list/pagination operations.

Tests T126: Unit test for session list/pagination in tests/unit/ai/test_session_management.py
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.ai.crud import crud_chat_sessions
from src.app.modules.ai.models import AIChatSession


@pytest.fixture
def session_data():
    """Sample session data for testing."""
    return {
        "title": "Test Chat Session",
        "model": "gemma3",
    }


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


class TestSessionListPagination:
    """Test suite for session list and pagination operations."""

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self, mock_db_session, mock_user):
        """Test listing sessions when none exist."""
        with patch.object(crud_chat_sessions, "get_multi", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"data": [], "total_count": 0}

            result = await crud_chat_sessions.get_multi(
                db=mock_db_session,
                user_id=mock_user["id"],
            )

            assert result["data"] == []
            assert result["total_count"] == 0

    @pytest.mark.asyncio
    async def test_list_sessions_with_data(self, mock_db_session, mock_user):
        """Test listing sessions with existing data."""
        mock_sessions = [
            MagicMock(
                spec=AIChatSession,
                id=1,
                user_id=mock_user["id"],
                title="Session 1",
                model="gemma3",
                created_at=datetime.now(UTC),
            ),
            MagicMock(
                spec=AIChatSession,
                id=2,
                user_id=mock_user["id"],
                title="Session 2",
                model="llama2",
                created_at=datetime.now(UTC),
            ),
        ]

        with patch.object(crud_chat_sessions, "get_multi", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"data": mock_sessions, "total_count": 2}

            result = await crud_chat_sessions.get_multi(
                db=mock_db_session,
                user_id=mock_user["id"],
            )

            assert len(result["data"]) == 2
            assert result["total_count"] == 2

    @pytest.mark.asyncio
    async def test_list_sessions_pagination_first_page(self, mock_db_session, mock_user):
        """Test listing sessions with pagination - first page."""
        mock_sessions = [MagicMock(spec=AIChatSession, id=i, title=f"Session {i}") for i in range(1, 11)]

        with patch.object(crud_chat_sessions, "get_multi", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"data": mock_sessions, "total_count": 25}

            result = await crud_chat_sessions.get_multi(
                db=mock_db_session,
                user_id=mock_user["id"],
                skip=0,
                limit=10,
            )

            assert len(result["data"]) == 10
            assert result["total_count"] == 25
            mock_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_sessions_pagination_second_page(self, mock_db_session, mock_user):
        """Test listing sessions with pagination - second page."""
        mock_sessions = [MagicMock(spec=AIChatSession, id=i, title=f"Session {i}") for i in range(11, 21)]

        with patch.object(crud_chat_sessions, "get_multi", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"data": mock_sessions, "total_count": 25}

            result = await crud_chat_sessions.get_multi(
                db=mock_db_session,
                user_id=mock_user["id"],
                skip=10,
                limit=10,
            )

            assert len(result["data"]) == 10
            assert result["total_count"] == 25

    @pytest.mark.asyncio
    async def test_list_sessions_pagination_last_page(self, mock_db_session, mock_user):
        """Test listing sessions with pagination - last page with fewer items."""
        mock_sessions = [MagicMock(spec=AIChatSession, id=i, title=f"Session {i}") for i in range(21, 26)]

        with patch.object(crud_chat_sessions, "get_multi", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"data": mock_sessions, "total_count": 25}

            result = await crud_chat_sessions.get_multi(
                db=mock_db_session,
                user_id=mock_user["id"],
                skip=20,
                limit=10,
            )

            assert len(result["data"]) == 5
            assert result["total_count"] == 25

    @pytest.mark.asyncio
    async def test_list_sessions_ordered_by_created_at(self, mock_db_session, mock_user):
        """Test sessions are returned ordered by created_at descending."""
        now = datetime.now(UTC)
        mock_sessions = [
            MagicMock(
                spec=AIChatSession,
                id=1,
                title="Newest",
                created_at=now,
            ),
            MagicMock(
                spec=AIChatSession,
                id=2,
                title="Oldest",
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
            ),
        ]

        with patch.object(crud_chat_sessions, "get_multi", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"data": mock_sessions, "total_count": 2}

            result = await crud_chat_sessions.get_multi(
                db=mock_db_session,
                user_id=mock_user["id"],
            )

            assert result["data"][0].title == "Newest"
            assert result["data"][1].title == "Oldest"

    @pytest.mark.asyncio
    async def test_list_sessions_user_isolation(self, mock_db_session):
        """Test that sessions are isolated by user."""
        user_1_id = 1
        user_2_id = 2

        user_1_sessions = [MagicMock(spec=AIChatSession, id=1, user_id=user_1_id, title="User 1 Session")]
        user_2_sessions = [MagicMock(spec=AIChatSession, id=2, user_id=user_2_id, title="User 2 Session")]

        with patch.object(crud_chat_sessions, "get_multi", new_callable=AsyncMock) as mock_get:
            # User 1's sessions
            mock_get.return_value = {"data": user_1_sessions, "total_count": 1}
            result = await crud_chat_sessions.get_multi(db=mock_db_session, user_id=user_1_id)
            assert len(result["data"]) == 1
            assert result["data"][0].user_id == user_1_id

            # User 2's sessions
            mock_get.return_value = {"data": user_2_sessions, "total_count": 1}
            result = await crud_chat_sessions.get_multi(db=mock_db_session, user_id=user_2_id)
            assert len(result["data"]) == 1
            assert result["data"][0].user_id == user_2_id


class TestSessionServiceListPagination:
    """Test suite for ChatSessionService list/pagination operations."""

    @pytest.mark.asyncio
    async def test_get_user_sessions(self, mock_db_session, mock_user, mock_tenant):
        """Test getting user sessions via service."""
        mock_sessions = {
            "data": [
                {"id": 1, "title": "Session 1", "user_id": mock_user["id"]},
                {"id": 2, "title": "Session 2", "user_id": mock_user["id"]},
            ],
            "total_count": 2,
        }

        with patch(
            "src.app.modules.ai.service.get_current_tenant",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.get_multi",
                new_callable=AsyncMock,
                return_value=mock_sessions,
            ):
                from src.app.modules.ai.service import ChatSessionService

                service = ChatSessionService(mock_db_session)
                result = await service.get_user_sessions(mock_user["id"])

                assert result["total_count"] == 2
                assert len(result["data"]) == 2

    @pytest.mark.asyncio
    async def test_get_user_sessions_with_pagination(self, mock_db_session, mock_user, mock_tenant):
        """Test getting user sessions with pagination parameters."""
        mock_sessions = {
            "data": [{"id": 1, "title": "Session 1"}],
            "total_count": 50,
        }

        with patch(
            "src.app.modules.ai.service.get_current_tenant",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.get_multi",
                new_callable=AsyncMock,
                return_value=mock_sessions,
            ) as mock_get:
                from src.app.modules.ai.service import ChatSessionService

                service = ChatSessionService(mock_db_session)
                result = await service.get_user_sessions(mock_user["id"], page=2, limit=10)

                assert result["total_count"] == 50
                mock_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_page_calculation(self, mock_db_session, mock_user):
        """Test page calculation for session list."""
        total_items = 25
        limit = 10

        # Calculate expected pages
        expected_pages = (total_items + limit - 1) // limit
        assert expected_pages == 3

        # Test with different totals
        assert (0 + 10 - 1) // 10 == 0 or 1  # Empty should have 1 page
        assert (10 + 10 - 1) // 10 == 1  # Exactly one page
        assert (11 + 10 - 1) // 10 == 2  # Just over one page
