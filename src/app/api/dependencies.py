from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import ForbiddenException, RateLimitException, UnauthorizedException
from src.app.core.logger import get_logger
from src.app.core.security import TokenType, oauth2_scheme, verify_token
from src.app.core.security.jwt_utils import get_cached_jwt_payload
from src.app.core.tenancy.context import get_current_tenant as get_tenant_context
from src.app.core.utils.rate_limit import rate_limiter
from src.app.modules.rate_limits.crud import crud_rate_limits
from src.app.modules.rate_limits.schemas import RateLimitRead, sanitize_path
from src.app.modules.users.service import UserService

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
    # Check if JWT was already decoded by RateLimitMiddleware using jwt_utils
    cached_payload = get_cached_jwt_payload(request)

    if cached_payload:
        # Reuse cached payload - still need to verify token isn't blacklisted
        from src.app.core.db.crud_token_blacklist import crud_token_blacklist
        from src.app.core.security.token_blacklist_cache import is_token_blacklisted

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

    # Use UserService with caching (P1-2 optimization)
    user_service = UserService(db)
    if "@" in username_or_email:
        user = await user_service.get_user_auth_info(email=username_or_email)
    else:
        user = await user_service.get_user_auth_info(username=username_or_email)

    if user:
        # SECURITY FIX: Verify token_version if using cached payload
        # This ensures revoked sessions (e.g. password change) are rejected even if JWT is cached
        if cached_payload:
            payload_version = cached_payload.get("token_version", 0)
            user_version = user.get("token_version", 0)
            if payload_version != user_version:
                raise UnauthorizedException("Session expired. Please log in again.")

        # Verify user belongs to current tenant context
        current_tenant = get_tenant_context()
        if current_tenant and user["tenant_id"] != current_tenant.id:
            if not user.get("is_superuser"):
                # Convert to UUID for comparison (JWT tenant_id is string)
                user_tenant_id = user["tenant_id"]
                current_tenant_id = current_tenant.id
                # Handle both UUID and string comparisons
                if str(user_tenant_id) != str(current_tenant_id):
                    logger.warning(
                        f"Tenant mismatch: User {user['id']} (tenant {user['tenant_id']}) "
                        f"requested access to tenant {current_tenant.id} ({current_tenant.slug})"
                    )
                    raise ForbiddenException("User does not belong to the current tenant.")

        if not user.get("is_active", True):
            raise UnauthorizedException("User account is no longer active.")

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

        # Use token_data directly instead of calling get_current_user
        # which would re-verify the token (2nd Redis + JWT decode)
        user_service = UserService(db)
        if "@" in token_data.username_or_email:
            user = await user_service.get_user_auth_info(email=token_data.username_or_email)
        else:
            user = await user_service.get_user_auth_info(username=token_data.username_or_email)

        if not user:
            return None

        # Verify tenant context
        current_tenant = get_tenant_context()
        if current_tenant and user["tenant_id"] != current_tenant.id:
            if not user.get("is_superuser"):
                return None

        return user

    except HTTPException as http_exc:
        if http_exc.status_code != 401:
            logger.error(f"Unexpected HTTPException in get_optional_user: {http_exc.detail}")
        return None

    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, jwt.DecodeError, jwt.InvalidSignatureError):
        # JWT validation errors - return None for optional authentication
        return None

    except Exception as exc:
        # Log unexpected errors but don't break the request for optional auth
        logger.error(f"Unexpected error in get_optional_user: {exc}")
        return None


async def get_current_superuser(current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
    if not current_user.get("is_active", True):
        raise ForbiddenException("User account is no longer active.")
    if not current_user["is_superuser"]:
        raise ForbiddenException("You do not have enough privileges.")

    return current_user


def get_current_tenant() -> Any:
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
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        # Use a unique identifier per request when client info is unavailable
        # to prevent DoS attacks on the shared "unknown" bucket
        if request.client and request.client.host:
            user_id = request.client.host
        else:
            # Generate a temporary unique identifier for this request
            # This prevents attackers from exhausting a shared bucket
            import uuid

            user_id = f"unknown_{uuid.uuid4().hex[:8]}"
        limit, period = DEFAULT_LIMIT, DEFAULT_PERIOD

    is_limited = await rate_limiter.is_rate_limited(db=db, user_id=user_id, path=path, limit=limit, period=period)
    if is_limited:
        raise RateLimitException("Rate limit exceeded.")
