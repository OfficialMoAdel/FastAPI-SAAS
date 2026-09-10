"""AI Features service layer.

Business logic for AI chat sessions, messages, and user settings.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.core.logger import get_logger
from src.app.core.tenancy.context import get_current_tenant
from src.app.modules._base.service import BaseService
from src.app.modules.ai.crud import crud_ai_settings, crud_chat_messages, crud_chat_sessions
from src.app.modules.ai.models import AIChatMessage, AIChatSession, AISettings
from src.app.modules.ai.providers import AIProvider, AIProviderError
from src.app.modules.ai.schemas import (
    AISettingsUpdate,
    ChatCompletionRequest,
    ChatMessageCreate,
    ChatSessionCreate,
    ChatSessionUpdate,
)
from src.app.modules.ai.unified_errors import SessionLimitExceededError
from src.app.shared.exceptions import ModelNotFoundError

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
        tenant = get_current_tenant()
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
        tenant = get_current_tenant()
        tenant_id = tenant.id if tenant else None
        return await crud_ai_settings.get(db=self.db, user_id=user_id, tenant_id=tenant_id)

    async def get_or_create_settings(self, user_id: int) -> dict[str, Any]:
        """Get AI settings for a user, creating defaults if needed."""
        tenant = get_current_tenant()
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
        tenant = get_current_tenant()
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


class ChatCompletionService:
    """Service for AI chat completions via AIProvider."""

    def __init__(
        self,
        db: AsyncSession,
        provider: AIProvider | None = None,
        fallback_provider: AIProvider | None = None,
    ):
        self.db = db
        if provider is not None:
            self.provider = provider
            self._injected_provider = True
        else:
            from src.app.modules.ai.factory import AIProviderFactory

            self.provider = AIProviderFactory.get_provider()
            self._injected_provider = False
        self._fallback_provider = fallback_provider
        self._session_service = ChatSessionService(db)
        self._message_service = ChatMessageService(db)
        self._settings_service = AISettingsService(db)

    async def _get_fallback_provider(self) -> Any | None:
        """Resolve the fallback provider, probing candidates in parallel (P-M10).

        Previously each candidate was constructed sequentially with
        try/except-continue, so a misconfigured provider list paid every
        failure serially before finding a working one. Now:

        1. All non-primary candidates are CONSTRUCTED concurrently —
           construction is a sync factory call, so each runs in a worker
           thread via ``asyncio.to_thread``; it fails fast for missing
           credentials, which makes it the probe.
        2. Constructed providers are HEALTH-CHECKED concurrently; the
           results are used only for ordering: healthy candidates come
           first, preserving factory registration order within each group.
        3. First-success wins: the first healthy provider is returned; if
           none pass their health check, the first constructed provider is
           returned (health is an ordering hint, not a gate — an at-the-moment
           failed probe may still serve the next request).
        """
        if self._fallback_provider is not None:
            return self._fallback_provider
        if self._injected_provider:
            return None
        from src.app.modules.ai.factory import SUPPORTED_PROVIDERS, AIProviderFactory

        current_name = getattr(self.provider, "provider_name", None)
        candidates = [name for name in SUPPORTED_PROVIDERS if name != current_name]
        if not candidates:
            return None

        # 1. Parallel construction probes (sync factory calls in threads;
        #    failures — e.g. missing API keys — surface as exceptions).
        constructed: list[Any] = []
        try:
            results = await asyncio.gather(
                *(asyncio.to_thread(AIProviderFactory.get_provider, provider_name=name) for name in candidates),
                return_exceptions=True,
            )
            constructed = [p for p in results if not isinstance(p, BaseException)]
        except Exception:
            return None
        if not constructed:
            return None

        # 2. Health-aware ordering: probe all constructed providers'
        #    reachability concurrently, healthy ones first. Providers expose
        #    an async health_check(); anything else is treated as unhealthy.
        async def _probe(p: Any) -> bool:
            check = getattr(p, "health_check", None)
            if check is None:
                return False
            try:
                return bool(await check())
            except Exception:
                return False

        health = await asyncio.gather(*(_probe(p) for p in constructed))

        ordered = [p for p, h in zip(constructed, health, strict=True) if h]
        ordered += [p for p, h in zip(constructed, health, strict=True) if not h]

        # 3. First-success wins.
        return ordered[0] if ordered else None

    async def _invoke_chat(
        self,
        provider: AIProvider,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        system_prompt: str | None,
    ) -> dict[str, Any]:
        """Invoke chat on the AIProvider seam (single dialect)."""
        prompt = messages[-1]["content"] if messages else ""
        res = await provider.generate_text(
            prompt=prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            messages=messages,
        )
        return {
            "message": res.content,
            "role": "assistant",
            "token_count": res.token_usage.total_tokens if res.token_usage else None,
            "model": res.model or model,
            "finish_reason": res.finish_reason,
        }

    async def _invoke_chat_stream(
        self,
        provider: AIProvider,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        system_prompt: str | None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream chat from the AIProvider seam (single dialect)."""
        prompt = messages[-1]["content"] if messages else ""
        async for chunk_str in provider.generate_text_stream(
            prompt=prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            messages=messages,
        ):
            if chunk_str:
                yield {"content": chunk_str, "done": False, "token_count": None}
        yield {"content": "", "done": True, "token_count": None}

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
        # M3: Removed verbose per-request logger.info to reduce logging overhead on hot path

        # R5-7: Parallelize pre-LLM DB reads using dedicated short-lived DB sessions
        # to avoid SQLAlchemy AsyncSession concurrency issues.
        async def _fetch_session():
            async with local_session() as db:
                return await ChatSessionService(db).get_session(session_id, user_id)

        async def _fetch_settings():
            async with local_session() as db:
                return await AISettingsService(db).get_or_create_settings(user_id)

        async def _fetch_messages():
            async with local_session() as db:
                return await ChatMessageService(db).get_session_messages(session_id, limit=50)

        session, user_settings, messages_result = await asyncio.gather(
            _fetch_session(),
            _fetch_settings(),
            _fetch_messages(),
        )

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
        user_msg = await self._message_service.add_message(
            session_id=session_id,
            data=ChatMessageCreate(role="user", content=request.message),
        )
        user_msg_id = user_msg.get("id") if isinstance(user_msg, dict) else getattr(user_msg, "id", None)

        # C2: Commit and release DB connection before LLM call
        await self.db.commit()

        # Phase2: Call LLM (connection released, no DB hold during inference)
        from src.app.core.utils.retry import with_fallback, with_retry

        _last_err: list[Exception] = []

        async def _primary_call() -> dict[str, Any]:
            try:
                return await with_retry(
                    lambda: self._invoke_chat(self.provider, model, history, temperature, max_tokens, system_prompt),
                    max_attempts=2,
                    delay_seconds=0.05,
                    exceptions=(AIProviderError,),
                )
            except Exception as e:
                _last_err.append(e)
                raise

        async def _fallback_call() -> dict[str, Any]:
            fb = await self._get_fallback_provider()
            if fb is not None and fb is not self.provider:
                logger.warning("Primary AI provider failed after retries, attempting fallback provider")
                return await self._invoke_chat(fb, model, history, temperature, max_tokens, system_prompt)
            if _last_err:
                raise _last_err[0]
            raise ChatCompletionError("No fallback provider available", "no_fallback", model)

        try:
            logger.debug("Calling AI provider with model=, temperature=", model=model, temperature=temperature)
            result = await with_fallback(_primary_call, _fallback_call, error_msg="Primary AI completion failed")
            logger.info("Chat completion successful, tokens=", val=result.get("token_count"))
        except (AIProviderError, ChatCompletionError) as e:
            # P2.20 FIX: the user message was already committed above. On LLM
            # failure it would be left as a dangling turn with no assistant reply,
            # so remove it to keep the conversation consistent.
            if user_msg_id is not None:
                try:
                    async with local_session() as fresh_db:
                        await crud_chat_messages.delete(db=fresh_db, id=user_msg_id)
                        await fresh_db.commit()
                except Exception as cleanup_err:  # noqa: BLE001
                    logger.warning(
                        "Failed to clean up orphaned chat user message",
                        id=user_msg_id,
                        error=cleanup_err,
                    )
            err_code = getattr(e, "error_code", "completion_error")
            err_msg = getattr(e, "message", str(e))
            logger.error("Chat completion failed: ()", message=err_msg, error_code=err_code)
            if isinstance(e, ChatCompletionError):
                raise
            raise ChatCompletionError(err_msg, err_code, model) from e

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

        # R5-7: Parallelize pre-LLM DB reads using dedicated short-lived DB sessions
        # to avoid SQLAlchemy AsyncSession concurrency issues.
        async def _fetch_session():
            async with local_session() as db:
                return await ChatSessionService(db).get_session(session_id, user_id)

        async def _fetch_settings():
            async with local_session() as db:
                return await AISettingsService(db).get_or_create_settings(user_id)

        async def _fetch_messages():
            async with local_session() as db:
                return await ChatMessageService(db).get_session_messages(session_id, limit=50)

        session, user_settings, messages_result = await asyncio.gather(
            _fetch_session(),
            _fetch_settings(),
            _fetch_messages(),
        )

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

        from src.app.core.tenancy.context import get_current_tenant

        current_tenant = get_current_tenant()

        try:
            _last_err: list[Exception] = []
            try:
                async for chunk in self._invoke_chat_stream(
                    self.provider, model, history, temperature, max_tokens, system_prompt
                ):
                    content = chunk.get("content", "")
                    if content:
                        response_chunks.append(content)
                    if chunk.get("done"):
                        token_count = chunk.get("token_count")
                    yield chunk
            except (AIProviderError, ChatCompletionError) as primary_err:
                _last_err.append(primary_err)
                logger.warning("Primary provider streaming failed, checking fallback", error=str(primary_err))
                fb = await self._get_fallback_provider()
                if fb is not None and fb is not self.provider:
                    try:
                        async for chunk in self._invoke_chat_stream(
                            fb, model, history, temperature, max_tokens, system_prompt
                        ):
                            content = chunk.get("content", "")
                            if content:
                                response_chunks.append(content)
                            if chunk.get("done"):
                                token_count = chunk.get("token_count")
                            yield chunk
                    except Exception as fallback_err:
                        logger.error("Fallback streaming also failed", error=str(fallback_err))
                        err_code = getattr(fallback_err, "error_code", "streaming_error")
                        err_msg = getattr(fallback_err, "message", str(fallback_err))
                        raise ChatCompletionError(err_msg, err_code, model) from fallback_err
                else:
                    err_code = getattr(primary_err, "error_code", "streaming_error")
                    err_msg = getattr(primary_err, "message", str(primary_err))
                    if isinstance(primary_err, ChatCompletionError):
                        raise primary_err
                    raise ChatCompletionError(err_msg, err_code, model) from primary_err

        finally:
            # Phase 3: Get fresh session for writing response safely in the background
            full_response = "".join(response_chunks)
            # P2.21 FIX: persist even if the client disconnected before the final
            # 'done' chunk arrived, so a partial assistant turn isn't lost.
            if full_response:

                async def _save_response(resp_content: str, resp_tokens: int | None, tenant_ctx: Any) -> None:
                    import asyncio

                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            if tenant_ctx:
                                from src.app.core.tenancy.context import set_current_tenant

                                set_current_tenant(tenant_ctx)
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
    provider: AIProvider | None = None,
    fallback_provider: AIProvider | None = None,
) -> ChatCompletionService:
    """Get ChatCompletionService instance."""
    return ChatCompletionService(db, provider=provider, fallback_provider=fallback_provider)
