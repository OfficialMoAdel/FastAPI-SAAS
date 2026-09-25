"""Authentication rate limiting dependency.

Single seam: the security-budget check
(``check(SecurityAction.AUTH, ...)``). Legs, keys, limits, algorithm choice
(GCRA/fixed-window), fail-closed policy, and the refusal all live in the seam;
this module only supplies the identity fact (client IP).
"""

from fastapi import Request

from src.app.core.config import settings
from src.app.core.middleware_state import is_whitelisted
from src.app.core.security.budgets import Identity, SecurityAction, check
from src.app.core.utils.ip_utils import get_client_ip


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
    await check(SecurityAction.AUTH, Identity(ip=client_ip))
