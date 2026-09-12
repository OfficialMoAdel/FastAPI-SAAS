"""AI Features router - HTTP handlers for AI chat operations."""

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep
from src.app.core.db.database import async_get_db
from src.app.core.json_utils import json_dumps
from src.app.modules.ai.factory import AIProviderFactory
from src.app.modules.ai.middleware import RateLimitedUser
from src.app.modules.ai.schemas import (
    AIModelsListResponse,
    AIServiceError,
    AISettingsRead,
    AISettingsUpdate,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessageList,
    ChatSessionCreate,
    ChatSessionList,
    ChatSessionRead,
    ChatSessionReadWithMessages,
    ChatSessionUpdate,
)
from src.app.modules.ai.schemas import (
    RateLimitError as RateLimitErrorSchema,
)
from src.app.modules.ai.service import (
    AISettingsService,
    ChatCompletionError,
    ChatMessageService,
    ChatSessionService,
    get_chat_completion_service,
)
from src.app.shared.exceptions import ServiceUnavailableError

router = APIRouter(prefix="/ai", tags=["ai"])


def get_chat_session_service(db: AsyncSession = Depends(async_get_db)) -> ChatSessionService:
    """Dependency injection factory (Q-M4: lives in router, not service)."""
    return ChatSessionService(db)


def get_chat_message_service(db: AsyncSession = Depends(async_get_db)) -> ChatMessageService:
    """Dependency injection factory (Q-M4: lives in router, not service)."""
    return ChatMessageService(db)


def get_ai_settings_service(db: AsyncSession = Depends(async_get_db)) -> AISettingsService:
    """Dependency injection factory (Q-M4: lives in router, not service)."""
    return AISettingsService(db)


# =============================================================================
# CHAT SESSION ENDPOINTS
# =============================================================================


@router.post("/sessions", response_model=ChatSessionRead, status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    data: ChatSessionCreate,
    ctx: RequestContextDep,
) -> Any:
    """Create a new chat session."""
    service = get_chat_session_service(ctx.db)
    return await service.create_session(ctx.user_id, data)


@router.get("/sessions", response_model=ChatSessionList)
async def list_chat_sessions(
    ctx: RequestContextDep,
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
) -> Any:
    """List chat sessions for the current user."""
    service = get_chat_session_service(ctx.db)
    result = await service.get_user_sessions(ctx.user_id, page=page, limit=per_page)
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    return {
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.get("/sessions/{session_id}", response_model=ChatSessionReadWithMessages)
async def get_chat_session(
    session_id: int,
    ctx: RequestContextDep,
) -> Any:
    """Get a chat session with its messages."""
    session_service = get_chat_session_service(ctx.db)
    message_service = get_chat_message_service(ctx.db)

    session = await session_service.get_session(session_id, ctx.user_id)
    messages_result = await message_service.get_session_messages(session_id, limit=100)
    messages = messages_result.get("data", [])

    return {**session, "messages": messages}


@router.patch("/sessions/{session_id}", response_model=ChatSessionRead)
async def update_chat_session(
    session_id: int,
    data: ChatSessionUpdate,
    ctx: RequestContextDep,
) -> Any:
    """Update a chat session."""
    service = get_chat_session_service(ctx.db)
    return await service.update_session(session_id, ctx.user_id, data)


@router.delete("/sessions/{session_id}", response_model=dict)
async def delete_chat_session(
    session_id: int,
    ctx: RequestContextDep,
) -> Any:
    """Delete a chat session."""
    service = get_chat_session_service(ctx.db)
    return await service.delete_session(session_id, ctx.user_id)


# =============================================================================
# CHAT MESSAGE ENDPOINTS
# =============================================================================


@router.get("/sessions/{session_id}/messages", response_model=ChatMessageList)
async def list_session_messages(
    session_id: int,
    ctx: RequestContextDep,
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=100, description="Items per page"),
) -> Any:
    """List messages in a chat session."""
    # First verify session ownership
    session_service = get_chat_session_service(ctx.db)
    await session_service.get_session(session_id, ctx.user_id)

    message_service = get_chat_message_service(ctx.db)
    result = await message_service.get_session_messages(session_id, page=page, limit=per_page)
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    return {
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


# =============================================================================
# AI SETTINGS ENDPOINTS
# =============================================================================


@router.get("/settings", response_model=AISettingsRead)
async def get_ai_settings(
    ctx: RequestContextDep,
) -> Any:
    """Get AI settings for the current user."""
    service = get_ai_settings_service(ctx.db)
    return await service.get_or_create_settings(ctx.user_id)


@router.patch("/settings", response_model=AISettingsRead)
async def update_ai_settings(
    data: AISettingsUpdate,
    ctx: RequestContextDep,
) -> Any:
    """Update AI settings for the current user."""
    service = get_ai_settings_service(ctx.db)
    return await service.update_settings(ctx.user_id, data)


@router.get("/models", response_model=AIModelsListResponse)
async def list_available_models(
    ctx: RequestContextDep,
) -> Any:
    """List available AI models.

    Returns a list of model names available on the configured provider,
    plus the default model.
    """
    provider = AIProviderFactory.get_provider()
    models = await provider.list_models()
    return {
        "models": models,
        "default_model": "gemma3",
    }


# =============================================================================
# CHAT COMPLETION ENDPOINTS
# =============================================================================
# NOTE: RateLimitedUser carries the rate-limit side effect (Redis INCR +
# X-RateLimit headers via check_rate_limit_dependency). Replacing it with
# plain RequestContextDep would silently drop the limiter — keep the
# dedicated dep here and extract a ctx-aware limiter in a follow-up if
# the 60 req/min policy needs to be shared with other modules.


@router.post(
    "/sessions/{session_id}/chat",
    response_model=ChatCompletionResponse,
    responses={
        429: {"model": RateLimitErrorSchema, "description": "Rate limit exceeded"},
        503: {"model": AIServiceError, "description": "AI service unavailable"},
    },
)
async def chat_completion(
    session_id: int,
    request: ChatCompletionRequest,
    current_user: RateLimitedUser,
    db: AsyncSession = Depends(async_get_db),
) -> Any:
    """Send a message and get an AI response.

    This endpoint sends the user's message to the AI model and returns
    the complete response. For real-time streaming, use the /chat/stream endpoint.

    Rate limited to 60 requests per user per minute.
    """
    service = get_chat_completion_service(db)
    try:
        return await service.complete(session_id, current_user.id, request)
    except ChatCompletionError as e:
        raise ServiceUnavailableError(e.message) from e


@router.post(
    "/sessions/{session_id}/chat/stream",
    responses={
        429: {"model": RateLimitErrorSchema, "description": "Rate limit exceeded"},
    },
)
async def chat_completion_stream(
    session_id: int,
    request: ChatCompletionRequest,
    current_user: RateLimitedUser,
    db: AsyncSession = Depends(async_get_db),
) -> StreamingResponse:
    """Send a message and stream the AI response.

    Returns a Server-Sent Events (SSE) stream of response chunks.
    Each chunk is a JSON object with 'content', 'done', and optionally 'token_count'.

    Rate limited to 60 requests per user per minute.
    """
    service = get_chat_completion_service(db)

    async def generate():
        try:
            async for chunk in service.complete_stream(session_id, current_user.id, request):
                data = str(json_dumps(chunk, as_str=True))
                yield f"data: {data}\n\n"
        except ChatCompletionError as e:
            error = {
                "error": True,
                "detail": e.message,
                "error_code": e.error_code,
                "model": e.model,
            }
            data = str(json_dumps(error, as_str=True))
            yield f"data: {data}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
