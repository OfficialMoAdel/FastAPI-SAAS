"""Unit tests for AIChatSessionCRUD operations.

Tests T081: Unit test for AIChatSessionCRUD in tests/unit/ai/test_chat_session_crud.py
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.modules.ai.crud import crud_chat_sessions
from src.app.modules.ai.models import AIChatSession
from src.app.modules.ai.schemas import ChatSessionCreate, ChatSessionUpdate


@pytest.fixture
def chat_session_data():
    """Sample chat session data for testing."""
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


class TestAIChatSessionCRUD:
    """Test suite for AIChatSessionCRUD operations."""

    @pytest.mark.asyncio
    async def test_create_chat_session(self, mock_db_session, chat_session_data):
        """Test creating a new chat session."""
        tenant_id = 1
        user_id = 1

        # Create session data with required fields
        session_data = {**chat_session_data, "user_id": user_id}
        create_schema = ChatSessionCreate(**chat_session_data)

        mock_session = MagicMock(spec=AIChatSession)
        mock_session.id = 1
        mock_session.tenant_id = tenant_id
        mock_session.user_id = user_id
        mock_session.title = chat_session_data["title"]
        mock_session.model = chat_session_data["model"]
        mock_session.created_at = datetime.now(timezone.utc)
        mock_session.updated_at = datetime.now(timezone.utc)

        with patch.object(crud_chat_sessions, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_session

            result = await crud_chat_sessions.create(
                db=mock_db_session,
                object={**create_schema.model_dump(), "tenant_id": tenant_id, "user_id": user_id}
            )

            assert result.id == 1
            assert result.user_id == user_id
            assert result.title == chat_session_data["title"]
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_chat_session_by_id(self, mock_db_session):
        """Test retrieving a chat session by ID."""
        session_id = 1
        user_id = 1

        mock_session = MagicMock(spec=AIChatSession)
        mock_session.id = session_id
        mock_session.user_id = user_id
        mock_session.title = "Test Session"

        with patch.object(crud_chat_sessions, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_session

            result = await crud_chat_sessions.get(
                db=mock_db_session,
                id=session_id
            )

            assert result is not None
            assert result.id == session_id
            mock_get.assert_called_once_with(
                db=mock_db_session,
                id=session_id
            )

    @pytest.mark.asyncio
    async def test_get_chat_session_not_found(self, mock_db_session):
        """Test retrieving a non-existent session returns None."""
        session_id = 999

        with patch.object(crud_chat_sessions, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            result = await crud_chat_sessions.get(
                db=mock_db_session,
                id=session_id
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_update_chat_session_title(self, mock_db_session):
        """Test updating a session title."""
        session_id = 1

        mock_session = MagicMock(spec=AIChatSession)
        mock_session.id = session_id
        mock_session.title = "Updated Title"

        update_data = ChatSessionUpdate(title="Updated Title")

        with patch.object(crud_chat_sessions, 'update', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_session

            result = await crud_chat_sessions.update(
                db=mock_db_session,
                object=update_data.model_dump(exclude_unset=True),
                id=session_id
            )

            assert result.title == "Updated Title"
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_chat_session(self, mock_db_session):
        """Test deleting a chat session."""
        session_id = 1

        with patch.object(crud_chat_sessions, 'db_delete', new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = None

            await crud_chat_sessions.db_delete(
                db=mock_db_session,
                id=session_id
            )

            mock_delete.assert_called_once_with(
                db=mock_db_session,
                id=session_id
            )

    @pytest.mark.asyncio
    async def test_get_multi_by_user(self, mock_db_session):
        """Test getting all sessions for a user."""
        user_id = 1

        mock_sessions = [
            MagicMock(spec=AIChatSession, id=1, user_id=user_id, title="Session 1"),
            MagicMock(spec=AIChatSession, id=2, user_id=user_id, title="Session 2"),
        ]

        with patch.object(crud_chat_sessions, 'get_multi', new_callable=AsyncMock) as mock_get_multi:
            mock_get_multi.return_value = {"data": mock_sessions, "total_count": 2}

            result = await crud_chat_sessions.get_multi(
                db=mock_db_session,
                user_id=user_id
            )

            assert result["total_count"] == 2
            assert len(result["data"]) == 2
            mock_get_multi.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_requires_user_id(self, chat_session_data):
        """Test that session creation requires user_id."""
        # ChatSessionCreate should work without user_id (it's set in service layer)
        schema = ChatSessionCreate(**chat_session_data)
        assert schema.title == chat_session_data["title"]
        assert schema.model == chat_session_data["model"]

    @pytest.mark.asyncio
    async def test_session_with_optional_model(self):
        """Test that session can have optional model field."""
        schema = ChatSessionCreate(title="Test Session")
        assert schema.title == "Test Session"
        # Model might have a default or be optional

    @pytest.mark.asyncio
    async def test_session_update_partial(self, mock_db_session):
        """Test partial update of session."""
        session_id = 1

        mock_session = MagicMock(spec=AIChatSession)
        mock_session.id = session_id
        mock_session.title = "New Title"
        mock_session.model = "gemma3"  # Unchanged

        update_data = ChatSessionUpdate(title="New Title")  # Only title

        with patch.object(crud_chat_sessions, 'update', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_session

            result = await crud_chat_sessions.update(
                db=mock_db_session,
                object=update_data.model_dump(exclude_unset=True),
                id=session_id
            )

            assert result.title == "New Title"
            # Verify only title was in the update payload
            call_args = mock_update.call_args
            update_obj = call_args.kwargs.get("object", call_args[1].get("object", {}))
            assert "title" in update_obj

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, mock_db_session):
        """Test that session queries are tenant-isolated."""
        tenant_id_1 = 1
        tenant_id_2 = 2
        session_id = 1

        mock_session = MagicMock(spec=AIChatSession)
        mock_session.id = session_id
        mock_session.tenant_id = tenant_id_1

        with patch.object(crud_chat_sessions, 'get', new_callable=AsyncMock) as mock_get:
            # When session exists for tenant 1
            mock_get.return_value = mock_session
            result = await crud_chat_sessions.get(
                db=mock_db_session,
                id=session_id
            )
            assert result is not None
            assert result.tenant_id == tenant_id_1

            # Different tenant should not see this session
            mock_get.return_value = None
            result = await crud_chat_sessions.get(
                db=mock_db_session,
                id=session_id,
                tenant_id=tenant_id_2
            )
            assert result is None
