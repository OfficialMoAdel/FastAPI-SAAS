"""AI Rate Limiting Middleware.

T099: Add rate limiting middleware (60 req/user/min)
T100: Add HTTP 429 RateLimitError response

Provides FastAPI dependencies for rate limiting AI endpoints.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Response, status

from src.app.api.deps import get_current_user
from src.app.api.deps.auth import AuthenticatedUser
from src.app.modules.ai.rate_limiter import RateLimitError, check_ai_rate_limit


async def check_rate_limit_dependency(
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    """FastAPI dependency to check rate limit before processing request.

    T099: Rate limiting middleware (60 requests/user/minute).
    T100: Returns HTTP 429 with Retry-After header when rate limited.

    Args:
        response: FastAPI response object for adding headers
        current_user: The authenticated user making the request

    Returns:
        The current user dict if rate limit check passes

    Raises:
        HTTPException: 429 Too Many Requests if rate limit exceeded
    """
    user_id = current_user.id

    try:
        is_allowed, remaining, retry_after = await check_ai_rate_limit(user_id)

        # Add rate limit headers to response
        response.headers["X-RateLimit-Limit"] = "60"
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(retry_after)

        return current_user

    except RateLimitError as e:
        # T100: Return HTTP 429 with Retry-After header.
        # P1-19: sanctioned HTTPException — this is a FastAPI Depends
        # dependency (native HTTPException support), and the Retry-After /
        # X-RateLimit-* headers plus the structured {error, retry_after}
        # detail have no domain-table equivalent (RateLimitExceededError
        # maps to a bare 429 with no headers).
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limit_exceeded",
                "message": e.message,
                "retry_after": e.retry_after,
            },
            headers={
                "Retry-After": str(e.retry_after),
                "X-RateLimit-Limit": "60",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(e.retry_after),
            },
        ) from e


# Type alias for dependency injection
RateLimitedUser = Annotated[AuthenticatedUser, Depends(check_rate_limit_dependency)]
