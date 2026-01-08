"""AI Features router - HTTP handlers for AI chat operations."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_user
from src.app.core.db.database import async_get_db

from .schemas import (
    AIServiceError,
    AISettingsRead,
    AISettingsUpdate,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessagePage,
    ChatMessageRead,
    ChatSessionCreate,
    ChatSessionPage,
    ChatSessionRead,
    ChatSessionReadWithMessages,
    ChatSessionUpdate,
)
from .service import (
    ChatCompletionError,
    get_ai_settings_service,
    get_chat_completion_service,
    get_chat_message_service,
    get_chat_session_service,
)

router = APIRouter(prefix="/ai", tags=["ai"])


# =============================================================================
# CHAT SESSION ENDPOINTS
# =============================================================================


@router.post("/sessions", response_model=ChatSessionRead, status_code=201)
async def create_chat_session(
    data: ChatSessionCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Create a new chat session."""
    service = get_chat_session_service(db)
    return await service.create_session(current_user["id"], data)


@router.get("/sessions", response_model=ChatSessionPage)
async def list_chat_sessions(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = 1,
    limit: int = 20,
) -> dict:
    """List chat sessions for the current user."""
    service = get_chat_session_service(db)
    result = await service.get_user_sessions(current_user["id"], page=page, limit=limit)
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    pages = (total + limit - 1) // limit if limit > 0 else 1
    return {
        "items": data,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
    }


@router.get("/sessions/{session_id}", response_model=ChatSessionReadWithMessages)
async def get_chat_session(
    session_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Get a chat session with its messages."""
    session_service = get_chat_session_service(db)
    message_service = get_chat_message_service(db)

    session = await session_service.get_session(session_id, current_user["id"])
    messages_result = await message_service.get_session_messages(session_id, limit=100)
    messages = messages_result.get("data", [])

    return {**session, "messages": messages}


@router.patch("/sessions/{session_id}", response_model=ChatSessionRead)
async def update_chat_session(
    session_id: int,
    data: ChatSessionUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Update a chat session."""
    service = get_chat_session_service(db)
    return await service.update_session(session_id, current_user["id"], data)


@router.delete("/sessions/{session_id}")
async def delete_chat_session(
    session_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Delete a chat session."""
    service = get_chat_session_service(db)
    return await service.delete_session(session_id, current_user["id"])


# =============================================================================
# CHAT MESSAGE ENDPOINTS
# =============================================================================


@router.get("/sessions/{session_id}/messages", response_model=ChatMessagePage)
async def list_session_messages(
    session_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = 1,
    limit: int = 50,
) -> dict:
    """List messages in a chat session."""
    # First verify session ownership
    session_service = get_chat_session_service(db)
    await session_service.get_session(session_id, current_user["id"])

    message_service = get_chat_message_service(db)
    result = await message_service.get_session_messages(session_id, page=page, limit=limit)
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    pages = (total + limit - 1) // limit if limit > 0 else 1
    return {
        "items": data,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
    }


# =============================================================================
# AI SETTINGS ENDPOINTS
# =============================================================================


@router.get("/settings", response_model=AISettingsRead)
async def get_ai_settings(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Get AI settings for the current user."""
    service = get_ai_settings_service(db)
    return await service.get_or_create_settings(current_user["id"])


@router.patch("/settings", response_model=AISettingsRead)
async def update_ai_settings(
    data: AISettingsUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Update AI settings for the current user."""
    service = get_ai_settings_service(db)
    return await service.update_settings(current_user["id"], data)


# =============================================================================
# CHAT COMPLETION ENDPOINTS
# =============================================================================


@router.post(
    "/sessions/{session_id}/chat",
    response_model=ChatCompletionResponse,
    responses={
        503: {"model": AIServiceError, "description": "AI service unavailable"},
    },
)
async def chat_completion(
    session_id: int,
    request: ChatCompletionRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Send a message and get an AI response.

    This endpoint sends the user's message to the AI model and returns
    the complete response. For real-time streaming, use the /chat/stream endpoint.
    """
    service = get_chat_completion_service(db)
    try:
        return await service.complete(session_id, current_user["id"], request)
    except ChatCompletionError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "detail": e.message,
                "error_code": e.error_code,
                "model": e.model,
            },
        ) from e


@router.post("/sessions/{session_id}/chat/stream")
async def chat_completion_stream(
    session_id: int,
    request: ChatCompletionRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> StreamingResponse:
    """Send a message and stream the AI response.

    Returns a Server-Sent Events (SSE) stream of response chunks.
    Each chunk is a JSON object with 'content', 'done', and optionally 'token_count'.
    """
    service = get_chat_completion_service(db)

    async def generate():
        try:
            async for chunk in service.complete_stream(
                session_id, current_user["id"], request
            ):
                yield f"data: {json.dumps(chunk)}\n\n"
        except ChatCompletionError as e:
            error = {
                "error": True,
                "detail": e.message,
                "error_code": e.error_code,
                "model": e.model,
            }
            yield f"data: {json.dumps(error)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
