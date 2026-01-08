"""AI Features service layer.

Business logic for AI chat sessions, messages, and user settings.
"""

from datetime import UTC, datetime
from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.tenancy.context import get_tenant_context
from src.app.modules._base.exceptions import ModelNotFoundError
from src.app.modules._base.service import BaseService

from .crud import crud_ai_settings, crud_chat_messages, crud_chat_sessions
from .models import AIChatMessage, AIChatSession, AISettings
from .ollama_client import OllamaClient, OllamaError, get_ollama_client
from .schemas import (
    AISettingsUpdate,
    ChatCompletionRequest,
    ChatMessageCreate,
    ChatSessionCreate,
    ChatSessionUpdate,
)


class ChatSessionService(BaseService[AIChatSession]):
    """Service for managing AI chat sessions."""

    def __init__(self, db: AsyncSession):
        super().__init__(AIChatSession, db)

    async def create_session(
        self, user_id: int, data: ChatSessionCreate
    ) -> dict[str, Any]:
        """Create a new chat session."""
        tenant = get_tenant_context()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        session_data = data.model_dump()
        session_data["tenant_id"] = tenant.id
        session_data["user_id"] = user_id
        return await crud_chat_sessions.create(db=self.db, object=session_data)

    async def get_session(self, session_id: int, user_id: int) -> dict[str, Any]:
        """Get a chat session by ID with ownership check."""
        session = await crud_chat_sessions.get(db=self.db, id=session_id)
        if session is None:
            raise ModelNotFoundError("ChatSession", session_id)
        if session["user_id"] != user_id:
            raise ModelNotFoundError("ChatSession", session_id)
        return session

    async def get_user_sessions(
        self, user_id: int, page: int = 1, limit: int = 20
    ) -> dict[str, Any]:
        """Get paginated chat sessions for a user."""
        offset = (page - 1) * limit
        return await crud_chat_sessions.get_multi(
            db=self.db, user_id=user_id, offset=offset, limit=limit
        )

    async def update_session(
        self, session_id: int, user_id: int, data: ChatSessionUpdate
    ) -> dict[str, Any]:
        """Update a chat session."""
        session = await self.get_session(session_id, user_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)
        await crud_chat_sessions.update(db=self.db, object=update_data, id=session_id)
        return await crud_chat_sessions.get(db=self.db, id=session_id)

    async def delete_session(self, session_id: int, user_id: int) -> dict[str, str]:
        """Delete a chat session."""
        await self.get_session(session_id, user_id)
        await crud_chat_sessions.db_delete(db=self.db, id=session_id)
        return {"message": "Chat session deleted"}


class ChatMessageService(BaseService[AIChatMessage]):
    """Service for managing chat messages."""

    def __init__(self, db: AsyncSession):
        super().__init__(AIChatMessage, db)

    async def add_message(
        self, session_id: int, data: ChatMessageCreate, token_count: int | None = None
    ) -> dict[str, Any]:
        """Add a message to a chat session."""
        message_data = data.model_dump()
        message_data["session_id"] = session_id
        if token_count is not None:
            message_data["token_count"] = token_count
        return await crud_chat_messages.create(db=self.db, object=message_data)

    async def get_session_messages(
        self, session_id: int, page: int = 1, limit: int = 50
    ) -> dict[str, Any]:
        """Get paginated messages for a session."""
        offset = (page - 1) * limit
        return await crud_chat_messages.get_multi(
            db=self.db, session_id=session_id, offset=offset, limit=limit
        )


class AISettingsService(BaseService[AISettings]):
    """Service for managing user AI settings."""

    def __init__(self, db: AsyncSession):
        super().__init__(AISettings, db)

    async def get_settings(self, user_id: int) -> dict[str, Any] | None:
        """Get AI settings for a user."""
        return await crud_ai_settings.get(db=self.db, user_id=user_id)

    async def get_or_create_settings(self, user_id: int) -> dict[str, Any]:
        """Get AI settings for a user, creating defaults if needed."""
        settings = await crud_ai_settings.get(db=self.db, user_id=user_id)
        if settings is None:
            # Create default settings
            settings = await crud_ai_settings.create(
                db=self.db,
                object={
                    "user_id": user_id,
                    "model": "gemma3",
                    "temperature": 0.7,
                    "max_tokens": 2048,
                    "system_prompt": None,
                },
            )
        return settings

    async def update_settings(
        self, user_id: int, data: AISettingsUpdate
    ) -> dict[str, Any]:
        """Update AI settings for a user."""
        settings = await crud_ai_settings.get(db=self.db, user_id=user_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)

        if settings is None:
            # Create with provided values
            update_data["user_id"] = user_id
            update_data.setdefault("model", "gemma3")
            update_data.setdefault("temperature", 0.7)
            update_data.setdefault("max_tokens", 2048)
            return await crud_ai_settings.create(db=self.db, object=update_data)
        else:
            await crud_ai_settings.update(db=self.db, object=update_data, id=settings["id"])
            return await crud_ai_settings.get(db=self.db, id=settings["id"])


