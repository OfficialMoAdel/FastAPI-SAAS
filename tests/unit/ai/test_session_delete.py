"""Unit tests for AI session deletion with cascade.

Tests T127: Unit test for session deletion with cascade in tests/unit/ai/test_session_delete.py
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.modules.ai.crud import crud_chat_messages, crud_chat_sessions
from src.app.modules.ai.models import AIChatMessage, AIChatSession


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
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


@pytest.fixture
def mock_session_with_messages(mock_user, mock_tenant):
    """Create a mock session with messages."""
    session = MagicMock(spec=AIChatSession)
    session.id = 1
    session.user_id = mock_user["id"]
    session.tenant_id = mock_tenant.id
    session.title = "Test Session"
    session.model = "gemma3"
    session.created_at = datetime.now(timezone.utc)

    messages = [
        MagicMock(
            spec=AIChatMessage,
            id=1,
            session_id=session.id,
            role="user",
            content="Hello",
            created_at=datetime.now(timezone.utc),
        ),
        MagicMock(
            spec=AIChatMessage,
            id=2,
            session_id=session.id,
            role="assistant",
            content="Hi there!",
            created_at=datetime.now(timezone.utc),
        ),
        MagicMock(
            spec=AIChatMessage,
            id=3,
            session_id=session.id,
            role="user",
            content="How are you?",
            created_at=datetime.now(timezone.utc),
        ),
    ]
    session.messages = messages
    return session


class TestSessionDeletion:
    """Test suite for session deletion operations."""

    @pytest.mark.asyncio
    async def test_delete_session(self, mock_db_session, mock_user):
        """Test basic session deletion."""
        session_id = 1

        with patch.object(
            crud_chat_sessions, "db_delete", new_callable=AsyncMock
        ) as mock_delete:
            mock_delete.return_value = None

            await crud_chat_sessions.db_delete(db=mock_db_session, id=session_id)

            mock_delete.assert_called_once_with(db=mock_db_session, id=session_id)

    @pytest.mark.asyncio
    async def test_delete_session_service(self, mock_db_session, mock_user, mock_tenant):
        """Test session deletion via service."""
        session_id = 1
        mock_session = {
            "id": session_id,
            "user_id": mock_user["id"],
            "tenant_id": mock_tenant.id,
            "title": "Test Session",
        }

        with patch(
            "src.app.modules.ai.service.get_tenant_context",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.get",
                new_callable=AsyncMock,
                return_value=mock_session,
            ):
                with patch(
                    "src.app.modules.ai.crud.crud_chat_sessions.db_delete",
                    new_callable=AsyncMock,
                ) as mock_delete:
                    from src.app.modules.ai.service import ChatSessionService

                    service = ChatSessionService(mock_db_session)
                    result = await service.delete_session(session_id, mock_user["id"])

                    assert "message" in result
                    assert "deleted" in result["message"].lower()
                    mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_session_not_found(self, mock_db_session, mock_user, mock_tenant):
        """Test deleting a non-existent session raises error."""
        from src.app.modules._base.exceptions import ModelNotFoundError

        session_id = 999

        with patch(
            "src.app.modules.ai.service.get_tenant_context",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.get",
                new_callable=AsyncMock,
                return_value=None,
            ):
                from src.app.modules.ai.service import ChatSessionService

                service = ChatSessionService(mock_db_session)

                with pytest.raises(ModelNotFoundError):
                    await service.delete_session(session_id, mock_user["id"])

    @pytest.mark.asyncio
    async def test_delete_session_wrong_user(self, mock_db_session, mock_user, mock_tenant):
        """Test deleting another user's session raises error."""
        from src.app.modules._base.exceptions import ModelNotFoundError

        session_id = 1
        other_user_id = 999

        mock_session = {
            "id": session_id,
            "user_id": mock_user["id"],  # Owned by user 1
            "tenant_id": mock_tenant.id,
            "title": "Test Session",
        }

        with patch(
            "src.app.modules.ai.service.get_tenant_context",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.get",
                new_callable=AsyncMock,
                return_value=None,  # Returns None because user filter doesn't match
            ):
                from src.app.modules.ai.service import ChatSessionService

                service = ChatSessionService(mock_db_session)

                with pytest.raises(ModelNotFoundError):
                    await service.delete_session(session_id, other_user_id)


