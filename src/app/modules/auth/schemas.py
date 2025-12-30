"""Auth schemas - token-related data models.

Note: Token and TokenData are kept in core/schemas.py to avoid circular imports.
This module re-exports them for module-internal use and adds auth-specific schemas.
"""


from pydantic import BaseModel

# Re-export from core for convenience (these are the canonical definitions)
from ...core.schemas import Token, TokenBlacklistBase, TokenBlacklistCreate, TokenBlacklistRead, TokenData


class LoginRequest(BaseModel):
    """Login request schema (for documentation)."""

    username: str
    password: str


class RefreshTokenResponse(BaseModel):
    """Refresh token response schema."""

    access_token: str
    token_type: str = "bearer"


class LogoutResponse(BaseModel):
    """Logout response schema."""

    message: str = "Logged out successfully"


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


class PasswordResetResponse(BaseModel):
    """Password reset response schema."""

    message: str


class EmailVerificationRequest(BaseModel):
    """Schema for requesting email verification (resend)."""

    email: str


class EmailVerificationConfirm(BaseModel):
    """Schema for confirming email verification."""

    token: str


class EmailVerificationResponse(BaseModel):
    """Schema for email verification response."""

    message: str


# Re-export all token schemas for backward compatibility
__all__ = [
    "Token",
    "TokenData",
    "TokenBlacklistBase",
    "TokenBlacklistCreate",
    "TokenBlacklistRead",
    "LoginRequest",
    "RefreshTokenResponse",
    "LogoutResponse",
]
