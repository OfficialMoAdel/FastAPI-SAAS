"""AI Features service layer.

Business logic for AI chat sessions, messages, and user settings.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.core.logger import get_logger
from src.app.core.tenancy.context import get_tenant_context
from src.app.modules._base.exceptions import ModelNotFoundError
from src.app.modules._base.service import BaseService
from src.app.modules.ai.crud import crud_ai_settings, crud_chat_messages, crud_chat_sessions
from src.app.modules.ai.models import AIChatMessage, AIChatSession, AISettings
from src.app.modules.ai.ollama_client import OllamaClient, OllamaError, get_ollama_client
from src.app.modules.ai.schemas import (
    AISettingsUpdate,
    ChatCompletionRequest,
    ChatMessageCreate,
    ChatSessionCreate,
    ChatSessionUpdate,
)
from src.app.modules.ai.unified_errors import SessionLimitExceededError

logger = get_logger(__name__)

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
    truncated: list[dict[str, str]] = []
    current_tokens = 0

    # Iterate from newest to oldest, append and reverse at end (O(n) vs O(n²) insert(0))
    for msg in reversed(messages):
        msg_tokens = estimate_tokens(msg.get("content", ""))
        msg_tokens += estimate_tokens(msg.get("role", ""))
        msg_tokens += 4  # Message structure overhead

        if current_tokens + msg_tokens <= available_tokens:
            truncated.append(msg)
            current_tokens += msg_tokens
        else:
            # No more room, stop adding older messages
            break

    truncated.reverse()
    return truncated


class ChatSessionService(BaseService[AIChatSession]):
    """Service for managing AI chat sessions."""

    def __init__(self, db: AsyncSession):
        super().__init__(AIChatSession, db)

    async def create_session(self, user_id: int, data: ChatSessionCreate) -> dict[str, Any]:
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
        result = await crud_chat_sessions.create(db=self.db, object=session_data)  # type: ignore
        await self.db.commit()
        return result if isinstance(result, dict) else {"id": getattr(result, "id", None)}

    async def get_session(self, session_id: int, user_id: int) -> dict[str, Any]:
        """Get a chat session by ID with ownership check."""
        session = await crud_chat_sessions.get(db=self.db, id=session_id)
        if session is None:
            raise ModelNotFoundError("ChatSession", session_id)
        if session["user_id"] != user_id:
            raise ModelNotFoundError("ChatSession", session_id)
        return session

    async def get_user_sessions(self, user_id: int, page: int = 1, limit: int = 20) -> dict[str, Any]:
        """Get paginated chat sessions for a user."""
        from src.app.modules.ai.schemas import ChatSessionRead

        offset = (page - 1) * limit
        return await crud_chat_sessions.get_multi(
            db=self.db, user_id=user_id, offset=offset, limit=limit, schema_to_select=ChatSessionRead
        )

    async def update_session(self, session_id: int, user_id: int, data: ChatSessionUpdate) -> dict[str, Any]:
        """Update a chat session."""
        await self.get_session(session_id, user_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)
        await crud_chat_sessions.update(db=self.db, object=update_data, id=session_id)
        await self.db.commit()
        result = await crud_chat_sessions.get(db=self.db, id=session_id)
        if result is None:
            raise ModelNotFoundError("ChatSession", session_id)
        return result

    async def delete_session(self, session_id: int, user_id: int) -> dict[str, str]:
        """Delete a chat session."""
        await self.get_session(session_id, user_id)
        await crud_chat_sessions.db_delete(db=self.db, id=session_id)
        await self.db.commit()
        return {"message": "Chat session deleted"}


from pydantic import BaseModel


class ChatMessageInternal(BaseModel):
    session_id: int
    role: str
    content: str
    token_count: int | None = None

class ChatMessageService(BaseService[AIChatMessage]):
    """Service for managing chat messages."""

    def __init__(self, db: AsyncSession):
        super().__init__(AIChatMessage, db)

    async def add_message(
        self, session_id: int, data: ChatMessageCreate, token_count: int | None = None
    ) -> dict[str, Any]:
        """Add a message to a chat session."""

        # Role is already a string (Literal type)
        internal_data = ChatMessageInternal(
            session_id=session_id,
            role=str(data.role),
            content=data.content,
            token_count=token_count,
        )
        result = await crud_chat_messages.create(db=self.db, object=internal_data.model_dump())  # type: ignore
        await self.db.commit()
        return result if isinstance(result, dict) else {"id": getattr(result, "id", None)}

    async def get_session_messages(self, session_id: int, page: int = 1, limit: int = 50) -> dict[str, Any]:
        """Get paginated messages for a session."""
        from src.app.modules.ai.schemas import ChatMessageRead

        offset = (page - 1) * limit
        return await crud_chat_messages.get_multi(
            db=self.db, session_id=session_id, offset=offset, limit=limit, schema_to_select=ChatMessageRead
        )


class AISettingsService(BaseService[AISettings]):
    """Service for managing user AI settings."""

    def __init__(self, db: AsyncSession):
        super().__init__(AISettings, db)

    async def get_settings(self, user_id: int) -> dict[str, Any] | None:
        """Get AI settings for a user within current tenant."""
        tenant = get_tenant_context()
        tenant_id = tenant.id if tenant else None
        return await crud_ai_settings.get(db=self.db, user_id=user_id, tenant_id=tenant_id)

    async def get_or_create_settings(self, user_id: int) -> dict[str, Any]:
        """Get AI settings for a user, creating defaults if needed."""
        tenant = get_tenant_context()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")
        tenant_id = tenant.id

        settings = await crud_ai_settings.get(db=self.db, user_id=user_id, tenant_id=tenant_id)
        if settings is None:
            # Create default settings - convert to dict and add user_id
            default_settings = AISettingsUpdate(
                model="gemma3",
                temperature=0.7,
                max_tokens=2048,
                system_prompt=None,
            )
            create_data = default_settings.model_dump()
            create_data["user_id"] = user_id
            create_data["tenant_id"] = tenant_id
            result = await crud_ai_settings.create(db=self.db, object=create_data)  # type: ignore
            await self.db.commit()
            settings = result if isinstance(result, dict) else {"id": getattr(result, "id", None)}
            settings["user_id"] = user_id
            settings["tenant_id"] = tenant_id
        return settings

    async def update_settings(self, user_id: int, data: AISettingsUpdate) -> dict[str, Any]:
        """Update AI settings for a user."""
        tenant = get_tenant_context()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")
        tenant_id = tenant.id

        settings = await crud_ai_settings.get(db=self.db, user_id=user_id, tenant_id=tenant_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)

        if settings is None:
            # Create with provided values - convert to dict and add user_id
            create_data_dict = {
                "model": update_data.get("model", "gemma3"),
                "temperature": update_data.get("temperature", 0.7),
                "max_tokens": update_data.get("max_tokens", 2048),
                "system_prompt": update_data.get("system_prompt"),
                "user_id": user_id,
                "tenant_id": tenant_id,
            }
            result = await crud_ai_settings.create(db=self.db, object=create_data_dict)  # type: ignore
            result_dict = result if isinstance(result, dict) else {"id": getattr(result, "id", None)}
            result_dict["user_id"] = user_id
            result_dict["tenant_id"] = tenant_id
            await self.db.commit()
            return result_dict
        else:
            await crud_ai_settings.update(db=self.db, object=update_data, id=settings["id"])
            await self.db.commit()

            # Update the dictionary locally instead of fetching again
            settings.update(update_data)
            return settings


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

        C2: Releases DB connection during LLM call to prevent pool exhaustion.
        Pattern: fetch→commit→LLM→fresh_session→write

        Args:
            session_id: The chat session ID
            user_id: The user making the request (for ownership check)
            request: The completion request with message and optional overrides

        Returns:
            Dict with assistant response, model, token_count
        """
        logger.info("Chat completion request for session by user", session_id=session_id, user_id=user_id)

        # Sequential fetch to avoid SQLAlchemy AsyncSession concurrency issues
        session = await self._session_service.get_session(session_id, user_id)
        user_settings = await self._settings_service.get_or_create_settings(user_id)
        messages_result = await self._message_service.get_session_messages(session_id, limit=50)

        model = request.model or session.get("model") or user_settings.get("model", "gemma3")
        temperature = request.temperature if request.temperature is not None else user_settings.get("temperature", 0.7)
        max_tokens = request.max_tokens if request.max_tokens is not None else user_settings.get("max_tokens", 2048)
        system_prompt = user_settings.get("system_prompt")

        history = []
        for m in messages_result.get("data", []):
            history.append({"role": m["role"], "content": m["content"]})

        sanitized_message = request.message.strip()
        history.append({"role": "user", "content": sanitized_message})

        history = truncate_context(
            messages=history,
            max_tokens=DEFAULT_CONTEXT_WINDOW,
            system_prompt=system_prompt,
            reserve_for_response=max_tokens,
        )

        # Save user message before releasing connection
        await self._message_service.add_message(
            session_id=session_id,
            data=ChatMessageCreate(role="user", content=request.message),
        )

        # C2: Commit and release DB connection before LLM call
        await self.db.commit()

        # Phase 2: Call LLM (connection released, no DB hold during inference)
        try:
            logger.debug("Calling Ollama with model=, temperature=", model=model, temperature=temperature)
            result = await self.ollama.chat(
                model=model,
                messages=history,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )
            logger.info("Chat completion successful, tokens=", val=result.get("token_count"))

        except OllamaError as e:
            logger.error("Chat completion failed: ()", message=e.message, error_code=e.error_code)
            raise ChatCompletionError(e.message, e.error_code, model) from e

        # Phase 3: Get fresh session for writing response
        async with local_session() as fresh_db:
            message_service = ChatMessageService(fresh_db)
            await message_service.add_message(
                session_id=session_id,
                data=ChatMessageCreate(role="assistant", content=result["message"]),
                token_count=result.get("token_count"),
            )

            await crud_chat_sessions.update(
                db=fresh_db,
                object={"updated_at": datetime.now(UTC)},
                id=session_id,
            )
            await fresh_db.commit()

        return {
            "message": result["message"],
            "role": "assistant",
            "token_count": result.get("token_count"),
            "model": result.get("model", model),
            "finish_reason": result.get("finish_reason"),
        }

    async def complete_stream(
        self,
        session_id: int,
        user_id: int,
        request: ChatCompletionRequest,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream a chat completion for a session.

        C2: Releases DB connection during LLM streaming to prevent pool exhaustion.
        P5: Uses asyncio.gather() for parallel data fetching.

        Yields dicts with 'content', 'done', and optionally 'token_count'.
        """
        # Sequential fetch to avoid SQLAlchemy AsyncSession concurrency issues
        session = await self._session_service.get_session(session_id, user_id)
        user_settings = await self._settings_service.get_or_create_settings(user_id)
        messages_result = await self._message_service.get_session_messages(session_id, limit=50)

        model = request.model or session.get("model") or user_settings.get("model", "gemma3")
        temperature = request.temperature if request.temperature is not None else user_settings.get("temperature", 0.7)
        max_tokens = request.max_tokens if request.max_tokens is not None else user_settings.get("max_tokens", 2048)
        system_prompt = user_settings.get("system_prompt")

        history = []
        for m in messages_result.get("data", []):
            history.append({"role": m["role"], "content": m["content"]})

        sanitized_message = request.message.strip()
        history.append({"role": "user", "content": sanitized_message})

        history = truncate_context(
            messages=history,
            max_tokens=DEFAULT_CONTEXT_WINDOW,
            system_prompt=system_prompt,
            reserve_for_response=max_tokens,
        )

        # Save user message before releasing connection
        await self._message_service.add_message(
            session_id=session_id,
            data=ChatMessageCreate(role="user", content=request.message),
        )

        # C2: Commit and release DB connection before LLM streaming
        await self.db.commit()

        # Phase 2: Stream from Ollama (connection released)
        # Use list accumulation to avoid O(n²) string concatenation
        response_chunks = []
        token_count = None
        stream_completed = False

        from src.app.core.tenancy.context import get_tenant_context
        current_tenant = get_tenant_context()

        try:
            async for chunk in self.ollama.chat_stream(
                model=model,
                messages=history,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            ):
                content = chunk.get("content", "")
                if content:
                    response_chunks.append(content)
                if chunk.get("done"):
                    stream_completed = True
                    token_count = chunk.get("token_count")
                yield chunk

        except OllamaError as e:
            raise ChatCompletionError(e.message, e.error_code, model) from e

        finally:
            # Phase 3: Get fresh session for writing response safely in the background
            full_response = "".join(response_chunks)
            if full_response and stream_completed:

                async def _save_response(resp_content: str, resp_tokens: int | None, tenant_ctx: Any) -> None:
                    import asyncio

                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            if tenant_ctx:
                                from src.app.core.tenancy.context import set_tenant_context
                                set_tenant_context(tenant_ctx)
                            async with local_session() as fresh_db:
                                message_service = ChatMessageService(fresh_db)
                                await message_service.add_message(
                                    session_id=session_id,
                                    data=ChatMessageCreate(role="assistant", content=resp_content),
                                    token_count=resp_tokens,
                                )
                                await crud_chat_sessions.update(
                                    db=fresh_db,
                                    object={"updated_at": datetime.now(UTC)},
                                    id=session_id,
                                )
                                await fresh_db.commit()
                            break
                        except Exception as e:
                            if attempt < max_retries - 1:
                                logger.warning(
                                    "Failed to save AI chat response in background task, retrying",
                                    attempt=attempt + 1,
                                    max_retries=max_retries,
                                    error=str(e),
                                )
                                import random

                                base_delay = 2 * (2**attempt)  # 2, 4, 8 seconds
                                jitter = random.uniform(0, 1)
                                await asyncio.sleep(base_delay + jitter)
                            else:
                                logger.error(
                                    "Failed to save AI chat response in background task after all retries", error=str(e)
                                )

                from src.app.core.utils.background_task import create_safe_task

                create_safe_task(_save_response(full_response, token_count, current_tenant))


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
