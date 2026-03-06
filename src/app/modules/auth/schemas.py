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
