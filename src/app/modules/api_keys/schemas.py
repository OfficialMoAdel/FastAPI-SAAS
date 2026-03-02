"""API Key Pydantic schemas."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.app.modules._base.schemas import BaseCreate, BaseList, BaseRead


class APIKeyBase(BaseModel):
    """Base schema for API key."""

    name: Annotated[str, Field(min_length=1, max_length=100, examples=["Production API Key"])]
    scopes: list[str] | None = Field(default=None, examples=[["read:posts", "write:posts"]])
    expires_at: datetime | None = Field(default=None, description="Optional expiration date")


class APIKeyCreate(APIKeyBase, BaseCreate):
    """Schema for creating an API key."""

    model_config = ConfigDict(extra="forbid")


class APIKeyRead(BaseRead):
    """Schema for reading an API key (without the actual key)."""

    name: str
    key_prefix: str
    user_id: int
    tenant_id: UUID
    is_active: bool
    scopes: list[str] | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class APIKeyResponse(BaseModel):
    """Schema for API key creation response (includes the actual key - only shown once)."""

    id: int
    name: str
    key: str  # The full API key - only returned on creation
    key_prefix: str
    expires_at: datetime | None
    created_at: datetime
    message: str = "Store this key securely. It will not be shown again."


class APIKeyList(BaseList[APIKeyRead]):
    """Standardized paginated API key list response.

    Inherits from BaseList: items, total, page, per_page, has_next, has_previous
    """

    pass


class APIKeyListResponse(BaseModel):
    """Schema for listing API keys.

    Deprecated: Use APIKeyList for new endpoints.
    This schema is kept for backward compatibility with existing API contracts.
    """

    data: list[APIKeyRead]
    total_count: int


class APIKeyRevokeResponse(BaseModel):
    """Schema for API key revocation response."""

    message: str = "API key revoked successfully"


class APIKeyRotateResponse(APIKeyResponse):
    """Schema for API key rotation response.

    Extends APIKeyResponse to include grace period info.
    """

    valid_from: datetime | None = None
    grace_period_days: int = 7
    message: str = (
        "Key rotated successfully. Old key remains valid for grace period. "
        "Store new key securely - it will not be shown again."
    )
