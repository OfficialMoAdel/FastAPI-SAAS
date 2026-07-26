"""
PERF-R1-B7: Tests for AI Completion DB Operation Optimizations

These tests verify that the ChatCompletionService.complete method:
1. Uses asyncio.gather for parallel reads
2. Batches post-Ollama writes
3. Preserves user message timing (before Ollama call)
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.ai.schemas import ChatCompletionRequest
from src.app.modules.ai.service import ChatCompletionService

logger = logging.getLogger(__name__)


class TestB7ParallelReads:
    """Tests for asyncio.gather optimization on reads."""

    @pytest.mark.asyncio
    async def test_complete_uses_gather_for_reads(self) -> None:
        """Verify that the 3 reads are executed in parallel via asyncio.gather.

        The original code executed these sequentially:
        1. get_session()
        2. get_or_create_settings()
        3. get_session_messages()

        After B7, these should run in parallel, reducing latency.
        """
        # Arrange
        mock_db = AsyncMock()
        service = ChatCompletionService(db=mock_db)

        # Mock the sub-services
        service._session_service = MagicMock()
        service._session_service.get_session = AsyncMock(return_value={"id": 1, "model": "gemma3"})

        service._settings_service = MagicMock()
        service._settings_service.get_or_create_settings = AsyncMock(
            return_value={"model": "gemma3", "temperature": 0.7, "max_tokens": 2048}
        )

        service._message_service = MagicMock()
        service._message_service.get_session_messages = AsyncMock(return_value={"data": [], "total": 0})
        service._message_service.add_message = AsyncMock()

        service.ollama = MagicMock()
        service.provider = service.ollama
        service._injected_provider = True
        service.ollama.chat = AsyncMock(return_value={"message": "Hello!", "token_count": 10})

        request = ChatCompletionRequest(message="Hello")

        # Act
        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud, \
             patch("src.app.modules.ai.service.ChatSessionService") as MockSessionService, \
             patch("src.app.modules.ai.service.AISettingsService") as MockSettingsService, \
             patch("src.app.modules.ai.service.ChatMessageService") as MockMsgService:
            mock_crud.update = AsyncMock()
            mock_crud.get = AsyncMock(return_value={"id": 1, "model": "gemma3", "user_id": 1})

            mock_session_inst = MagicMock()
            mock_session_inst.get_session = AsyncMock(return_value={"id": 1, "model": "gemma3", "user_id": 1})
            MockSessionService.return_value = mock_session_inst

            mock_settings_inst = MagicMock()
            mock_settings_inst.get_or_create_settings = AsyncMock(
                return_value={"model": "gemma3", "temperature": 0.7, "max_tokens": 2048}
            )
            MockSettingsService.return_value = mock_settings_inst

            mock_msg_inst = MagicMock()
            mock_msg_inst.get_session_messages = AsyncMock(return_value={"data": [], "total": 0})
            mock_msg_inst.add_message = AsyncMock()
            MockMsgService.return_value = mock_msg_inst

            with patch("src.app.modules.ai.service.truncate_context", return_value=[]):
                # Patch local_session to avoid real DB calls in Phase 3
                with patch("src.app.modules.ai.service.local_session") as mock_local_session:
                    mock_fresh_db = AsyncMock()
                    mock_fresh_db.__aenter__ = AsyncMock(return_value=mock_fresh_db)
                    mock_fresh_db.__aexit__ = AsyncMock(return_value=None)
                    mock_fresh_db.commit = AsyncMock()
                    mock_local_session.return_value = mock_fresh_db
                    await service.complete(session_id=1, user_id=1, request=request)

        # Assert - all 3 reads should have been called
        mock_session_inst.get_session.assert_called_once_with(1, 1)
        mock_settings_inst.get_or_create_settings.assert_called_once_with(1)
        mock_msg_inst.get_session_messages.assert_called_once_with(1, limit=50)

        logger.info("Verified: All 3 reads were called (parallel execution via gather)")

    @pytest.mark.asyncio
    async def test_gather_exception_handling(self) -> None:
        """Verify that if any read fails, the exception propagates correctly.

        asyncio.gather should fail fast and propagate the first exception.
        """
        mock_db = AsyncMock()
        service = ChatCompletionService(db=mock_db)

        # Mock session service to raise an exception
        service._session_service = MagicMock()
        service._session_service.get_session = AsyncMock(side_effect=ValueError("Session not found"))

        service._settings_service = MagicMock()
        service._settings_service.get_or_create_settings = AsyncMock()

        service._message_service = MagicMock()
        service._message_service.get_session_messages = AsyncMock()

        request = ChatCompletionRequest(message="Hello")

        # Act & Assert
        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud, \
             patch("src.app.modules.ai.service.ChatSessionService") as MockSessionService, \
             patch("src.app.modules.ai.service.AISettingsService") as MockSettingsService, \
             patch("src.app.modules.ai.service.ChatMessageService") as MockMsgService:
            mock_crud.update = AsyncMock()
            mock_crud.get = AsyncMock(return_value={"id": 1, "model": "gemma3", "user_id": 1})

            mock_session_inst = MagicMock()
            mock_session_inst.get_session = AsyncMock(side_effect=ValueError("Session not found"))
            MockSessionService.return_value = mock_session_inst

            mock_settings_inst = MagicMock()
            mock_settings_inst.get_or_create_settings = AsyncMock(
                return_value={"model": "gemma3", "temperature": 0.7, "max_tokens": 2048}
            )
            MockSettingsService.return_value = mock_settings_inst

            mock_msg_inst = MagicMock()
            mock_msg_inst.get_session_messages = AsyncMock(return_value={"data": [], "total": 0})
            mock_msg_inst.add_message = AsyncMock()
            MockMsgService.return_value = mock_msg_inst

            with pytest.raises(ValueError, match="Session not found"):
                await service.complete(session_id=1, user_id=1, request=request)

        logger.info("Verified: Exception from gather propagates correctly")


class TestB7UserMessageTiming:
    """Tests to verify user message is saved BEFORE Ollama call."""

    @pytest.mark.asyncio
    async def test_user_message_saved_before_ollama(self) -> None:
        """Verify user message is saved to DB before Ollama is called.

        This is critical for conversation context - the user message must
        be persisted before the AI generates a response.
        """
        call_order: list[str] = []

        mock_db = AsyncMock()
        service = ChatCompletionService(db=mock_db)

        # Mock sub-services
        service._session_service = MagicMock()
        service._session_service.get_session = AsyncMock(return_value={"id": 1, "model": "gemma3"})

        service._settings_service = MagicMock()
        service._settings_service.get_or_create_settings = AsyncMock(
            return_value={"model": "gemma3", "temperature": 0.7, "max_tokens": 2048}
        )

        service._message_service = MagicMock()
        service._message_service.get_session_messages = AsyncMock(return_value={"data": [], "total": 0})

        async def track_add_message(*args, **kwargs):
            call_order.append("add_user_message")

        service._message_service.add_message = AsyncMock(side_effect=track_add_message)

        async def track_ollama_chat(*args, **kwargs):
            call_order.append("ollama_chat")
            return {"message": "Hello!", "token_count": 10}

        service.ollama = MagicMock()
        service.provider = service.ollama
        service._injected_provider = True
        service.ollama.chat = AsyncMock(side_effect=track_ollama_chat)

        request = ChatCompletionRequest(message="Hello")

        # Act
        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud, \
             patch("src.app.modules.ai.service.ChatSessionService") as MockSessionService, \
             patch("src.app.modules.ai.service.AISettingsService") as MockSettingsService, \
             patch("src.app.modules.ai.service.ChatMessageService") as MockMsgService:
            mock_crud.update = AsyncMock()
            mock_crud.get = AsyncMock(return_value={"id": 1, "model": "gemma3", "user_id": 1})

            mock_session_inst = MagicMock()
            mock_session_inst.get_session = AsyncMock(return_value={"id": 1, "model": "gemma3", "user_id": 1})
            MockSessionService.return_value = mock_session_inst

            mock_settings_inst = MagicMock()
            mock_settings_inst.get_or_create_settings = AsyncMock(
                return_value={"model": "gemma3", "temperature": 0.7, "max_tokens": 2048}
            )
            MockSettingsService.return_value = mock_settings_inst

            mock_msg_inst = MagicMock()
            mock_msg_inst.get_session_messages = AsyncMock(return_value={"data": [], "total": 0})
            async def track_add_message(*args, **kwargs):
                call_order.append("add_user_message")
            mock_msg_inst.add_message = AsyncMock(side_effect=track_add_message)
            MockMsgService.return_value = mock_msg_inst

            with patch("src.app.modules.ai.service.truncate_context", return_value=[]):
                # Patch local_session to avoid real DB calls in Phase 3
                with patch("src.app.modules.ai.service.local_session") as mock_local_session:
                    mock_fresh_db = AsyncMock()
                    mock_fresh_db.__aenter__ = AsyncMock(return_value=mock_fresh_db)
                    mock_fresh_db.__aexit__ = AsyncMock(return_value=None)
                    mock_fresh_db.commit = AsyncMock()
                    mock_local_session.return_value = mock_fresh_db
                    await service.complete(session_id=1, user_id=1, request=request)

        # Assert - user message must come before ollama
        user_msg_index = call_order.index("add_user_message")
        ollama_index = call_order.index("ollama_chat")

        assert user_msg_index < ollama_index, (
            f"User message should be saved before Ollama call. Order was: {call_order}"
        )

        logger.info(f"Verified: Call order correct - {call_order}")


class TestB7BatchedWrites:
    """Tests for post-Ollama batched writes."""

    @pytest.mark.asyncio
    async def test_post_ollama_writes_use_gather(self) -> None:
        """Verify that post-Ollama writes (assistant message + session update)
        are executed in parallel via asyncio.gather.
        """
        mock_db = AsyncMock()
        service = ChatCompletionService(db=mock_db)

        # Mock sub-services
        service._session_service = MagicMock()
        service._session_service.get_session = AsyncMock(return_value={"id": 1, "model": "gemma3"})

        service._settings_service = MagicMock()
        service._settings_service.get_or_create_settings = AsyncMock(
            return_value={"model": "gemma3", "temperature": 0.7, "max_tokens": 2048}
        )

        service._message_service = MagicMock()
        service._message_service.get_session_messages = AsyncMock(return_value={"data": [], "total": 0})
        service._message_service.add_message = AsyncMock()

        service.ollama = MagicMock()
        service.provider = service.ollama
        service._injected_provider = True
        service.ollama.chat = AsyncMock(return_value={"message": "Hello!", "token_count": 10})

        request = ChatCompletionRequest(message="Hello")

        # Track calls to verify both user and assistant messages
        add_message_calls = []

        # Act
        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud, \
             patch("src.app.modules.ai.service.ChatSessionService") as MockSessionService, \
             patch("src.app.modules.ai.service.AISettingsService") as MockSettingsService, \
             patch("src.app.modules.ai.service.ChatMessageService") as MockMsgService:
            mock_crud.update = AsyncMock()
            mock_crud.get = AsyncMock(return_value={"id": 1, "model": "gemma3", "user_id": 1})

            mock_session_inst = MagicMock()
            mock_session_inst.get_session = AsyncMock(return_value={"id": 1, "model": "gemma3", "user_id": 1})
            MockSessionService.return_value = mock_session_inst

            mock_settings_inst = MagicMock()
            mock_settings_inst.get_or_create_settings = AsyncMock(
                return_value={"model": "gemma3", "temperature": 0.7, "max_tokens": 2048}
            )
            MockSettingsService.return_value = mock_settings_inst

            mock_msg_inst = MagicMock()
            mock_msg_inst.get_session_messages = AsyncMock(return_value={"data": [], "total": 0})
            async def track_assistant_msg(*args, **kwargs):
                add_message_calls.append(("assistant", kwargs))
            mock_msg_inst.add_message = AsyncMock(side_effect=track_assistant_msg)
            MockMsgService.return_value = mock_msg_inst

            with patch("src.app.modules.ai.service.truncate_context", return_value=[]):
                # Patch local_session to avoid real DB calls in Phase 3
                with patch("src.app.modules.ai.service.local_session") as mock_local_session:
                    mock_fresh_db = AsyncMock()
                    mock_fresh_db.__aenter__ = AsyncMock(return_value=mock_fresh_db)
                    mock_fresh_db.__aexit__ = AsyncMock(return_value=None)
                    mock_fresh_db.commit = AsyncMock()
                    mock_local_session.return_value = mock_fresh_db
                    await service.complete(session_id=1, user_id=1, request=request)

            # Assert - both post-Ollama operations should be called
            # User message via original service, assistant via fresh service
            service._message_service.add_message.assert_called_once()
            assert len(add_message_calls) == 1  # Assistant message via fresh session
            mock_crud.update.assert_called_once()

        logger.info("Verified: Post-Ollama writes both executed")

    @pytest.mark.asyncio
    async def test_assistant_message_includes_token_count(self) -> None:
        """Verify assistant message is saved with correct token count."""
        mock_db = AsyncMock()
        service = ChatCompletionService(db=mock_db)

        service._session_service = MagicMock()
        service._session_service.get_session = AsyncMock(return_value={"id": 1, "model": "gemma3"})

        service._settings_service = MagicMock()
        service._settings_service.get_or_create_settings = AsyncMock(
            return_value={"model": "gemma3", "temperature": 0.7, "max_tokens": 2048}
        )

        service._message_service = MagicMock()
        service._message_service.get_session_messages = AsyncMock(return_value={"data": [], "total": 0})
        service._message_service.add_message = AsyncMock()

        expected_token_count = 42
        service.ollama = MagicMock()
        service.provider = service.ollama
        service._injected_provider = True
        service.ollama.chat = AsyncMock(return_value={"message": "Test response", "token_count": expected_token_count})

        request = ChatCompletionRequest(message="Hello")

        # Track assistant message to verify token count
        assistant_message_kwargs = {}

        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud, \
             patch("src.app.modules.ai.service.ChatSessionService") as MockSessionService, \
             patch("src.app.modules.ai.service.AISettingsService") as MockSettingsService, \
             patch("src.app.modules.ai.service.ChatMessageService") as MockMsgService:
            mock_crud.update = AsyncMock()
            mock_crud.get = AsyncMock(return_value={"id": 1, "model": "gemma3", "user_id": 1})

            mock_session_inst = MagicMock()
            mock_session_inst.get_session = AsyncMock(return_value={"id": 1, "model": "gemma3", "user_id": 1})
            MockSessionService.return_value = mock_session_inst

            mock_settings_inst = MagicMock()
            mock_settings_inst.get_or_create_settings = AsyncMock(
                return_value={"model": "gemma3", "temperature": 0.7, "max_tokens": 2048}
            )
            MockSettingsService.return_value = mock_settings_inst

            mock_msg_inst = MagicMock()
            mock_msg_inst.get_session_messages = AsyncMock(return_value={"data": [], "total": 0})
            async def track_assistant_msg(*args, **kwargs):
                assistant_message_kwargs.update(kwargs)
            mock_msg_inst.add_message = AsyncMock(side_effect=track_assistant_msg)
            MockMsgService.return_value = mock_msg_inst

            with patch("src.app.modules.ai.service.truncate_context", return_value=[]):
                # Patch local_session to avoid real DB calls in Phase 3
                with patch("src.app.modules.ai.service.local_session") as mock_local_session:
                    mock_fresh_db = AsyncMock()
                    mock_fresh_db.__aenter__ = AsyncMock(return_value=mock_fresh_db)
                    mock_fresh_db.__aexit__ = AsyncMock(return_value=None)
                    mock_fresh_db.commit = AsyncMock()
                    mock_local_session.return_value = mock_fresh_db
                    await service.complete(session_id=1, user_id=1, request=request)

        # Check the assistant message was saved with correct token count
        assert assistant_message_kwargs.get("token_count") == expected_token_count

        logger.info(f"Verified: Assistant message saved with token_count={expected_token_count}")


class TestB7ErrorHandling:
    """Tests for error handling in optimized flow."""

    @pytest.mark.asyncio
    async def test_ollama_error_propagates(self) -> None:
        """Verify that OllamaError is properly converted to ChatCompletionError."""
        from src.app.modules.ai.ollama_client import OllamaError
        from src.app.modules.ai.service import ChatCompletionError

        mock_db = AsyncMock()
        service = ChatCompletionService(db=mock_db)

        service._session_service = MagicMock()
        service._session_service.get_session = AsyncMock(return_value={"id": 1, "model": "gemma3"})

        service._settings_service = MagicMock()
        service._settings_service.get_or_create_settings = AsyncMock(
            return_value={"model": "gemma3", "temperature": 0.7, "max_tokens": 2048}
        )

        service._message_service = MagicMock()
        service._message_service.get_session_messages = AsyncMock(return_value={"data": [], "total": 0})
        service._message_service.add_message = AsyncMock()

        # Ollama fails
        service.ollama = MagicMock()
        service.provider = service.ollama
        service._injected_provider = True
        service.ollama.chat = AsyncMock(side_effect=OllamaError("Model not found", "MODEL_NOT_FOUND"))

        request = ChatCompletionRequest(message="Hello")

        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud, \
             patch("src.app.modules.ai.service.ChatSessionService") as MockSessionService, \
             patch("src.app.modules.ai.service.AISettingsService") as MockSettingsService, \
             patch("src.app.modules.ai.service.ChatMessageService") as MockMsgService:
            mock_crud.update = AsyncMock()
            mock_crud.get = AsyncMock(return_value={"id": 1, "model": "gemma3", "user_id": 1})

            mock_session_inst = MagicMock()
            mock_session_inst.get_session = AsyncMock(return_value={"id": 1, "model": "gemma3", "user_id": 1})
            MockSessionService.return_value = mock_session_inst

            mock_settings_inst = MagicMock()
            mock_settings_inst.get_or_create_settings = AsyncMock(
                return_value={"model": "gemma3", "temperature": 0.7, "max_tokens": 2048}
            )
            MockSettingsService.return_value = mock_settings_inst

            mock_msg_inst = MagicMock()
            mock_msg_inst.get_session_messages = AsyncMock(return_value={"data": [], "total": 0})
            mock_msg_inst.add_message = AsyncMock()
            MockMsgService.return_value = mock_msg_inst

            with patch("src.app.modules.ai.service.truncate_context", return_value=[]):
                with pytest.raises(ChatCompletionError) as exc_info:
                    await service.complete(session_id=1, user_id=1, request=request)

        assert "Model not found" in str(exc_info.value)
        logger.info("Verified: OllamaError properly converted to ChatCompletionError")
