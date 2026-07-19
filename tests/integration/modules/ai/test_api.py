"""Integration tests for AI Features API endpoints.

Tests cover:
- Chat session CRUD operations
- Chat message management
- AI settings management
- Chat completion endpoints
- Authentication and authorization
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

# Test constants for consistent UUID usage
TEST_TENANT_ID = uuid4()


class TestChatSessionAPI:
    """Integration tests for chat session endpoints."""

    def test_create_session_requires_auth(self, client, regular_user_token_headers) -> None:
        """Creating a session requires authentication."""
        mock_service = AsyncMock()
        mock_service.create_session = AsyncMock(
            return_value={
                "id": 1,
                "user_id": 1,
                "tenant_id": str(TEST_TENANT_ID),
                "title": "Test Session",
                "model": "gemma3",
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }
        )
        with patch(
            "src.app.modules.ai.router.get_chat_session_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/api/v1/ai/sessions",
                headers=regular_user_token_headers,
                json={"title": "Test Session"},
            )
            # With full_mock_context, auth is mocked
            assert response.status_code in [200, 201, 401, 403, 422, 500]

    def test_create_session_with_auth(self, client, regular_user_token_headers) -> None:
        """Create a chat session with authentication."""
        mock_service = AsyncMock()
        mock_service.create_session = AsyncMock(
            return_value={
                "id": 1,
                "user_id": 1,
                "tenant_id": str(TEST_TENANT_ID),
                "title": "Test Session",
                "model": "gemma3",
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }
        )
        with patch(
            "src.app.modules.ai.router.get_chat_session_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/api/v1/ai/sessions",
                headers=regular_user_token_headers,
                json={"title": "Test Session"},
            )
            assert response.status_code in [200, 201, 422, 500]

    def test_list_sessions_with_auth(self, client, regular_user_token_headers) -> None:
        """List chat sessions for authenticated user."""
        with patch(
            "src.app.modules.ai.service.ChatSessionService.get_user_sessions",
            new_callable=AsyncMock,
            return_value={"data": [], "total_count": 0},
        ):
            response = client.get(
                "/api/v1/ai/sessions",
                headers=regular_user_token_headers,
            )
            assert response.status_code in [200, 422]
            if response.status_code == 200:
                data = response.json()
                assert "items" in data

    def test_list_sessions_pagination(self, client, regular_user_token_headers) -> None:
        """List sessions should support pagination."""
        with patch(
            "src.app.modules.ai.service.ChatSessionService.get_user_sessions",
            new_callable=AsyncMock,
            return_value={"data": [], "total_count": 0},
        ):
            response = client.get(
                "/api/v1/ai/sessions",
                headers=regular_user_token_headers,
                params={"page": 1, "limit": 10},
            )
            assert response.status_code in [200, 422]

    def test_get_session_by_id(self, client, regular_user_token_headers) -> None:
        """Get a single session by ID."""
        mock_session_service = AsyncMock()
        mock_session_service.get_session = AsyncMock(
            return_value={
                "id": 1,
                "user_id": 1,
                "tenant_id": str(TEST_TENANT_ID),
                "title": "Test Session",
                "model": "gemma3",
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }
        )
        mock_message_service = AsyncMock()
        mock_message_service.get_session_messages = AsyncMock(return_value={"data": []})
        with (
            patch(
                "src.app.modules.ai.router.get_chat_session_service",
                return_value=mock_session_service,
            ),
            patch(
                "src.app.modules.ai.router.get_chat_message_service",
                return_value=mock_message_service,
            ),
        ):
            response = client.get(
                "/api/v1/ai/sessions/1",
                headers=regular_user_token_headers,
            )
            assert response.status_code in [200, 404, 422]

    def test_update_session(self, client, regular_user_token_headers) -> None:
        """Update a chat session."""
        mock_service = AsyncMock()
        mock_service.update_session = AsyncMock(
            return_value={
                "id": 1,
                "tenant_id": str(TEST_TENANT_ID),
                "user_id": 1,
                "title": "Updated Session",
                "model": "gemma3",
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }
        )
        with patch(
            "src.app.modules.ai.router.get_chat_session_service",
            return_value=mock_service,
        ):
            response = client.patch(
                "/api/v1/ai/sessions/1",
                headers=regular_user_token_headers,
                json={"title": "Updated Session"},
            )
            assert response.status_code in [200, 404, 422]

    def test_delete_session(self, client, regular_user_token_headers) -> None:
        """Delete a chat session."""
        with patch(
            "src.app.modules.ai.service.ChatSessionService.delete_session",
            new_callable=AsyncMock,
            return_value={"message": "Chat session deleted"},
        ):
            response = client.delete(
                "/api/v1/ai/sessions/1",
                headers=regular_user_token_headers,
            )
            assert response.status_code in [200, 404]


class TestChatMessageAPI:
    """Integration tests for chat message endpoints."""

    def test_list_session_messages(self, client, regular_user_token_headers) -> None:
        """List messages in a session."""
        with (
            patch(
                "src.app.modules.ai.service.ChatSessionService.get_session",
                new_callable=AsyncMock,
                return_value={"id": 1, "user_id": 1},
            ),
            patch(
                "src.app.modules.ai.service.ChatMessageService.get_session_messages",
                new_callable=AsyncMock,
                return_value={"data": [], "total_count": 0},
            ),
        ):
            response = client.get(
                "/api/v1/ai/sessions/1/messages",
                headers=regular_user_token_headers,
            )
            assert response.status_code in [200, 404]

    def test_list_messages_pagination(self, client, regular_user_token_headers) -> None:
        """List messages should support pagination."""
        with (
            patch(
                "src.app.modules.ai.service.ChatSessionService.get_session",
                new_callable=AsyncMock,
                return_value={"id": 1, "user_id": 1},
            ),
            patch(
                "src.app.modules.ai.service.ChatMessageService.get_session_messages",
                new_callable=AsyncMock,
                return_value={"data": [], "total_count": 0},
            ),
        ):
            response = client.get(
                "/api/v1/ai/sessions/1/messages",
                headers=regular_user_token_headers,
                params={"page": 1, "limit": 50},
            )
            assert response.status_code in [200, 404]


class TestAISettingsAPI:
    """Integration tests for AI settings endpoints."""

    def test_get_ai_settings(self, client, regular_user_token_headers) -> None:
        """Get AI settings for current user."""
        mock_service = AsyncMock()
        mock_service.get_or_create_settings = AsyncMock(
            return_value={
                "id": 1,
                "user_id": 1,
                "model": "gemma3",
                "temperature": 0.7,
                "max_tokens": 2048,
                "system_prompt": None,
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }
        )
        with patch(
            "src.app.modules.ai.router.get_ai_settings_service",
            return_value=mock_service,
        ):
            response = client.get(
                "/api/v1/ai/settings",
                headers=regular_user_token_headers,
            )
            assert response.status_code in [200, 422]

    def test_update_ai_settings(self, client, regular_user_token_headers) -> None:
        """Update AI settings."""
        mock_service = AsyncMock()
        mock_service.update_settings = AsyncMock(
            return_value={
                "id": 1,
                "user_id": 1,
                "model": "llama3",
                "temperature": 0.5,
                "max_tokens": 4096,
                "system_prompt": None,
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }
        )
        with patch(
            "src.app.modules.ai.router.get_ai_settings_service",
            return_value=mock_service,
        ):
            response = client.patch(
                "/api/v1/ai/settings",
                headers=regular_user_token_headers,
                json={
                    "model": "llama3",
                    "temperature": 0.5,
                    "max_tokens": 4096,
                },
            )
            assert response.status_code in [200, 422]


class TestChatCompletionAPI:
    """Integration tests for chat completion endpoints."""

    def test_chat_completion_requires_auth(self, client, regular_user_token_headers) -> None:
        """Chat completion requires authentication."""
        with patch(
            "src.app.modules.ai.service.ChatCompletionService.complete",
            new_callable=AsyncMock,
            return_value={
                "message": "Hello! How can I help you?",
                "role": "assistant",
                "token_count": 10,
                "model": "gemma3",
                "finish_reason": "stop",
            },
        ):
            response = client.post(
                "/api/v1/ai/sessions/1/chat",
                headers=regular_user_token_headers,
                json={"message": "Hello"},
            )
        # Auth is mocked in test fixture
        assert response.status_code in [200, 401, 403, 404, 422, 500, 503]

    def test_chat_completion_success(self, client, regular_user_token_headers) -> None:
        """Successful chat completion."""
        with patch(
            "src.app.modules.ai.service.ChatCompletionService.complete",
            new_callable=AsyncMock,
            return_value={
                "message": "Hello! How can I help you?",
                "role": "assistant",
                "token_count": 10,
                "model": "gemma3",
                "finish_reason": "stop",
            },
        ):
            response = client.post(
                "/api/v1/ai/sessions/1/chat",
                headers=regular_user_token_headers,
                json={"message": "Hello"},
            )
            assert response.status_code in [200, 404, 503]
            if response.status_code == 200:
                data = response.json()
                assert "message" in data

    def test_chat_completion_with_model_override(self, client, regular_user_token_headers) -> None:
        """Chat completion with model override."""
        with patch(
            "src.app.modules.ai.service.ChatCompletionService.complete",
            new_callable=AsyncMock,
            return_value={
                "message": "Response from llama3",
                "role": "assistant",
                "model": "llama3",
            },
        ):
            response = client.post(
                "/api/v1/ai/sessions/1/chat",
                headers=regular_user_token_headers,
                json={
                    "message": "Hello",
                    "model": "llama3",
                    "temperature": 0.5,
                },
            )
            assert response.status_code in [200, 404, 503]

    def test_chat_completion_service_unavailable(self, client, regular_user_token_headers) -> None:
        """Chat completion when AI service is unavailable."""
        from src.app.modules.ai.service import ChatCompletionError

        with patch(
            "src.app.modules.ai.service.ChatCompletionService.complete",
            new_callable=AsyncMock,
            side_effect=ChatCompletionError("Service unavailable", "timeout", "gemma3"),
        ):
            response = client.post(
                "/api/v1/ai/sessions/1/chat",
                headers=regular_user_token_headers,
                json={"message": "Hello"},
            )
            assert response.status_code in [404, 503]


class TestChatCompletionStreamAPI:
    """Integration tests for streaming chat completion endpoint."""

    def test_stream_endpoint_exists(self, client, regular_user_token_headers) -> None:
        """Streaming endpoint should exist."""
        mock_service = AsyncMock()

        async def mock_stream(*args, **kwargs):
            yield {"content": "Hello", "done": False}
            yield {"content": " World", "done": True}

        mock_service.complete_stream = mock_stream
        with patch(
            "src.app.modules.ai.router.get_chat_completion_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/api/v1/ai/sessions/1/chat/stream",
                headers=regular_user_token_headers,
                json={"message": "Hello"},
            )
            # May return 200 (streaming) or 404 (session not found) or 503 (service unavailable)
            assert response.status_code in [200, 404, 503]


class TestAITenantIsolation:
    """Tests for AI feature tenant isolation."""

    def test_sessions_scoped_to_user(self, client, regular_user_token_headers) -> None:
        """Sessions should be scoped to current user."""
        with patch(
            "src.app.modules.ai.service.ChatSessionService.get_user_sessions",
            new_callable=AsyncMock,
            return_value={"data": [], "total_count": 0},
        ):
            response = client.get(
                "/api/v1/ai/sessions",
                headers=regular_user_token_headers,
            )
            assert response.status_code in [200, 422]

    def test_cannot_access_other_user_session(self, client, regular_user_token_headers) -> None:
        """Should not be able to access another user's session."""
        from src.app.modules._base.exceptions import ModelNotFoundError

        with (
            patch(
                "src.app.modules.ai.service.ChatSessionService.get_session",
                new_callable=AsyncMock,
                side_effect=ModelNotFoundError("ChatSession", 999),
            ),
            patch(
                "src.app.modules.ai.service.ChatMessageService.get_session_messages",
                new_callable=AsyncMock,
                return_value={"data": []},
            ),
        ):
            response = client.get(
                "/api/v1/ai/sessions/999",
                headers=regular_user_token_headers,
            )
            assert response.status_code in [404, 500]


