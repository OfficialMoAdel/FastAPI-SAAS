"""AI rate-limit dependency (s92p.3: relocated from modules/ai/middleware.py).

It lived under modules/ but was a pure FastAPI adapter: it imported the api
layer and offered a `Depends`, which is the same category as a router. Under
the widened feature-must-not-import-api contract that made it the ONE real
offender among 187 module files. It has no AI-domain logic — only the
security-budget probe — so it belongs beside the other dependencies it uses.

T099: Add rate limiting middleware (60 req/user/min)
T100: Add HTTP 429 RateLimitError response

Provides FastAPI dependencies for rate limiting AI endpoints.

The engine seam: this adapter keeps its sanctioned HTTPException 429 shape
(Retry-After / X-RateLimit headers, structured detail — no domain-table
equivalent) but delegates the decision to the unified rate-policy engine's
the security-budget seam. The "ai_chat" meter (60/min per user by default)
lives in ``settings.rate_limit.SECURITY_BUDGETS``; keys, algorithm choice
(GCRA/fixed-window), and fail-open policy are owned by the engine.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Response, status

from src.app.api.deps import get_current_user
from src.app.api.deps.auth import AuthenticatedUser
from src.app.core.security.budgets import Identity, SecurityAction, probe
from src.app.core.security.rate_policy import window_reset_seconds


async def check_rate_limit_dependency(
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    """FastAPI dependency to check rate limit before processing request.

    T099: Rate limiting middleware (60 requests/user/minute by default).
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

    verdict = await probe(SecurityAction.AI_CHAT, Identity(user_id=user_id))

    # Add rate limit headers to response. The canonical set comes from the
    # verdict itself (build_verdict), so this surface speaks the same
    # contract as every other rate-limited route instead of hand-rolling
    # three headers. X-RateLimit-Reset is the window the counter lives in —
    # the old code sent `retry_after`, which is 0 on every ALLOWED request
    # and told the client nothing.
    for header_name, header_value in verdict.headers.items():
        response.headers[header_name] = header_value
    response.headers["X-RateLimit-Reset"] = str(window_reset_seconds(verdict.period))

    if verdict.is_limited:
        # T100: Return HTTP 429 with Retry-After header.
        # P1-19: sanctioned HTTPException — this is a FastAPI Depends
        # dependency (native HTTPException support), and the extra
        # X-RateLimit-* headers plus the structured {error, retry_after}
        # detail have no domain-table equivalent (RateLimitExceededError
        # maps to a bare 429 with table Retry-After only).
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Rate limit exceeded. Retry after {verdict.retry_after} seconds.",
                "retry_after": verdict.retry_after,
            },
            headers={
                "Retry-After": str(verdict.retry_after),
                "X-RateLimit-Limit": str(verdict.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Period": str(verdict.period),
                "X-RateLimit-Reset": str(window_reset_seconds(verdict.period)),
            },
        )

    return current_user


# Type alias for dependency injection
RateLimitedUser = Annotated[AuthenticatedUser, Depends(check_rate_limit_dependency)]
