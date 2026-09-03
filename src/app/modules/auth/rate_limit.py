"""Authentication rate limiting dependencies."""

from fastapi import Request

from src.app.core.exceptions.http_exceptions import RateLimitException
from src.app.core.utils.ip_utils import get_client_ip
from src.app.core.utils.rate_limit import rate_limiter

AUTH_RATE_LIMIT = 5  # attempts
AUTH_RATE_PERIOD = 60  # 1 minute


async def check_auth_rate_limit(request: Request) -> None:
    """Rate limit sensitive auth endpoints by client IP."""
    client_ip = get_client_ip(request)
    key = f"auth_ip:{client_ip}"
    is_limited, _ = await rate_limiter.check_rate_limit(key, limit=AUTH_RATE_LIMIT, period=AUTH_RATE_PERIOD)

    if is_limited:
        raise RateLimitException(f"Too many authentication attempts. Please try again in {AUTH_RATE_PERIOD} seconds.")
