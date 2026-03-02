"""AI module unified error exceptions.

Maps provider-specific errors to common application exceptions for consistent
error handling across the AI module.

T135: Unified AI Error Exceptions
"""


from __future__ import annotations

from uuid import UUID

from src.app.modules.ai.providers import AIProviderError


class AIAuthenticationError(AIProviderError):
    """Raised when authentication with AI provider fails.

    Typically caused by invalid API keys, tokens, or credentials.
    """

    def __init__(
        self,
        message: str = "Authentication failed with AI provider",
        provider: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, error_code="authentication_error", provider=provider)
        self.original_error = original_error


class AIRateLimitError(AIProviderError):
    """Raised when AI provider rate limit is exceeded.

    The client should back off and retry after appropriate delay.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded for AI provider",
        provider: str | None = None,
        retry_after: int | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, error_code="rate_limit_error", provider=provider)
        self.retry_after = retry_after
        self.original_error = original_error


class AIQuotaExceededError(AIProviderError):
    """Raised when AI provider quota is exceeded.

    This is different from rate limit - it's a hard limit on usage (tokens, requests, etc.).
    """

    def __init__(
        self,
        message: str = "Quota exceeded for AI provider",
        provider: str | None = None,
        quota_type: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, error_code="quota_exceeded_error", provider=provider)
        self.quota_type = quota_type
        self.original_error = original_error


class AIModelNotFoundError(AIProviderError):
    """Raised when requested model is not available on the provider.

    The model name may be incorrect or not accessible with current credentials.
    """

    def __init__(
        self,
        message: str = "Model not found on AI provider",
        provider: str | None = None,
        model_name: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, error_code="model_not_found_error", provider=provider)
        self.model_name = model_name
        self.original_error = original_error


class AIInvalidRequestError(AIProviderError):
    """Raised when request to AI provider is invalid.

    Caused by malformed requests, invalid parameters, or validation errors.
    """

    def __init__(
        self,
        message: str = "Invalid request to AI provider",
        provider: str | None = None,
        field: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, error_code="invalid_request_error", provider=provider)
        self.field = field
        self.original_error = original_error


class AIContentPolicyError(AIProviderError):
    """Raised when content violates provider's content policy.

    The input or generated content was flagged as inappropriate.
    """

    def __init__(
        self,
        message: str = "Content policy violation",
        provider: str | None = None,
        policy_type: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, error_code="content_policy_error", provider=provider)
        self.policy_type = policy_type
        self.original_error = original_error


class AIServerError(AIProviderError):
    """Raised when AI provider experiences an internal server error.

    These are temporary failures on the provider's side.
    """

    def __init__(
        self,
        message: str = "AI provider server error",
        provider: str | None = None,
        status_code: int | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, error_code="server_error", provider=provider)
        self.status_code = status_code
        self.original_error = original_error


class AIServiceUnavailableError(AIProviderError):
    """Raised when AI provider is temporarily unavailable.

    The service may be down for maintenance or experiencing issues.
    """

    def __init__(
        self,
        message: str = "AI provider service unavailable",
        provider: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, error_code="service_unavailable_error", provider=provider)
        self.original_error = original_error


class AITimeoutError(AIProviderError):
    """Raised when AI provider request times out.

    The request took longer than the configured timeout.
    """

    def __init__(
        self,
        message: str = "AI provider request timeout",
        provider: str | None = None,
        timeout_seconds: float | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, error_code="timeout_error", provider=provider)
        self.timeout_seconds = timeout_seconds
        self.original_error = original_error


class AIConnectionError(AIProviderError):
    """Raised when connection to AI provider fails.

    Network errors, DNS issues, or unreachable endpoints.
    """

    def __init__(
        self,
        message: str = "Failed to connect to AI provider",
        provider: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, error_code="connection_error", provider=provider)
        self.original_error = original_error


class AIResponseParseError(AIProviderError):
    """Raised when AI provider response cannot be parsed.

    The response format is unexpected or malformed.
    """

    def __init__(
        self,
        message: str = "Failed to parse AI provider response",
        provider: str | None = None,
        expected_format: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, error_code="response_parse_error", provider=provider)
        self.expected_format = expected_format
        self.original_error = original_error


class AIProviderUnavailableError(AIProviderError):
    """Raised when AI provider is not configured or unavailable.

    The provider credentials may not be set or the service is not enabled.
    """

    def __init__(
        self,
        message: str = "AI provider is not available or not configured",
        provider: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, error_code="provider_unavailable_error", provider=provider)
        self.original_error = original_error


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
        tenant_id: int | UUID,
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


__all__ = [
    "AIAuthenticationError",
    "AIRateLimitError",
    "AIQuotaExceededError",
    "AIModelNotFoundError",
    "AIInvalidRequestError",
    "AIContentPolicyError",
    "AIServerError",
    "AIServiceUnavailableError",
    "AITimeoutError",
    "AIConnectionError",
    "AIResponseParseError",
    "AIProviderUnavailableError",
    "SessionLimitExceededError",
]
