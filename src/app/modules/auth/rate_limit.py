"""Authentication rate limiting dependency.

Single seam: the rate-policy engine's security-budget check
(``check_security_budget("auth", ...)``). Keys, limits, algorithm choice
(GCRA/fixed-window), and fail-open policy all live in the engine; this
module only supplies the identity (client IP) and the budget name.
"""

from fastapi import Request

from src.app.core.exceptions.http_exceptions import RateLimitException
from src.app.core.security.rate_policy import check_security_budget
from src.app.core.utils.ip_utils import get_client_ip


async def check_auth_rate_limit(request: Request) -> None:
    """Rate limit sensitive auth endpoints by client IP.

    Budget: ``auth`` in ``settings.rate_limit.SECURITY_BUDGETS``
    (default 5 attempts / 60s, previously hardcoded here).
    """
    client_ip = get_client_ip(request)
    verdict = await check_security_budget("auth", client_ip)

    if verdict.is_limited:
        raise RateLimitException(f"Too many authentication attempts. Please try again in {verdict.period} seconds.")
