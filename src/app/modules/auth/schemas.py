"""Auth schemas - token-related data models.

Note: Token and TokenData are kept in core/schemas.py to avoid circular imports.
This module re-exports them for module-internal use and adds auth-specific schemas.
"""

from pydantic import BaseModel, Field

# Re-export from core for convenience (these are the canonical definitions)
from src.app.core.schemas import Token, TokenBlacklistBase, TokenBlacklistCreate, TokenBlacklistRead, TokenData


class AuthTokenResponse(BaseModel):
    """Full authentication token response for new auth flows.

    Used by Magic Link, SSO, and Passkey authentication to return
    complete token information including refresh token and expiration.
    """

    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="Refresh token for obtaining new access tokens")
    token_type: str = Field(default="bearer", description="Token type (always 'bearer')")
    expires_in: int = Field(..., description="Access token expiration time in seconds")


class LoginRequest(BaseModel):
    """Login request schema (for documentation)."""

    username: str
    password: str


class LoginTokenResponse(BaseModel):
    """Login success response schema."""

    access_token: str
    token_type: str = "bearer"


class LoginMFAResponse(BaseModel):
    """Login MFA required response schema."""

    mfa_required: str
    mfa_token: str
    expires_in: str
    message: str


class RefreshTokenResponse(BaseModel):
    """Refresh token response schema."""

    access_token: str
    token_type: str = "bearer"


class LogoutResponse(BaseModel):
    """Logout response schema."""

    message: str = "Logged out successfully"


class SessionInfo(BaseModel):
    """Session information schema."""

    id: str
    user_id: int
    created_at: int
    last_accessed_at: int
    ip_address: str | None = None
    user_agent: str | None = None


class SessionListResponse(BaseModel):
    """Session list response schema."""

    sessions: list[SessionInfo]


class SessionCountResponse(BaseModel):
    """Session count response schema."""

    count: int


class PasswordChangeRequest(BaseModel):
    """Password change request schema."""

    current_password: str
    new_password: str


class PasswordResetRequest(BaseModel):
    """Password reset request schema (forgot password)."""

    email: str


class PasswordResetConfirm(BaseModel):
    """Password reset confirmation schema."""

    token: str
    new_password: str


class EmailVerificationRequest(BaseModel):
    """Schema for requesting email verification (resend)."""

    email: str


class EmailVerificationConfirm(BaseModel):
    """Schema for confirming email verification."""

    token: str


class MessageResponse(BaseModel):
    """Generic message response schema for simple success messages."""

    message: str


# M7: Consolidate duplicate message schemas - use MessageResponse as base
# Type aliases for backward compatibility with existing API contracts
PasswordChangeResponse = MessageResponse
PasswordResetResponse = MessageResponse
EmailVerificationResponse = MessageResponse
ForgotPasswordResponse = MessageResponse


__all__ = [
    "AuthTokenResponse",
    "EmailVerificationResponse",
    "ForgotPasswordResponse",
    "LoginMFAResponse",
    "LoginRequest",
    "LogoutResponse",
    "MessageResponse",
    "PasswordChangeRequest",
    "PasswordChangeResponse",
    "PasswordResetConfirm",
    "PasswordResetRequest",
    "PasswordResetResponse",
    "RefreshTokenResponse",
    "SessionCountResponse",
    "SessionInfo",
    "SessionListResponse",
    "Token",
    "TokenBlacklistBase",
    "TokenBlacklistCreate",
    "TokenBlacklistRead",
    "TokenData",
]
