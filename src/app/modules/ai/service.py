"""AI Features service layer.

Business logic for AI chat sessions, messages, and user settings.
"""

import logging
from datetime import UTC, datetime
from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.tenancy.context import get_tenant_context
from src.app.modules._base.exceptions import ModelNotFoundError
from src.app.modules._base.service import BaseService

logger = logging.getLogger(__name__)

from .crud import crud_ai_settings, crud_chat_messages, crud_chat_sessions
from .exceptions import SessionLimitExceededError
from .models import AIChatMessage, AIChatSession, AISettings
from .ollama_client import OllamaClient, OllamaError, get_ollama_client
from .schemas import (
    AISettingsUpdate,
    ChatCompletionRequest,
    ChatMessageCreate,
    ChatSessionCreate,
    ChatSessionUpdate,
)


# T096: Token estimation (4 chars per token)
DEFAULT_CONTEXT_WINDOW = 8192  # Default context window size in tokens

# T134: Maximum concurrent sessions per tenant
MAX_SESSIONS_PER_TENANT = 100


def estimate_tokens(text: str) -> int:
    """Estimate token count using 4 chars per token approximation.

    T096: Implement token estimation (4 chars per token).

    Args:
        text: The text to estimate tokens for

    Returns:
        Estimated token count
    """
    return len(text) // 4 if text else 0


def estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    """Estimate total tokens for a list of messages.

    Args:
        messages: List of message dicts with 'role' and 'content'

    Returns:
        Estimated total token count
    """
    total = 0
    for msg in messages:
        # Count role and content
        total += estimate_tokens(msg.get("role", ""))
        total += estimate_tokens(msg.get("content", ""))
        # Add overhead for message structure (approx 4 tokens per message)
        total += 4
    return total


# T097: Context window truncation (FIFO, preserve system prompt)
def truncate_context(
    messages: list[dict[str, str]],
    max_tokens: int = DEFAULT_CONTEXT_WINDOW,
    system_prompt: str | None = None,
    reserve_for_response: int = 1024,
) -> list[dict[str, str]]:
    """Truncate message history to fit within context window.

    T097: Implement context window truncation (FIFO, preserve system prompt).

    Uses FIFO strategy: removes oldest messages first while preserving
    the system prompt and most recent messages.

    Args:
        messages: List of message dicts with 'role' and 'content'
        max_tokens: Maximum context window size in tokens
        system_prompt: Optional system prompt to preserve (not counted in messages)
        reserve_for_response: Tokens to reserve for model response

    Returns:
        Truncated list of messages that fits within token limit
    """
    if not messages:
        return []

    # Calculate available tokens (subtract system prompt and response reserve)
    available_tokens = max_tokens - reserve_for_response
    if system_prompt:
        available_tokens -= estimate_tokens(system_prompt)
        available_tokens -= 4  # Overhead for system message structure

    if available_tokens <= 0:
        # Not enough room, return only the last message
        return messages[-1:] if messages else []

    # Start from the most recent messages (FIFO removes oldest first)
    truncated = []
    current_tokens = 0

    # Iterate from newest to oldest
    for msg in reversed(messages):
        msg_tokens = estimate_tokens(msg.get("content", ""))
        msg_tokens += estimate_tokens(msg.get("role", ""))
        msg_tokens += 4  # Message structure overhead

        if current_tokens + msg_tokens <= available_tokens:
            truncated.insert(0, msg)  # Insert at beginning to maintain order
            current_tokens += msg_tokens
        else:
            # No more room, stop adding older messages
            break

    return truncated


class ChatSessionService(BaseService[AIChatSession]):
    """Service for managing AI chat sessions."""

    def __init__(self, db: AsyncSession):
        super().__init__(AIChatSession, db)

    async def create_session(
        self, user_id: int, data: ChatSessionCreate
    ) -> dict[str, Any]:
        """Create a new chat session.

        T134: Enforces maximum 100 concurrent sessions per tenant.
        """
        tenant = get_tenant_context()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        # T134: Check session limit before creating
        current_count = await crud_chat_sessions.count(db=self.db, tenant_id=tenant.id)
        if current_count >= MAX_SESSIONS_PER_TENANT:
            raise SessionLimitExceededError(
                tenant_id=tenant.id,
                limit=MAX_SESSIONS_PER_TENANT,
                current_count=current_count,
            )

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
        logger.info(f"Chat completion request for session {session_id} by user {user_id}")
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

        # T097: Truncate context to fit within context window
        history = truncate_context(
            messages=history,
            max_tokens=DEFAULT_CONTEXT_WINDOW,
            system_prompt=system_prompt,
            reserve_for_response=max_tokens,
        )

        # Save user message to database
        await self._message_service.add_message(
            session_id=session_id,
            data=ChatMessageCreate(role="user", content=request.message),
        )

        # Call Ollama
        try:
            logger.debug(f"Calling Ollama with model={model}, temperature={temperature}")
            result = await self.ollama.chat(
                model=model,
                messages=history,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )
            logger.info(f"Chat completion successful, tokens={result.get('token_count')}")

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
            logger.error(f"Chat completion failed: {e.message} ({e.error_code})")
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

        # T097: Truncate context to fit within context window
        history = truncate_context(
            messages=history,
            max_tokens=DEFAULT_CONTEXT_WINDOW,
            system_prompt=system_prompt,
            reserve_for_response=max_tokens,
        )

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