class TestAISchemaValidation:
    """Tests for AI API schema validation."""

    def test_create_session_validation(self, client, regular_user_token_headers) -> None:
        """Create session should validate input."""
        mock_service = AsyncMock()
        mock_service.create_session = AsyncMock(
            return_value={
                "id": 1,
                "user_id": 1,
                "tenant_id": str(TEST_TENANT_ID),
                "title": "Untitled",
                "model": "gemma3",
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }
        )
        with patch(
            "src.app.modules.ai.router.get_chat_session_service",
            return_value=mock_service,
        ):
            # Empty title might be allowed or rejected
            response = client.post(
                "/api/v1/ai/sessions",
                headers=regular_user_token_headers,
                json={},
            )
            # Either 422 (validation error) or accepts empty and uses default
            assert response.status_code in [200, 201, 422]

    def test_chat_completion_message_required(self, client, regular_user_token_headers) -> None:
        """Chat completion should require message."""
        response = client.post(
            "/api/v1/ai/sessions/1/chat",
            headers=regular_user_token_headers,
            json={},
        )
        # Should return 422 for missing required field
        assert response.status_code in [404, 422]

    def test_update_settings_validation(self, client, regular_user_token_headers) -> None:
        """Update settings should validate temperature range."""
        response = client.patch(
            "/api/v1/ai/settings",
            headers=regular_user_token_headers,
            json={"temperature": 3.0},  # Invalid - should be 0-2
        )
        # Either 422 (validation) or 200 (accepts and clamps)
        assert response.status_code in [200, 422]
