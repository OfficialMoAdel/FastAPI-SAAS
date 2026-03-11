"""Unit tests for AI service layer.

TDD tests for AI chat sessions, messages, and user settings.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestChatSessionService:
    """Test chat session service operations."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def mock_tenant_context(self):
        """Create a mock tenant context."""
        tenant = MagicMock()
        tenant.id = 1
        return tenant

    @pytest.mark.asyncio
    async def test_create_session_injects_tenant_and_user_id(self, mock_db, mock_tenant_context):
        """Test that create_session injects tenant_id and user_id."""
        from src.app.modules.ai.schemas import ChatSessionCreate
        from src.app.modules.ai.service import ChatSessionService

        with patch("src.app.modules.ai.service.get_tenant_context", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud:
                mock_crud.create = AsyncMock(return_value=MagicMock(id=1, tenant_id=1, user_id=1))
                mock_crud.count = AsyncMock(return_value=0)

                service = ChatSessionService(mock_db)
                data = ChatSessionCreate(title="Test Session")
                await service.create_session(user_id=1, data=data)

                call_args = mock_crud.create.call_args
                assert call_args.kwargs["object"]["tenant_id"] == 1
                assert call_args.kwargs["object"]["user_id"] == 1

    @pytest.mark.asyncio
    async def test_create_session_raises_without_tenant(self, mock_db):
        """Test that create_session raises ModelNotFoundError without tenant context."""
        from src.app.modules._base.exceptions import ModelNotFoundError
        from src.app.modules.ai.schemas import ChatSessionCreate
        from src.app.modules.ai.service import ChatSessionService

        with patch("src.app.modules.ai.service.get_tenant_context", return_value=None):
            service = ChatSessionService(mock_db)
            data = ChatSessionCreate(title="Test Session")

            with pytest.raises(ModelNotFoundError):
                await service.create_session(user_id=1, data=data)

    @pytest.mark.asyncio
    async def test_get_session_verifies_ownership(self, mock_db):
        """Test that get_session checks user ownership."""
        from src.app.modules._base.exceptions import ModelNotFoundError
        from src.app.modules.ai.service import ChatSessionService

        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud:
            mock_crud.get = AsyncMock(return_value={"id": 1, "user_id": 2})  # Different user

            service = ChatSessionService(mock_db)

            with pytest.raises(ModelNotFoundError):
                await service.get_session(session_id=1, user_id=1)

    @pytest.mark.asyncio
    async def test_get_session_returns_session_for_owner(self, mock_db):
        """Test that get_session returns session for the owner."""
        from src.app.modules.ai.service import ChatSessionService

        expected_session = {"id": 1, "user_id": 1, "title": "Test"}

        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud:
            mock_crud.get = AsyncMock(return_value=expected_session)

            service = ChatSessionService(mock_db)
            result = await service.get_session(session_id=1, user_id=1)

            assert result == expected_session

    @pytest.mark.asyncio
    async def test_get_user_sessions_filters_by_user_id(self, mock_db):
        """Test that get_user_sessions filters by user_id."""
        from src.app.modules.ai.service import ChatSessionService

        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud:
            mock_crud.get_multi = AsyncMock(return_value={"data": [], "total_count": 0})

            service = ChatSessionService(mock_db)
            await service.get_user_sessions(user_id=1, page=1, limit=20)

            call_args = mock_crud.get_multi.call_args
            assert call_args.kwargs["user_id"] == 1

    @pytest.mark.asyncio
    async def test_delete_session_verifies_ownership(self, mock_db):
        """Test that delete_session checks user ownership."""
        from src.app.modules._base.exceptions import ModelNotFoundError
        from src.app.modules.ai.service import ChatSessionService

        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud:
            mock_crud.get = AsyncMock(return_value={"id": 1, "user_id": 2})

            service = ChatSessionService(mock_db)

            with pytest.raises(ModelNotFoundError):
                await service.delete_session(session_id=1, user_id=1)

    @pytest.mark.asyncio
    async def test_delete_session_hard_deletes(self, mock_db):
        """Test that delete_session performs hard delete."""
        from src.app.modules.ai.service import ChatSessionService

        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud:
            mock_crud.get = AsyncMock(return_value={"id": 1, "user_id": 1})
            mock_crud.db_delete = AsyncMock()

            service = ChatSessionService(mock_db)
            result = await service.delete_session(session_id=1, user_id=1)

            mock_crud.db_delete.assert_called_once_with(db=mock_db, id=1)
            assert "message" in result


class TestChatMessageService:
    """Test chat message service operations."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_add_message_includes_session_id(self, mock_db):
        """Test that add_message includes session_id."""
        from src.app.modules.ai.schemas import ChatMessageCreate
        from src.app.modules.ai.service import ChatMessageService

        with patch("src.app.modules.ai.service.crud_chat_messages") as mock_crud:
            mock_crud.create = AsyncMock(return_value={"id": 1, "session_id": 1})

            service = ChatMessageService(mock_db)
            data = ChatMessageCreate(role="user", content="Hello")
            await service.add_message(session_id=1, data=data)

            # Verify create was called with a dict containing session_id
            call_args = mock_crud.create.call_args
            schema_obj = call_args.kwargs["object"]
            # The service now passes a dict from model_dump()
            if isinstance(schema_obj, dict):
                assert schema_obj["session_id"] == 1
            else:
                assert schema_obj.session_id == 1

    @pytest.mark.asyncio
    async def test_add_message_includes_token_count_if_provided(self, mock_db):
        """Test that add_message includes token_count when provided."""
        from src.app.modules.ai.schemas import ChatMessageCreate
        from src.app.modules.ai.service import ChatMessageService

        with patch("src.app.modules.ai.service.crud_chat_messages") as mock_crud:
            mock_crud.create = AsyncMock(return_value={"id": 1, "session_id": 1, "token_count": 50})

            service = ChatMessageService(mock_db)
            data = ChatMessageCreate(role="assistant", content="Hello!")
            await service.add_message(session_id=1, data=data, token_count=50)

            # Verify create was called with a dict containing token_count
            call_args = mock_crud.create.call_args
            schema_obj = call_args.kwargs["object"]
            # The service now passes a dict from model_dump()
            if isinstance(schema_obj, dict):
                assert schema_obj["token_count"] == 50
            else:
                assert schema_obj.token_count == 50

    @pytest.mark.asyncio
    async def test_get_session_messages_filters_by_session_id(self, mock_db):
        """Test that get_session_messages filters by session_id."""
        from src.app.modules.ai.service import ChatMessageService

        with patch("src.app.modules.ai.service.crud_chat_messages") as mock_crud:
            mock_crud.get_multi = AsyncMock(return_value={"data": [], "total_count": 0})

            service = ChatMessageService(mock_db)
            await service.get_session_messages(session_id=1, page=1, limit=50)

            call_args = mock_crud.get_multi.call_args
            assert call_args.kwargs["session_id"] == 1


class TestAISettingsService:
    """Test AI settings service operations."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_get_settings_queries_by_user_id(self, mock_db):
        """Test that get_settings queries by user_id."""
        from src.app.modules.ai.service import AISettingsService

        with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
            mock_crud.get = AsyncMock(return_value={"id": 1, "user_id": 1, "model": "gemma3"})

            service = AISettingsService(mock_db)
            await service.get_settings(user_id=1)

            mock_crud.get.assert_called_once_with(db=mock_db, user_id=1)

    @pytest.mark.asyncio
    async def test_get_or_create_settings_creates_defaults_when_none(self, mock_db):
        """Test that get_or_create_settings creates default settings if none exist."""
        from src.app.modules.ai.service import AISettingsService

        with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
            mock_crud.get = AsyncMock(return_value=None)
            mock_crud.create = AsyncMock(return_value={"id": 1, "user_id": 1, "model": "gemma3"})

            service = AISettingsService(mock_db)
            await service.get_or_create_settings(user_id=1)

            mock_crud.create.assert_called_once()
            call_args = mock_crud.create.call_args
            create_obj = call_args.kwargs.get("object", {})
            # The service passes a dict (not a schema), so access as dict
            if hasattr(create_obj, "user_id"):
                # It's a Pydantic model
                assert create_obj.user_id == 1
                assert create_obj.model == "gemma3"
                assert create_obj.temperature == 0.7
                assert create_obj.max_tokens == 2048
            else:
                # It's a dict
                assert create_obj["user_id"] == 1
                assert create_obj["model"] == "gemma3"
                assert create_obj["temperature"] == 0.7
                assert create_obj["max_tokens"] == 2048

    @pytest.mark.asyncio
    async def test_get_or_create_settings_returns_existing(self, mock_db):
        """Test that get_or_create_settings returns existing settings."""
        from src.app.modules.ai.service import AISettingsService

        existing_settings = {"id": 1, "user_id": 1, "model": "llama3", "temperature": 0.5}

        with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
            mock_crud.get = AsyncMock(return_value=existing_settings)

            service = AISettingsService(mock_db)
            result = await service.get_or_create_settings(user_id=1)

            mock_crud.create.assert_not_called()
            assert result == existing_settings

    @pytest.mark.asyncio
    async def test_update_settings_creates_if_none_exist(self, mock_db):
        """Test that update_settings creates settings if none exist."""
        from src.app.modules.ai.schemas import AISettingsUpdate
        from src.app.modules.ai.service import AISettingsService

        with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
            mock_crud.get = AsyncMock(return_value=None)
            mock_crud.create = AsyncMock(return_value={"id": 1, "user_id": 1})

            service = AISettingsService(mock_db)
            data = AISettingsUpdate(model="llama3")
            await service.update_settings(user_id=1, data=data)

            mock_crud.create.assert_called_once()
            call_args = mock_crud.create.call_args
            create_obj = call_args.kwargs.get("object", {})
            if hasattr(create_obj, "user_id"):
                assert create_obj.user_id == 1
                assert create_obj.model == "llama3"
            else:
                assert create_obj["user_id"] == 1
                assert create_obj["model"] == "llama3"

    @pytest.mark.asyncio
    async def test_update_settings_updates_existing(self, mock_db):
        """Test that update_settings updates existing settings."""
        from src.app.modules.ai.schemas import AISettingsUpdate
        from src.app.modules.ai.service import AISettingsService

        with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
            mock_crud.get = AsyncMock(
                side_effect=[
                    {"id": 1, "user_id": 1, "model": "gemma3"},  # First call
                    {"id": 1, "user_id": 1, "model": "llama3"},  # After update
                ]
            )
            mock_crud.update = AsyncMock()

            service = AISettingsService(mock_db)
            data = AISettingsUpdate(model="llama3")
            await service.update_settings(user_id=1, data=data)

            mock_crud.update.assert_called_once()
            call_args = mock_crud.update.call_args
            assert call_args.kwargs["object"]["model"] == "llama3"


class TestChatCompletionService:
    """Test chat completion service with Ollama integration."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def mock_ollama_client(self):
        """Create a mock Ollama client."""
        client = MagicMock()
        client.chat = AsyncMock(
            return_value={
                "message": "Hello! How can I help you?",
                "model": "gemma3",
                "token_count": 25,
                "finish_reason": "stop",
            }
        )
        return client

    @pytest.mark.asyncio
    async def test_complete_calls_ollama_with_history(self, mock_db, mock_ollama_client):
        """Test that complete passes message history to Ollama."""
        from src.app.modules.ai.schemas import ChatCompletionRequest
        from src.app.modules.ai.service import ChatCompletionService

        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_sessions:
            with patch("src.app.modules.ai.service.crud_chat_messages") as mock_messages:
                with patch("src.app.modules.ai.service.crud_ai_settings") as mock_settings:
                    # Setup mocks
                    mock_sessions.get = AsyncMock(return_value={"id": 1, "user_id": 1, "model": "gemma3"})
                    mock_sessions.update = AsyncMock()
                    mock_messages.get_multi = AsyncMock(
                        return_value={
                            "data": [{"role": "user", "content": "Hi"}],
                            "total_count": 1,
                        }
                    )
                    mock_messages.create = AsyncMock(return_value={"id": 1})
                    mock_settings.get = AsyncMock(
                        return_value={
                            "id": 1,
                            "user_id": 1,
                            "model": "gemma3",
                            "temperature": 0.7,
                            "max_tokens": 2048,
                            "system_prompt": None,
                        }
                    )

                    service = ChatCompletionService(mock_db, mock_ollama_client)
                    request = ChatCompletionRequest(message="Hello")
                    result = await service.complete(session_id=1, user_id=1, request=request)

                    # Verify Ollama was called
                    mock_ollama_client.chat.assert_called_once()
                    call_args = mock_ollama_client.chat.call_args

                    # Check messages include history + new message
                    messages = call_args.kwargs["messages"]
                    assert len(messages) == 2  # Previous + new
                    assert messages[-1]["content"] == "Hello"

                    # Check response
                    assert result["message"] == "Hello! How can I help you?"
                    assert result["model"] == "gemma3"

    @pytest.mark.asyncio
    async def test_complete_saves_user_and_assistant_messages(self, mock_db, mock_ollama_client):
        """Test that complete saves both user and assistant messages."""
        from src.app.modules.ai.schemas import ChatCompletionRequest
        from src.app.modules.ai.service import ChatCompletionService

        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_sessions:
            with patch("src.app.modules.ai.service.crud_chat_messages") as mock_messages:
                with patch("src.app.modules.ai.service.crud_ai_settings") as mock_settings:
                    # Setup mocks
                    mock_sessions.get = AsyncMock(return_value={"id": 1, "user_id": 1, "model": "gemma3"})
                    mock_sessions.update = AsyncMock()
                    mock_messages.get_multi = AsyncMock(
                        return_value={
                            "data": [],
                            "total_count": 0,
                        }
                    )
                    mock_messages.create = AsyncMock(return_value={"id": 1})
                    mock_settings.get = AsyncMock(
                        return_value={
                            "id": 1,
                            "user_id": 1,
                            "model": "gemma3",
                            "temperature": 0.7,
                            "max_tokens": 2048,
                            "system_prompt": None,
                        }
                    )

                    service = ChatCompletionService(mock_db, mock_ollama_client)
                    request = ChatCompletionRequest(message="Hello")
                    await service.complete(session_id=1, user_id=1, request=request)

                    # Verify two messages were saved (user + assistant)
                    assert mock_messages.create.call_count == 2

                    # Check first call was user message (using dict from model_dump)
                    first_call = mock_messages.create.call_args_list[0]
                    first_obj = first_call.kwargs["object"]
                    if isinstance(first_obj, dict):
                        assert first_obj["role"] == "user"
                        assert first_obj["content"] == "Hello"
                    else:
                        assert first_obj.role == "user"
                        assert first_obj.content == "Hello"

                    # Check second call was assistant message
                    second_call = mock_messages.create.call_args_list[1]
                    second_obj = second_call.kwargs["object"]
                    if isinstance(second_obj, dict):
                        assert second_obj["role"] == "assistant"
                    else:
                        assert second_obj.role == "assistant"

    @pytest.mark.asyncio
    async def test_complete_uses_request_overrides(self, mock_db, mock_ollama_client):
        """Test that request parameters override defaults."""
        from src.app.modules.ai.schemas import ChatCompletionRequest
        from src.app.modules.ai.service import ChatCompletionService

        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_sessions:
            with patch("src.app.modules.ai.service.crud_chat_messages") as mock_messages:
                with patch("src.app.modules.ai.service.crud_ai_settings") as mock_settings:
                    # Setup mocks
                    mock_sessions.get = AsyncMock(return_value={"id": 1, "user_id": 1, "model": "gemma3"})
                    mock_sessions.update = AsyncMock()
                    mock_messages.get_multi = AsyncMock(
                        return_value={
                            "data": [],
                            "total_count": 0,
                        }
                    )
                    mock_messages.create = AsyncMock(return_value={"id": 1})
                    mock_settings.get = AsyncMock(
                        return_value={
                            "id": 1,
                            "user_id": 1,
                            "model": "gemma3",
                            "temperature": 0.7,
                            "max_tokens": 2048,
                            "system_prompt": None,
                        }
                    )

                    service = ChatCompletionService(mock_db, mock_ollama_client)
                    request = ChatCompletionRequest(
                        message="Hello",
                        model="llama3",
                        temperature=0.5,
                        max_tokens=1000,
                    )
                    await service.complete(session_id=1, user_id=1, request=request)

                    # Verify Ollama was called with overrides
                    call_args = mock_ollama_client.chat.call_args
                    assert call_args.kwargs["model"] == "llama3"
                    assert call_args.kwargs["temperature"] == 0.5
                    assert call_args.kwargs["max_tokens"] == 1000

    @pytest.mark.asyncio
    async def test_complete_raises_on_ollama_error(self, mock_db):
        """Test that Ollama errors are wrapped in ChatCompletionError."""
        from src.app.modules.ai.ollama_client import OllamaError
        from src.app.modules.ai.schemas import ChatCompletionRequest
        from src.app.modules.ai.service import ChatCompletionError, ChatCompletionService

        mock_ollama = MagicMock()
        mock_ollama.chat = AsyncMock(side_effect=OllamaError("Connection refused", "connection_error"))

        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_sessions:
            with patch("src.app.modules.ai.service.crud_chat_messages") as mock_messages:
                with patch("src.app.modules.ai.service.crud_ai_settings") as mock_settings:
                    mock_sessions.get = AsyncMock(return_value={"id": 1, "user_id": 1, "model": "gemma3"})
                    mock_messages.get_multi = AsyncMock(
                        return_value={
                            "data": [],
                            "total_count": 0,
                        }
                    )
                    mock_messages.create = AsyncMock(return_value={"id": 1})
                    mock_settings.get = AsyncMock(
                        return_value={
                            "id": 1,
                            "user_id": 1,
                            "model": "gemma3",
                            "temperature": 0.7,
                            "max_tokens": 2048,
                            "system_prompt": None,
                        }
                    )

                    service = ChatCompletionService(mock_db, mock_ollama)
                    request = ChatCompletionRequest(message="Hello")

                    with pytest.raises(ChatCompletionError) as exc_info:
                        await service.complete(session_id=1, user_id=1, request=request)

                    assert exc_info.value.error_code == "connection_error"
                    assert exc_info.value.model == "gemma3"
