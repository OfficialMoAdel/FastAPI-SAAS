"""Pydantic schemas for magic link authentication."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from .._base import BaseCreate, BaseRead

# =============================================================================
# Auth Flow Schemas (API Request/Response)
# =============================================================================


class MagicLinkSendRequest(BaseModel):
    """Request schema for sending a magic link."""

    email: EmailStr = Field(..., description="Email address to send magic link to")


class MagicLinkSendResponse(BaseModel):
    """Response schema after sending magic link."""

    message: str = Field(..., description="Success message")
    may_be_delayed: bool = Field(False, description="Whether email delivery may be delayed")


class MagicLinkVerifyRequest(BaseModel):
    """Request schema for verifying a magic link token."""

    token: str = Field(..., min_length=32, max_length=64, description="Magic link token")


# =============================================================================
# CRUD Schemas (for MagicLink entity)
# =============================================================================


class MagicLinkCreate(BaseCreate):
    """Schema for creating a magic link record."""

    user_id: int = Field(..., description="User ID")
    token: str = Field(..., max_length=64, description="URL-safe token")
    action: str = Field(..., max_length=50, description="Token purpose (login, verify_email, password_reset)")
    expires_at: datetime = Field(..., description="Token expiration timestamp")
    ip_address: str | None = Field(None, description="Request IP address")
    user_agent: str | None = Field(None, description="Request user agent")


class MagicLinkRead(BaseRead):
    """Schema for reading a magic link record."""

    user_id: int
    token: str
    action: str
    expires_at: datetime
    used_at: datetime | None
    ip_address: str | None
    user_agent: str | None
