"""AI Features Pydantic schemas.

Request/response models for AI chat sessions, messages,
and user AI settings/preferences.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from src.app.modules._base.schemas import BaseCreate, BaseList, BaseRead, BaseUpdate

# =============================================================================
# AI MODEL CONFIGURATION
# =============================================================================


# P2-6: widened from Literal to str — providers pass model names raw to
# the vendor API, and the Literal 422'd any non-Ollama name (gpt-4, claude-*).
# Length bounds keep the early-422 behavior for empty/absurd values.
AIModelType = Annotated[str, Field(min_length=1, max_length=128)]
MessageRole = Literal["user", "assistant", "system"]


# =============================================================================
# CHAT SESSION SCHEMAS
# =============================================================================


class ChatSessionCreate(BaseCreate):
    """Schema for creating a chat session."""

    title: Annotated[str | None, Field(max_length=255)] = None
    model: AIModelType = "gemma3"


class ChatSessionCreateInternal(ChatSessionCreate):
    """Internal schema for creating a chat session with tenant and user IDs."""

    tenant_id: UUID
    user_id: int


class ChatSessionUpdate(BaseUpdate):
    """Schema for updating a chat session."""

    title: Annotated[str | None, Field(max_length=255)] = None
    model: AIModelType | None = None


class ChatSessionRead(BaseRead):
    """Schema for reading a chat session."""

    tenant_id: UUID
    user_id: int
    title: str | None
    model: str


class ChatSessionReadWithMessages(ChatSessionRead):
    """Schema for reading a chat session with its messages."""

    messages: list["ChatMessageRead"] = []


# =============================================================================
# CHAT MESSAGE SCHEMAS
# =============================================================================


class ChatMessageCreate(BaseCreate):
    """Schema for creating a chat message."""

    role: MessageRole
    content: Annotated[str, Field(min_length=1, max_length=32000)]


class ChatMessageRead(BaseRead):
    """Schema for reading a chat message."""

    session_id: int
    role: str
    content: str
    token_count: int | None


class ChatMessageCreateInternal(BaseCreate):
    """Internal schema for persisting a chat message (includes tenant scoping)."""

    session_id: int
    tenant_id: UUID
    role: str
    content: str
    token_count: int | None = None


# =============================================================================
# AI SETTINGS SCHEMAS
# =============================================================================


class AISettingsUpdate(BaseUpdate):
    """Schema for updating user AI settings."""

    model: AIModelType | None = None
    temperature: Annotated[float | None, Field(ge=0.0, le=2.0)] = None
    max_tokens: Annotated[int | None, Field(ge=1, le=32000)] = None
    system_prompt: Annotated[str | None, Field(max_length=4000)] = None


class AISettingsRead(BaseRead):
    """Schema for reading user AI settings."""

    user_id: int
    model: str
    temperature: float
    max_tokens: int
    system_prompt: str | None


class AISettingsCreateInternal(BaseCreate):
    """Internal schema for persisting AI settings (includes tenant scoping)."""

    user_id: int
    tenant_id: UUID
    model: AIModelType
    temperature: Annotated[float, Field(ge=0.0, le=2.0)]
    max_tokens: Annotated[int, Field(ge=1, le=32000)]
    system_prompt: Annotated[str | None, Field(max_length=4000)] = None


class AIModelInfo(BaseCreate):
    """Schema for model information."""

    name: str
    available: bool = True


class AIModelsListResponse(BaseCreate):
    """Schema for available models list response."""

    models: list[str]
    default_model: str = "gemma3"


# =============================================================================
# CHAT COMPLETION SCHEMAS
# =============================================================================


class ChatCompletionRequest(BaseCreate):
    """Schema for requesting a chat completion."""

    message: Annotated[str, Field(min_length=1, max_length=32000)]
    model: AIModelType | None = None  # Override session default
    temperature: Annotated[float | None, Field(ge=0.0, le=2.0)] = None
    max_tokens: Annotated[int | None, Field(ge=1, le=32000)] = None


class ChatCompletionResponse(BaseCreate):
    """Schema for chat completion response."""

    message: str
    role: str = "assistant"
    token_count: int | None = None
    model: str
    finish_reason: str | None = None


class StreamingChunk(BaseCreate):
    """Schema for streaming response chunk."""

    content: str
    done: bool = False
    token_count: int | None = None


# =============================================================================
# PAGINATION SCHEMAS
# =============================================================================


class ChatSessionList(BaseList[ChatSessionRead]):
    """Standardized paginated chat session list response.

    Inherits from BaseList: items, total, page, per_page, has_next, has_previous
    """

    pass


class ChatSessionPage(BaseCreate):
    """Paginated list of chat sessions.

    Deprecated: Use ChatSessionList for new endpoints.
    This schema is kept for backward compatibility with existing API contracts.
    """

    items: list[ChatSessionRead]
    total: int
    page: int
    limit: int
    pages: int


class ChatMessageList(BaseList[ChatMessageRead]):
    """Standardized paginated chat message list response.

    Inherits from BaseList: items, total, page, per_page, has_next, has_previous
    """

    pass


class ChatMessagePage(BaseCreate):
    """Paginated list of chat messages.

    Deprecated: Use ChatMessageList for new endpoints.
    This schema is kept for backward compatibility with existing API contracts.
    """

    items: list[ChatMessageRead]
    total: int
    page: int
    limit: int
    pages: int


# =============================================================================
# ERROR SCHEMAS
# =============================================================================


class AIServiceError(BaseCreate):
    """Schema for AI service error response."""

    detail: str
    error_code: str
    model: str | None = None


class RateLimitError(BaseCreate):
    """Schema for rate limit error response."""

    detail: str
    retry_after: int  # seconds until retry allowed
    limit: int
    remaining: int


# Resolve forward references
ChatSessionReadWithMessages.model_rebuild()
