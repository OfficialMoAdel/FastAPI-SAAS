"""Auth and identity domain exceptions."""

from __future__ import annotations

from src.app.shared.exceptions import AppException


class AuthError(AppException):
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
class SSOGatewayError(AuthError):
    """SSO provider-transport failure (item 7: replaces raw HTTPException).

    Carries ``headers`` (``Set-Cookie`` clearing ``sso_state``) the same way
    the old sanctioned HTTPException did — the central table merges
    ``exc.headers`` into the envelope response. Status mirrors the old
    behavior: 504 for provider timeouts, 502 for provider error responses.
    """

    def __init__(self, detail: str = "SSO provider error", status_code: int = 502) -> None:
        super().__init__(detail=detail, status_code=status_code)
        self.headers: dict[str, str] | None = None

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
    "SSOGatewayError",
]
