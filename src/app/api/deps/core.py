from typing import Annotated

from fastapi import Depends, Request
from fastcrud import FastCRUD
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps.auth import AuthenticatedUser, get_optional_user
from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import RateLimitException
from src.app.core.logger import get_logger
from src.app.core.utils.ip_utils import get_client_ip
from src.app.core.utils.path_utils import sanitize_path
from src.app.core.utils.rate_limit import rate_limiter
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules.rate_limits.models import RateLimit
from src.app.modules.rate_limits.schemas import RateLimitRead

# RateLimit has no tenant_id column, so a plain (non-tenant-scoped) FastCRUD is
# correct here. Local stateless instance replaces the deleted rate_limits/crud.py
# module global.
crud_rate_limits: FastCRUD[RateLimit, None, None, None, None, RateLimitRead] = FastCRUD(RateLimit)  # type: ignore[type-var]

logger = get_logger(__name__)

async def rate_limiter_dependency(
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    user: Annotated[AuthenticatedUser | None, Depends(get_optional_user)] = None,
) -> None:
    if hasattr(request.app.state, "initialization_complete"):
        await request.app.state.initialization_complete.wait()

    DEFAULT_LIMIT = settings.DEFAULT_RATE_LIMIT_LIMIT
    DEFAULT_PERIOD = settings.DEFAULT_RATE_LIMIT_PERIOD

    path = sanitize_path(request.url.path)
    if user:
        user_id = str(get_user_attr(user, "id"))
        tier = get_user_attr(user, "tier")
        if tier:
            tier_id = tier["id"] if isinstance(tier, dict) else getattr(tier, "id", None)
            tier_name = tier["name"] if isinstance(tier, dict) else getattr(tier, "name", "unknown")
            rate_limit = await crud_rate_limits.get(
                db=db, tier_id=tier_id, path=path, schema_to_select=RateLimitRead
            )
            if rate_limit:
                limit, period = rate_limit["limit"], rate_limit["period"]
            else:
                logger.warning(
                    "path_rate_limit_missing_using_default",
                    user_id=user_id,
                    tier_name=tier_name,
                    path=path,
                )
                limit, period = DEFAULT_LIMIT, DEFAULT_PERIOD
        else:
            logger.warning("User has no assigned tier. Applying default rate limit.", user_id=user_id)
            limit, period = DEFAULT_LIMIT, DEFAULT_PERIOD
    else:
        # Try to extract email from request body for per-email rate limiting
        email_hash = None
        if request.method == "POST":
            try:
                body = await request.json()
                if "email" in body:
                    import hashlib
                    email_hash = hashlib.sha256(body["email"].encode()).hexdigest()[:16]
            except Exception:
                pass

        if email_hash:
            user_id = f"email:{email_hash}"
        else:
            user_id = f"unknown:{get_client_ip(request)}"
        limit, period = DEFAULT_LIMIT, DEFAULT_PERIOD

    is_limited = await rate_limiter.is_rate_limited(db=db, user_id=user_id, path=path, limit=limit, period=period)
    if is_limited:
        raise RateLimitException("Rate limit exceeded.")