# Dependency injection factories
def get_chat_session_service(db: AsyncSession) -> ChatSessionService:
    """Get ChatSessionService instance."""
    return ChatSessionService(db)


def get_chat_message_service(db: AsyncSession) -> ChatMessageService:
    """Get ChatMessageService instance."""
    return ChatMessageService(db)


def get_ai_settings_service(db: AsyncSession) -> AISettingsService:
    """Get AISettingsService instance."""
    return AISettingsService(db)


class ChatCompletionService:
    """Service for AI chat completions via Ollama."""

    def __init__(
        self,
        db: AsyncSession,
        ollama_client: OllamaClient | None = None,
    ):
        self.db = db
        self.ollama = ollama_client or get_ollama_client()
        self._session_service = ChatSessionService(db)
        self._message_service = ChatMessageService(db)
        self._settings_service = AISettingsService(db)

    async def complete(
        self,
        session_id: int,
        user_id: int,
        request: ChatCompletionRequest,
    ) -> dict[str, Any]:
        """Generate a chat completion for a session.

        Args:
            session_id: The chat session ID
            user_id: The user making the request (for ownership check)
            request: The completion request with message and optional overrides

        Returns:
            Dict with assistant response, model, token_count
        """
        # Verify session ownership
        session = await self._session_service.get_session(session_id, user_id)

        # Get user settings for defaults
        user_settings = await self._settings_service.get_or_create_settings(user_id)

        # Determine model and parameters
        model = request.model or session.get("model") or user_settings.get("model", "gemma3")
        temperature = request.temperature or user_settings.get("temperature", 0.7)
        max_tokens = request.max_tokens or user_settings.get("max_tokens", 2048)
        system_prompt = user_settings.get("system_prompt")

        # Get existing messages for context
        messages_result = await self._message_service.get_session_messages(
            session_id, limit=50
        )
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in messages_result.get("data", [])
        ]

        # Add the new user message
        history.append({"role": "user", "content": request.message})

        # Save user message to database
        await self._message_service.add_message(
            session_id=session_id,
            data=ChatMessageCreate(role="user", content=request.message),
        )

        # Call Ollama
        try:
            result = await self.ollama.chat(
                model=model,
                messages=history,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )

            # Save assistant response to database
            await self._message_service.add_message(
                session_id=session_id,
                data=ChatMessageCreate(role="assistant", content=result["message"]),
                token_count=result.get("token_count"),
            )

            # Update session timestamp
            await crud_chat_sessions.update(
                db=self.db,
                object={"updated_at": datetime.now(UTC)},
                id=session_id,
            )

            return {
                "message": result["message"],
                "role": "assistant",
                "token_count": result.get("token_count"),
                "model": result.get("model", model),
                "finish_reason": result.get("finish_reason"),
            }

        except OllamaError as e:
            raise ChatCompletionError(e.message, e.error_code, model) from e

    async def complete_stream(
        self,
        session_id: int,
        user_id: int,
        request: ChatCompletionRequest,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream a chat completion for a session.

        Yields dicts with 'content', 'done', and optionally 'token_count'.
        """
        # Verify session ownership
        session = await self._session_service.get_session(session_id, user_id)

        # Get user settings for defaults
        user_settings = await self._settings_service.get_or_create_settings(user_id)

        # Determine model and parameters
        model = request.model or session.get("model") or user_settings.get("model", "gemma3")
        temperature = request.temperature or user_settings.get("temperature", 0.7)
        max_tokens = request.max_tokens or user_settings.get("max_tokens", 2048)
        system_prompt = user_settings.get("system_prompt")

        # Get existing messages for context
        messages_result = await self._message_service.get_session_messages(
            session_id, limit=50
        )
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in messages_result.get("data", [])
        ]

        # Add the new user message
        history.append({"role": "user", "content": request.message})

        # Save user message to database
        await self._message_service.add_message(
            session_id=session_id,
            data=ChatMessageCreate(role="user", content=request.message),
        )

        # Stream from Ollama
        full_response = ""
        token_count = None

        try:
            async for chunk in self.ollama.chat_stream(
                model=model,
                messages=history,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            ):
                full_response += chunk.get("content", "")
                if chunk.get("done"):
                    token_count = chunk.get("token_count")
                yield chunk

            # Save complete assistant response to database
            if full_response:
                await self._message_service.add_message(
                    session_id=session_id,
                    data=ChatMessageCreate(role="assistant", content=full_response),
                    token_count=token_count,
                )

                # Update session timestamp
                await crud_chat_sessions.update(
                    db=self.db,
                    object={"updated_at": datetime.now(UTC)},
                    id=session_id,
                )

        except OllamaError as e:
            raise ChatCompletionError(e.message, e.error_code, model) from e


class ChatCompletionError(Exception):
    """Exception raised for chat completion errors."""

    def __init__(self, message: str, error_code: str, model: str | None = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.model = model


def get_chat_completion_service(
    db: AsyncSession,
    ollama_client: OllamaClient | None = None,
) -> ChatCompletionService:
    """Get ChatCompletionService instance."""
    return ChatCompletionService(db, ollama_client)
