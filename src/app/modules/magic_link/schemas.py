"""Magic Link authentication Pydantic schemas.

Request and response schemas for magic link passwordless authentication.
Follows the API contract defined in auth-api.yaml.
"""

from pydantic import BaseModel, EmailStr, Field


class MagicLinkSendRequest(BaseModel):
    """Request schema for sending a magic link.

    POST /api/v1/auth/magic-link/send
    """

    email: EmailStr = Field(
        ...,
        description="Email address to send the magic link to",
        examples=["user@example.com"],
    )


class MagicLinkSendResponse(BaseModel):
    """Response schema after sending a magic link.

    Note: Always returns success message to prevent email enumeration,
    even if the user doesn't exist.
    """

    message: str = Field(
        default="If an account exists, a magic link has been sent",
        description="Status message",
    )
    may_be_delayed: bool = Field(
        default=False,
        description="Whether email delivery may be delayed due to queuing",
    )


class MagicLinkVerifyRequest(BaseModel):
    """Request schema for verifying a magic link token.

    POST /api/v1/auth/magic-link/verify
    """

    token: str = Field(
        ...,
        description="Magic link token from the email link",
        min_length=32,
        max_length=64,
        examples=["abc123..."],
    )


# Note: MagicLinkVerifyResponse uses AuthTokenResponse from auth.schemas
# which includes: access_token, refresh_token, token_type, expires_in
