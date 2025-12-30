"""API Key Pydantic schemas."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class APIKeyBase(BaseModel):
    """Base schema for API key."""

    name: Annotated[str, Field(min_length=1, max_length=100, examples=["Production API Key"])]
    scopes: list[str] | None = Field(default=None, examples=[["read:posts", "write:posts"]])
    expires_at: datetime | None = Field(default=None, description="Optional expiration date")


class APIKeyCreate(APIKeyBase):
    """Schema for creating an API key."""

    model_config = ConfigDict(extra="forbid")


class APIKeyRead(APIKeyBase):
    """Schema for reading an API key (without the actual key)."""

    id: int
    key_prefix: str
    user_id: int
    tenant_id: int
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime

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


class APIKeyListResponse(BaseModel):
    """Schema for listing API keys."""

    data: list[APIKeyRead]
    total_count: int


class APIKeyRevokeResponse(BaseModel):
    """Schema for API key revocation response."""

    message: str = "API key revoked successfully"
