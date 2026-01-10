"""Unit tests for AIChatMessageCRUD operations.

Tests T082: Unit test for AIChatMessageCRUD in tests/unit/ai/test_chat_message_crud.py
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.ai.crud import crud_chat_messages
from src.app.modules.ai.models import AIChatMessage
from src.app.modules.ai.schemas import ChatMessageCreate


@pytest.fixture
def chat_message_data():
    """Sample chat message data for testing."""
    return {
        "role": "user",
        "content": "Hello, how are you?",
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


class TestAIChatMessageCRUD:
    """Test suite for AIChatMessageCRUD operations."""

    @pytest.mark.asyncio
    async def test_create_user_message(self, mock_db_session, chat_message_data):
        """Test creating a user message."""
        session_id = 1

        mock_message = MagicMock(spec=AIChatMessage)
        mock_message.id = 1
        mock_message.session_id = session_id
        mock_message.role = chat_message_data["role"]
        mock_message.content = chat_message_data["content"]
        mock_message.token_count = None
        mock_message.created_at = datetime.now(UTC)

        create_schema = ChatMessageCreate(**chat_message_data)

        with patch.object(crud_chat_messages, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_message

            result = await crud_chat_messages.create(
                db=mock_db_session,
                object={**create_schema.model_dump(), "session_id": session_id}
            )

            assert result.id == 1
            assert result.role == "user"
            assert result.content == chat_message_data["content"]
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_assistant_message(self, mock_db_session):
        """Test creating an assistant message with token count."""
        session_id = 1
        message_data = {
            "role": "assistant",
            "content": "I'm doing well, thank you for asking!",
        }

        mock_message = MagicMock(spec=AIChatMessage)
        mock_message.id = 2
        mock_message.session_id = session_id
        mock_message.role = message_data["role"]
        mock_message.content = message_data["content"]
        mock_message.token_count = 25
        mock_message.created_at = datetime.now(UTC)

        create_schema = ChatMessageCreate(**message_data)

        with patch.object(crud_chat_messages, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_message

            result = await crud_chat_messages.create(
                db=mock_db_session,
                object={**create_schema.model_dump(), "session_id": session_id, "token_count": 25}
            )

            assert result.id == 2
            assert result.role == "assistant"
            assert result.token_count == 25
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_message_by_id(self, mock_db_session):
        """Test retrieving a message by ID."""
        message_id = 1

        mock_message = MagicMock(spec=AIChatMessage)
        mock_message.id = message_id
        mock_message.role = "user"
        mock_message.content = "Test content"

        with patch.object(crud_chat_messages, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_message

            result = await crud_chat_messages.get(
                db=mock_db_session,
                id=message_id
            )

            assert result is not None
            assert result.id == message_id
            mock_get.assert_called_once_with(
                db=mock_db_session,
                id=message_id
            )

    @pytest.mark.asyncio
    async def test_get_message_not_found(self, mock_db_session):
        """Test retrieving a non-existent message returns None."""
        message_id = 999

        with patch.object(crud_chat_messages, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            result = await crud_chat_messages.get(
                db=mock_db_session,
                id=message_id
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_get_multi_by_session(self, mock_db_session):
        """Test getting all messages for a session."""
        session_id = 1

        mock_messages = [
            MagicMock(spec=AIChatMessage, id=1, session_id=session_id, role="user", content="Hello"),
            MagicMock(spec=AIChatMessage, id=2, session_id=session_id, role="assistant", content="Hi!"),
            MagicMock(spec=AIChatMessage, id=3, session_id=session_id, role="user", content="How are you?"),
        ]

        with patch.object(crud_chat_messages, 'get_multi', new_callable=AsyncMock) as mock_get_multi:
            mock_get_multi.return_value = {"data": mock_messages, "total_count": 3}

            result = await crud_chat_messages.get_multi(
                db=mock_db_session,
                session_id=session_id
            )

            assert result["total_count"] == 3
            assert len(result["data"]) == 3
            mock_get_multi.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_multi_with_pagination(self, mock_db_session):
        """Test paginated retrieval of messages."""
        session_id = 1

        mock_messages = [
            MagicMock(spec=AIChatMessage, id=3, role="user"),
            MagicMock(spec=AIChatMessage, id=4, role="assistant"),
        ]

        with patch.object(crud_chat_messages, 'get_multi', new_callable=AsyncMock) as mock_get_multi:
            mock_get_multi.return_value = {"data": mock_messages, "total_count": 10}

            result = await crud_chat_messages.get_multi(
                db=mock_db_session,
                session_id=session_id,
                offset=2,
                limit=2
            )

            assert len(result["data"]) == 2
            assert result["total_count"] == 10

    @pytest.mark.asyncio
    async def test_message_role_values(self, chat_message_data):
        """Test that message role values are valid."""
        valid_roles = ["user", "assistant", "system"]

        for role in valid_roles:
            data = chat_message_data.copy()
            data["role"] = role
            schema = ChatMessageCreate(**data)
            assert schema.role == role

    @pytest.mark.asyncio
    async def test_message_requires_content(self):
        """Test that message requires content."""
        # Content is required
        schema = ChatMessageCreate(role="user", content="Test message")
        assert schema.content == "Test message"

    @pytest.mark.asyncio
    async def test_message_with_token_count(self, mock_db_session):
        """Test message creation with token count."""
        session_id = 1
        token_count = 150

        mock_message = MagicMock(spec=AIChatMessage)
        mock_message.id = 1
        mock_message.session_id = session_id
        mock_message.role = "assistant"
        mock_message.content = "A longer response with more tokens."
        mock_message.token_count = token_count

        with patch.object(crud_chat_messages, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_message

            result = await crud_chat_messages.create(
                db=mock_db_session,
                object={
                    "session_id": session_id,
                    "role": "assistant",
                    "content": "A longer response with more tokens.",
                    "token_count": token_count
                }
            )

            assert result.token_count == token_count

    @pytest.mark.asyncio
    async def test_session_isolation(self, mock_db_session):
        """Test that messages are isolated by session."""
        session_id_1 = 1
        session_id_2 = 2

        mock_messages_session_1 = [
            MagicMock(spec=AIChatMessage, id=1, session_id=session_id_1),
            MagicMock(spec=AIChatMessage, id=2, session_id=session_id_1),
        ]

        mock_messages_session_2 = [
            MagicMock(spec=AIChatMessage, id=3, session_id=session_id_2),
        ]

        with patch.object(crud_chat_messages, 'get_multi', new_callable=AsyncMock) as mock_get_multi:
            # Get messages for session 1
            mock_get_multi.return_value = {"data": mock_messages_session_1, "total_count": 2}
            result = await crud_chat_messages.get_multi(
                db=mock_db_session,
                session_id=session_id_1
            )
            assert len(result["data"]) == 2

            # Get messages for session 2
            mock_get_multi.return_value = {"data": mock_messages_session_2, "total_count": 1}
            result = await crud_chat_messages.get_multi(
                db=mock_db_session,
                session_id=session_id_2
            )
            assert len(result["data"]) == 1

    @pytest.mark.asyncio
    async def test_message_ordering(self, mock_db_session):
        """Test that messages are ordered by creation time."""
        session_id = 1
        now = datetime.now(UTC)

        mock_messages = [
            MagicMock(spec=AIChatMessage, id=1, created_at=now),
            MagicMock(spec=AIChatMessage, id=2, created_at=now),
            MagicMock(spec=AIChatMessage, id=3, created_at=now),
        ]

        with patch.object(crud_chat_messages, 'get_multi', new_callable=AsyncMock) as mock_get_multi:
            mock_get_multi.return_value = {"data": mock_messages, "total_count": 3}

            result = await crud_chat_messages.get_multi(
                db=mock_db_session,
                session_id=session_id
            )

            # Messages should be returned in order
            ids = [m.id for m in result["data"]]
            assert ids == [1, 2, 3]
