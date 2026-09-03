"""Auth and identity domain exceptions."""

from fastapi import HTTPException, status


class AuthError(HTTPException):
    """Base exception for authentication operations."""

    def __init__(
        self,
        detail: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail)


class MagicLinkError(AuthError):
    """Base exception for magic link operations."""

    pass


class InvalidMagicLinkError(MagicLinkError):
    """Raised when magic link token is invalid or expired."""

    def __init__(self, detail: str = "Invalid or expired magic link token") -> None:
        super().__init__(detail=detail, status_code=status.HTTP_400_BAD_REQUEST)


class MagicLinkAlreadyUsedError(MagicLinkError):
    """Raised when magic link token has already been used."""

    def __init__(self, detail: str = "Magic link has already been used") -> None:
        super().__init__(detail=detail, status_code=status.HTTP_410_GONE)


class MagicLinkRateLimitError(MagicLinkError):
    """Raised when rate limit for magic link requests is exceeded."""

    def __init__(self, detail: str = "Too many magic link requests. Please try again later.") -> None:
        super().__init__(detail=detail, status_code=status.HTTP_429_TOO_MANY_REQUESTS)


class MagicLinkUserInactiveError(MagicLinkError):
    """Raised when user account is inactive/disabled."""

    def __init__(self, detail: str = "User account is inactive") -> None:
        super().__init__(detail=detail, status_code=status.HTTP_403_FORBIDDEN)


__all__ = [
    "AuthError",
    "MagicLinkError",
    "InvalidMagicLinkError",
    "MagicLinkAlreadyUsedError",
    "MagicLinkRateLimitError",
    "MagicLinkUserInactiveError",
]
