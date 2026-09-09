from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps.auth import AuthenticatedUser, get_optional_user
from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import RateLimitException
from src.app.core.logger import get_logger
from src.app.core.security.rate_policy import resolve_anonymous_user_id, resolve_rate_limits
from src.app.core.utils.path_utils import sanitize_path
from src.app.core.utils.rate_limit import rate_limiter
from src.app.core.utils.user_utils import get_user_attr

logger = get_logger(__name__)

# H-9 note: the per-request FastCRUD RateLimit lookup was replaced by the
# unified rate-policy engine (resolve_rate_limits): L1 → Redis policy cache →
# DB (with cache write-back) → settings. No module-local crud lives here.


async def rate_limiter_dependency(
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    user: Annotated[AuthenticatedUser | None, Depends(get_optional_user)] = None,
) -> None:
    if hasattr(request.app.state, "initialization_complete"):
        await request.app.state.initialization_complete.wait()

    DEFAULT_LIMIT = settings.rate_limit.DEFAULT_RATE_LIMIT_LIMIT
    DEFAULT_PERIOD = settings.rate_limit.DEFAULT_RATE_LIMIT_PERIOD

    path = sanitize_path(request.url.path)
    if user:
        user_id = str(get_user_attr(user, "id"))
        tier = get_user_attr(user, "tier")
        tier_id = tier["id"] if isinstance(tier, dict) else getattr(tier, "id", None)
        tier_name = tier["name"] if isinstance(tier, dict) else getattr(tier, "name", "unknown")
        # H-9: resolve through the unified rate-policy engine — L1 cache →
        # Redis policy cache → DB (with write-back) → settings. This was a
        # per-request DB query for the tier's path limit.
        limit, period = await resolve_rate_limits(path, tier_id=tier_id, tier_name=tier_name, user_id=user_id, db=db)
    else:
        user_id = await resolve_anonymous_user_id(request)
        limit, period = DEFAULT_LIMIT, DEFAULT_PERIOD
    is_limited = await rate_limiter.is_rate_limited(db=db, user_id=user_id, path=path, limit=limit, period=period)
    if is_limited:
        raise RateLimitException("Rate limit exceeded.")