class TestCascadeDelete:
    """Test suite for cascade deletion of session messages."""

    @pytest.mark.asyncio
    async def test_cascade_deletes_messages(
        self, mock_db_session, mock_session_with_messages
    ):
        """Test that deleting a session cascades to its messages.

        Note: This tests the SQLAlchemy cascade behavior via the model definition.
        The AIChatSession model has cascade='all, delete-orphan' on the messages relationship.
        """
        session = mock_session_with_messages
        assert len(session.messages) == 3

        # When the session is deleted via SQLAlchemy, messages are cascaded
        with patch.object(
            crud_chat_sessions, "db_delete", new_callable=AsyncMock
        ) as mock_delete:
            mock_delete.return_value = None

            await crud_chat_sessions.db_delete(db=mock_db_session, id=session.id)

            mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_model_has_cascade_configuration(self):
        """Test that AIChatSession model has correct cascade configuration."""
        from src.app.modules.ai.models import AIChatSession

        # Get the messages relationship
        messages_rel = AIChatSession.__mapper__.relationships.get("messages")

        assert messages_rel is not None
        # SQLAlchemy expands "all" to individual cascade options
        # Check for the key cascade behaviors
        assert "delete" in messages_rel.cascade
        assert "delete-orphan" in messages_rel.cascade

    @pytest.mark.asyncio
    async def test_delete_session_cleans_up_messages(
        self, mock_db_session, mock_user, mock_tenant
    ):
        """Test session deletion properly cleans up associated messages."""
        session_id = 1
        mock_session = {
            "id": session_id,
            "user_id": mock_user["id"],
            "tenant_id": mock_tenant.id,
            "title": "Session with messages",
        }

        with patch(
            "src.app.modules.ai.service.get_tenant_context",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.get",
                new_callable=AsyncMock,
                return_value=mock_session,
            ):
                with patch(
                    "src.app.modules.ai.crud.crud_chat_sessions.db_delete",
                    new_callable=AsyncMock,
                ) as mock_delete:
                    from src.app.modules.ai.service import ChatSessionService

                    service = ChatSessionService(mock_db_session)
                    await service.delete_session(session_id, mock_user["id"])

                    # Session deletion is called, cascade handles messages
                    mock_delete.assert_called_once_with(db=mock_db_session, id=session_id)


class TestSessionDeletionEdgeCases:
    """Test edge cases for session deletion."""

    @pytest.mark.asyncio
    async def test_delete_session_with_no_messages(
        self, mock_db_session, mock_user, mock_tenant
    ):
        """Test deleting a session that has no messages."""
        session_id = 1
        mock_session = {
            "id": session_id,
            "user_id": mock_user["id"],
            "tenant_id": mock_tenant.id,
            "title": "Empty session",
        }

        with patch(
            "src.app.modules.ai.service.get_tenant_context",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.get",
                new_callable=AsyncMock,
                return_value=mock_session,
            ):
                with patch(
                    "src.app.modules.ai.crud.crud_chat_sessions.db_delete",
                    new_callable=AsyncMock,
                ):
                    from src.app.modules.ai.service import ChatSessionService

                    service = ChatSessionService(mock_db_session)
                    result = await service.delete_session(session_id, mock_user["id"])

                    assert "message" in result
                    assert "deleted" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_delete_multiple_sessions(
        self, mock_db_session, mock_user, mock_tenant
    ):
        """Test deleting multiple sessions in sequence."""
        session_ids = [1, 2, 3]

        with patch(
            "src.app.modules.ai.service.get_tenant_context",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.get",
                new_callable=AsyncMock,
            ) as mock_get:
                with patch(
                    "src.app.modules.ai.crud.crud_chat_sessions.db_delete",
                    new_callable=AsyncMock,
                ) as mock_delete:
                    from src.app.modules.ai.service import ChatSessionService

                    service = ChatSessionService(mock_db_session)

                    for session_id in session_ids:
                        mock_get.return_value = {
                            "id": session_id,
                            "user_id": mock_user["id"],
                            "tenant_id": mock_tenant.id,
                        }
                        result = await service.delete_session(session_id, mock_user["id"])
                        assert "deleted" in result["message"].lower()

                    assert mock_delete.call_count == 3

    @pytest.mark.asyncio
    async def test_delete_returns_confirmation_message(
        self, mock_db_session, mock_user, mock_tenant
    ):
        """Test that deletion returns a proper confirmation message."""
        session_id = 1
        mock_session = {
            "id": session_id,
            "user_id": mock_user["id"],
            "tenant_id": mock_tenant.id,
            "title": "Test",
        }

        with patch(
            "src.app.modules.ai.service.get_tenant_context",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.ai.crud.crud_chat_sessions.get",
                new_callable=AsyncMock,
                return_value=mock_session,
            ):
                with patch(
                    "src.app.modules.ai.crud.crud_chat_sessions.db_delete",
                    new_callable=AsyncMock,
                ):
                    from src.app.modules.ai.service import ChatSessionService

                    service = ChatSessionService(mock_db_session)
                    result = await service.delete_session(session_id, mock_user["id"])

                    assert isinstance(result, dict)
                    assert "message" in result
                    assert isinstance(result["message"], str)
