from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.db.database import async_get_db
from ..core.exceptions.http_exceptions import ForbiddenException, RateLimitException, UnauthorizedException
from ..core.logger import get_logger
from ..core.security import TokenType, oauth2_scheme, verify_token
from ..core.tenancy.context import get_tenant_context
from ..core.utils.rate_limit import rate_limiter
from ..modules.rate_limits.crud import crud_rate_limits
from ..modules.rate_limits.schemas import RateLimitRead, sanitize_path
from ..modules.tenants.models import Tenant
from ..modules.users.crud import crud_users

logger = get_logger(__name__)


async def get_current_user(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    """Get current authenticated user with tier eager-loaded.

    Uses joinedload to fetch user and tier in single query,
    eliminating N+1 queries in rate limit middleware.

    Optimized to reuse JWT payload if already decoded by RateLimitMiddleware,
    avoiding duplicate cryptographic operations.
    """
    # Check if JWT was already decoded by RateLimitMiddleware
    cached_payload = getattr(request.state, "jwt_payload", None)

    if cached_payload:
        # Reuse cached payload - still need to verify token isn't blacklisted
        from ..core.db.crud_token_blacklist import crud_token_blacklist
        from ..core.security.token_blacklist_cache import is_token_blacklisted

        # Check Redis cache first (O(1) lookup)
        cache_result = await is_token_blacklisted(token)
        if cache_result is True:
            raise UnauthorizedException("User not authenticated.")
        elif cache_result is None:
            # Redis unavailable, fall back to DB check
            is_blacklisted = await crud_token_blacklist.exists(db, token=token)
            if is_blacklisted:
                raise UnauthorizedException("User not authenticated.")

        # Validate token type
        token_type = cached_payload.get("token_type")
        if token_type != TokenType.ACCESS:
            raise UnauthorizedException("User not authenticated.")

        username_or_email = cached_payload.get("sub")
        if not username_or_email:
            raise UnauthorizedException("User not authenticated.")
    else:
        # No cached payload - do full verification
        token_data = await verify_token(token, TokenType.ACCESS, db)
        if token_data is None:
            raise UnauthorizedException("User not authenticated.")
        username_or_email = token_data.username_or_email

    # Use eager loading to get user with tier in single query
    if "@" in username_or_email:
        user = await crud_users.get_user_with_tier_eager(db=db, email=username_or_email)
    else:
        user = await crud_users.get_user_with_tier_eager(db=db, username=username_or_email)

    if user:
        return user

    raise UnauthorizedException("User not authenticated.")


async def get_optional_user(request: Request, db: AsyncSession = Depends(async_get_db)) -> dict | None:
    token = request.headers.get("Authorization")
    if not token:
        return None

    try:
        token_type, _, token_value = token.partition(" ")
        if token_type.lower() != "bearer" or not token_value:
            return None

        token_data = await verify_token(token_value, TokenType.ACCESS, db)
        if token_data is None:
            return None

        return await get_current_user(token_value, db=db)

    except HTTPException as http_exc:
        if http_exc.status_code != 401:
            logger.error(f"Unexpected HTTPException in get_optional_user: {http_exc.detail}")
        return None

    except Exception as exc:
        logger.error(f"Unexpected error in get_optional_user: {exc}")
        return None


async def get_current_superuser(current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
    if not current_user["is_superuser"]:
        raise ForbiddenException("You do not have enough privileges.")

    return current_user


def get_current_tenant() -> Tenant:
    """Get the current tenant from context.

    This dependency retrieves the tenant that was set by TenantMiddleware.
    Should only be used on endpoints that require tenant context (non-whitelisted).

    Returns
    -------
        The current tenant.

    Raises
    ------
        HTTPException: 500 if tenant context is not set (middleware misconfiguration).
    """
    tenant = get_tenant_context()
    if tenant is None:
        logger.error("Tenant context is None - TenantMiddleware may not be configured correctly")
        raise HTTPException(
            status_code=500,
            detail="Internal server error: tenant context not available",
        )
    return tenant


async def rate_limiter_dependency(
    request: Request, db: Annotated[AsyncSession, Depends(async_get_db)], user: dict | None = Depends(get_optional_user)
) -> None:
    if hasattr(request.app.state, "initialization_complete"):
        await request.app.state.initialization_complete.wait()

    DEFAULT_LIMIT = settings.DEFAULT_RATE_LIMIT_LIMIT
    DEFAULT_PERIOD = settings.DEFAULT_RATE_LIMIT_PERIOD

    path = sanitize_path(request.url.path)
    if user:
        user_id = user["id"]
        # Use already eager-loaded tier from get_user_with_tier_eager() - no DB query needed
        tier = user.get("tier")
        if tier:
            rate_limit = await crud_rate_limits.get(
                db=db, tier_id=tier["id"], path=path, schema_to_select=RateLimitRead
            )
            if rate_limit:
                limit, period = rate_limit["limit"], rate_limit["period"]
            else:
                logger.warning(
                    f"User {user_id} with tier '{tier['name']}' has no specific rate limit for path '{path}'. \
                        Applying default rate limit."
                )
                limit, period = DEFAULT_LIMIT, DEFAULT_PERIOD
        else:
            logger.warning(f"User {user_id} has no assigned tier. Applying default rate limit.")
            limit, period = DEFAULT_LIMIT, DEFAULT_PERIOD
    else:
        user_id = request.client.host if request.client else "unknown"
        limit, period = DEFAULT_LIMIT, DEFAULT_PERIOD

    is_limited = await rate_limiter.is_rate_limited(db=db, user_id=user_id, path=path, limit=limit, period=period)
    if is_limited:
        raise RateLimitException("Rate limit exceeded.")
