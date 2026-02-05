"""AI module specific exceptions.

Provides exception classes for AI-related error scenarios.

T134: Session limit exceeded handling
T135: Unified AI error exceptions for multi-provider support
"""

from __future__ import annotations


class SessionLimitExceededError(Exception):
    """Raised when a tenant has reached the maximum number of concurrent sessions.

    Attributes:
        message: Human-readable error message.
        tenant_id: The tenant that exceeded the limit.
        limit: The maximum allowed sessions.
        current_count: The current number of sessions.
    """

    def __init__(
        self,
        tenant_id: int,
        limit: int = 100,
        current_count: int | None = None,
    ):
        """Initialize SessionLimitExceededError.

        Args:
            tenant_id: ID of the tenant that exceeded the limit.
            limit: Maximum allowed concurrent sessions.
            current_count: Current number of sessions for the tenant.
        """
        self.tenant_id = tenant_id
        self.limit = limit
        self.current_count = current_count
        self.message = f"Session limit exceeded. Maximum {limit} concurrent sessions allowed per tenant."
        super().__init__(self.message)


class AIAuthenticationError(Exception):
    """Raised when AI provider authentication fails.

    Common causes:
    - Invalid API key
    - Expired API key
    - Missing credentials
    """

    def __init__(self, provider: str, message: str = "Authentication failed with AI provider"):
        self.provider = provider
        self.message = f"{provider}: {message}"
        super().__init__(self.message)


class AIRateLimitError(Exception):
    """Raised when AI provider rate limit is exceeded.

    Common causes:
    - Too many requests per minute
    - Quota exceeded
    - Rate limit window not reset
    """

    def __init__(self, provider: str, message: str = "Rate limit exceeded", retry_after: int | None = None):
        self.provider = provider
        self.retry_after = retry_after
        self.message = f"{provider}: {message}"
        super().__init__(self.message)


class AIQuotaExceededError(Exception):
    """Raised when AI provider quota is exceeded.

    Common causes:
    - Monthly token limit reached
    - Billing quota exceeded
    - Account suspended
    """

    def __init__(self, provider: str, message: str = "Quota exceeded"):
        self.provider = provider
        self.message = f"{provider}: {message}"
        super().__init__(self.message)


class AIModelNotFoundError(Exception):
    """Raised when requested AI model is not found.

    Common causes:
    - Invalid model name
    - Model not available for this account
    - Model deprecated
    """

    def __init__(self, provider: str, model: str, message: str | None = None):
        self.provider = provider
        self.model = model
        self.message = message or f"{provider}: Model '{model}' not found"
        super().__init__(self.message)


class AIInvalidRequestError(Exception):
    """Raised when AI provider receives an invalid request.

    Common causes:
    - Invalid parameters
    - Malformed request
    - Unsupported feature
    """

    def __init__(self, provider: str, message: str = "Invalid request"):
        self.provider = provider
        self.message = f"{provider}: {message}"
        super().__init__(self.message)


class AITimeoutError(Exception):
    """Raised when AI provider request times out.

    Common causes:
    - Slow model response
    - Network issues
    - High server load
    """

    def __init__(self, provider: str, message: str = "Request timeout"):
        self.provider = provider
        self.message = f"{provider}: {message}"
        super().__init__(self.message)


class AIServerError(Exception):
    """Raised when AI provider returns a server error.

    Common causes:
    - Internal server error
    - Provider downtime
    - Service unavailable
    """

    def __init__(self, provider: str, message: str = "Provider server error"):
        self.provider = provider
        self.message = f"{provider}: {message}"
        super().__init__(self.message)


__all__ = [
    "SessionLimitExceededError",
    "AIAuthenticationError",
    "AIRateLimitError",
    "AIQuotaExceededError",
    "AIModelNotFoundError",
    "AIInvalidRequestError",
    "AITimeoutError",
    "AIServerError",
]
