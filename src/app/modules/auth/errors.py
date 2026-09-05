"""Auth and identity domain exceptions."""

from __future__ import annotations


class AuthError(Exception):
    """Base exception for authentication operations (plain domain exception)."""

    def __init__(
        self,
        detail: str = "Authentication error",
        status_code: int = 400,
    ) -> None:
        self.detail = detail
        self.message = detail
        self.status_code = status_code
        super().__init__(self.detail)


class MagicLinkError(AuthError):
    """Base exception for magic link operations."""

    pass


class InvalidMagicLinkError(MagicLinkError):
    """Raised when magic link token is invalid or expired."""

    def __init__(self, detail: str = "Invalid or expired magic link token") -> None:
        super().__init__(detail=detail, status_code=400)


class MagicLinkAlreadyUsedError(MagicLinkError):
    """Raised when magic link token has already been used."""

    def __init__(self, detail: str = "Magic link has already been used") -> None:
        super().__init__(detail=detail, status_code=410)


class MagicLinkRateLimitError(MagicLinkError):
    """Raised when rate limit for magic link requests is exceeded."""

    def __init__(self, detail: str = "Too many magic link requests. Please try again later.") -> None:
        super().__init__(detail=detail, status_code=429)


class MagicLinkUserInactiveError(MagicLinkError):
    """Raised when user account is inactive/disabled."""

    def __init__(self, detail: str = "User account is inactive") -> None:
        super().__init__(detail=detail, status_code=403)


__all__ = [
    "AuthError",
    "MagicLinkError",
    "InvalidMagicLinkError",
    "MagicLinkAlreadyUsedError",
    "MagicLinkRateLimitError",
    "MagicLinkUserInactiveError",
]
