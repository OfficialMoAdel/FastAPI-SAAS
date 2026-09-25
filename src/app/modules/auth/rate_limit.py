"""Authentication rate limiting dependency.

Single seam: the rate-policy engine's security-budget check
(``check_security_budget("auth", ...)``). Keys, limits, algorithm choice
(GCRA/fixed-window), and fail-open policy all live in the engine; this
module only supplies the identity (client IP) and the budget name.
"""

from fastapi import Request

from src.app.core.config import settings
from src.app.core.middleware_state import is_whitelisted
from src.app.core.security.rate_policy import check_security_budget
from src.app.core.utils.ip_utils import get_client_ip
from src.app.shared.exceptions import RateLimitExceededError


async def check_auth_rate_limit(request: Request) -> None:
    """Rate limit sensitive auth endpoints by client IP.

    Budget: ``auth`` in ``settings.rate_limit.SECURITY_BUDGETS``
    (default 5 attempts / 60s, previously hardcoded here).

    A whitelisted IP with IP_WHITELIST_BYPASS_RATE_LIMIT skips the budget:
    the budget is per client IP, so a load-test runner sharing one IP
    exhausts it in seconds even though every request is legitimate.
    Mirrors the same opt-in bypass the RateLimit middleware already applies
    to path policies; the brute-force defense stays on for every other IP.
    """
    if settings.security.IP_WHITELIST_BYPASS_RATE_LIMIT and is_whitelisted(request.state):
        return

    client_ip = get_client_ip(request)
    verdict = await check_security_budget("auth", client_ip)

    if verdict.is_limited:
        raise RateLimitExceededError(
            f"Too many authentication attempts. Please try again in {verdict.period} seconds.",
            retry_after=verdict.retry_after_seconds(),
        )
