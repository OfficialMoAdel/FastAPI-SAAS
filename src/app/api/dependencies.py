from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import ForbiddenException, RateLimitException, UnauthorizedException
from src.app.core.logger import get_logger
from src.app.core.security import TokenType, api_key_scheme, oauth2_scheme, verify_token
from src.app.core.security.jwt_utils import get_cached_jwt_payload
from src.app.core.tenancy.context import get_current_tenant as get_tenant_context
from src.app.core.utils.rate_limit import rate_limiter
from src.app.modules.rate_limits.crud import crud_rate_limits
from src.app.modules.rate_limits.schemas import RateLimitRead, sanitize_path
from src.app.modules.users.service import UserService

logger = get_logger(__name__)


class AuthenticatedUser(BaseModel):
    """Strict typed user model for request dependencies.

    Provides IDE autocomplete while maintaining backward compatibility
    with dict-based access via __getitem__.
    """

    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    id: int
    uuid: UUID | str | None = None
    email: str
    username: str | None = None
    is_superuser: bool = False
    is_active: bool = True
    is_deleted: bool = False
    tier_id: int | None = None
    tenant_id: UUID | str | None = None
    profile_image_url: str | None = None
    token_version: int = 0

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


async def _is_token_blacklisted_with_fallback(token: str, jti: str | None, db: AsyncSession) -> bool:
    """Check if token is blacklisted, falling back to DB if Redis fails."""
    from src.app.core.db.crud_token_blacklist import crud_token_blacklist
    from src.app.core.security.token_blacklist_cache import is_token_blacklisted

    cache_result = await is_token_blacklisted(token)
    if cache_result is True:
        return True
    if cache_result is None:
        if jti and await crud_token_blacklist.exists(db, token=f"jti:{jti}"):
            return True
        if await crud_token_blacklist.exists(db, token=token):
            return True
    return False


async def get_current_user(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> AuthenticatedUser:
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
        jti = cached_payload.get("jti")
        if await _is_token_blacklisted_with_fallback(token, jti, db):
            raise UnauthorizedException("Token has been revoked.")

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
        if current_tenant:
            if str(user.get("tenant_id")) != str(current_tenant.id):
                logger.warning(
                    "tenant_mismatch_detected",
                    user_id=user["id"],
                    user_tenant_id=user.get("tenant_id"),
                    requested_tenant_id=current_tenant.id,
                    requested_tenant_slug=current_tenant.slug,
                )
                raise ForbiddenException("User does not belong to the current tenant.")

        if not user.get("is_active", False):
            raise UnauthorizedException("User account is no longer active.")
        if user.get("is_deleted", False):
            raise UnauthorizedException("User account has been deleted.")

        return AuthenticatedUser(**user)

    raise UnauthorizedException("User not authenticated.")


async def get_user_by_api_key(
    api_key: Annotated[str | None, Depends(api_key_scheme)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    request: Request,
) -> AuthenticatedUser | None:
    """Validate API key and return user info.

    This dependency allows authentication using 'X-API-Key' header.
    Returns user dict if valid, None otherwise.
    """
    if not api_key:
        return None

    from src.app.modules.api_keys.service import APIKeyService

    api_key_service = APIKeyService(db)
    key_info = await api_key_service.validate_api_key(api_key)

    if not key_info:
        return None

    # ISSUE 5 FIX: Verify tenant from API key matches the requested tenant context.
    # Prevents an attacker from using a valid API key for Tenant A to access Tenant B.
    from src.app.core.tenancy.context import get_current_tenant

    current_tenant = get_current_tenant()
    if current_tenant:
        key_tenant_id = key_info.get("tenant_id")
        if not key_tenant_id or str(key_tenant_id) != str(current_tenant.tenant_id):
            from src.app.core.exceptions.http_exceptions import UnauthorizedException

            raise UnauthorizedException("API key does not match the requested tenant context.")

    # Fetch full user auth info
    user_service = UserService(db)
    user_data = await user_service.get(key_info["user_id"])

    if not user_data:
        return None

    # Ensure we have a dict
    if not isinstance(user_data, dict):
        # Convert ORM object to dict if needed
        # This is a simplified conversion for security context
        user_dict = {
            "id": user_data.id,
            "username": user_data.username,
            "email": user_data.email,
            "tenant_id": user_data.tenant_id,
            "is_active": user_data.is_active,
            "is_superuser": user_data.is_superuser,
            "token_version": user_data.token_version,
        }
    else:
        user_dict = user_data

    if user_dict.get("is_active", True) and not user_dict.get("is_deleted", False):
        # Attach API key specific info to the user object
        user_dict["api_key_scopes"] = key_info.get("scopes", [])
        user_dict["api_key_id"] = key_info.get("key_id")
        return AuthenticatedUser(**user_dict)  # type: ignore[arg-type]

    return None


async def get_optional_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    api_key_user: Annotated[AuthenticatedUser | None, Depends(get_user_by_api_key)] = None,
) -> AuthenticatedUser | None:
    """Get optional user from Bearer token or API Key.

    Try API key first (via api_key_user dependency), then fall back to Bearer token.
    """
    if api_key_user:
        return api_key_user

    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None

    try:
        token_type, _, token_value = auth_header.partition(" ")
        if token_type.lower() != "bearer" or not token_value:
            return None

        # Check if JWT was already decoded by RateLimitMiddleware using jwt_utils
        from src.app.core.security.jwt_utils import get_cached_jwt_payload

        cached_payload = get_cached_jwt_payload(request)

        if cached_payload:
            # SECURITY FIX: Need to check blacklist even if cached
            jti = cached_payload.get("jti")
            if await _is_token_blacklisted_with_fallback(token_value, jti, db):
                return None

            username_or_email = cached_payload.get("sub")
            # BUG FIX: Use .get() with explicit default 0 — matches the non-cached path below.
            payload_version = cached_payload.get("token_version", 0)
            if not username_or_email:
                return None
        else:
            token_data = await verify_token(token_value, TokenType.ACCESS, db)
            if token_data is None:
                return None
            username_or_email = token_data.username_or_email
            # BUG FIX: Unified extraction — same default 0 as the cached path above.
            # Previously used `hasattr` + `is not None` check which is subtly different:
            # if token_version is explicitly None, hasattr returns True but result differed.
            payload_version = getattr(token_data, "token_version", None) or 0

        # Use username_or_email directly instead of calling get_current_user
        # which would re-verify the token (2nd Redis + JWT decode)
        user_service = UserService(db)
        if "@" in username_or_email:
            user = await user_service.get_user_auth_info(email=username_or_email)
        else:
            user = await user_service.get_user_auth_info(username=username_or_email)

        if not user:
            return None

        # SECURITY FIX: Check is_active — suspended users must not access optional-auth routes.
        # Use False as the secure default so a missing field denies access.
        if not user.get("is_active", False):
            return None

        # SECURITY FIX: Validate token_version to reject revoked sessions.
        # Mirrors the check performed by get_current_user so that a password change
        # or forced logout also invalidates tokens on optional-auth routes.
        user_version = user.get("token_version", 0)
        if payload_version != user_version:
            return None

        # Verify tenant context
        current_tenant = get_tenant_context()
        if current_tenant and not user.get("is_superuser"):
            if str(user.get("tenant_id")) != str(current_tenant.id):
                return None

        return AuthenticatedUser(**user)  # type: ignore[arg-type]

    except HTTPException as http_exc:
        if http_exc.status_code != 401:
            logger.error("Unexpected HTTPException in get_optional_user:", detail=http_exc.detail)
        return None

    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, jwt.DecodeError, jwt.InvalidSignatureError):
        # JWT validation errors - return None for optional authentication
        return None


def requires_api_key_scope(required_scope: str) -> Any:
    """Dependency to check if the current API key has the required scope.

    If the user authenticated via Bearer token (not API key), they bypass this check.
    If they authenticated via API key, the key must have the requested scope.
    """
    def dependency(current_user: Annotated[AuthenticatedUser, Depends(get_current_user)]) -> AuthenticatedUser:
        key_id = current_user.get("api_key_id")
        scopes = current_user.get("api_key_scopes", [])

        if key_id and required_scope not in scopes:
            raise ForbiddenException(f"API key missing required scope: {required_scope}")

        return current_user
    return dependency


async def get_current_superuser(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    if not current_user.get("is_active", False):
        raise ForbiddenException("User account is no longer active.")
    if not current_user.get("is_superuser", False):
        raise ForbiddenException("You do not have enough privileges.")

    return current_user


from src.app.core.tenancy.context import TenantContext


def get_current_tenant() -> TenantContext:

    """Get the current tenant from context.

    This dependency retrieves the tenant that was set by TenantMiddleware.
    Should only be used on endpoints that require tenant context (non-whitelisted).

    Returns
    -------
        The current tenant context.

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
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    user: AuthenticatedUser | None = Depends(get_optional_user),
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
                    "path_rate_limit_missing_using_default",
                    user_id=user_id,
                    tier_name=tier["name"],
                    path=path,
                )
                limit, period = DEFAULT_LIMIT, DEFAULT_PERIOD
        else:
            logger.warning("User has no assigned tier. Applying default rate limit.", user_id=user_id)
            limit, period = DEFAULT_LIMIT, DEFAULT_PERIOD
    else:
        # Use a unique identifier per request when client info is unavailable
        # to prevent DoS attacks on the shared "unknown" bucket
        if request.client and request.client.host:
            # ISSUE 7 FIX: Use per-IP bucket for unknown clients
            user_id = f"unknown:{request.client.host}"
        else:
            # Drop the UUID so that unknown clients all share the same bucket
            # and effectively get rate limited.
            user_id = "unknown"
        limit, period = DEFAULT_LIMIT, DEFAULT_PERIOD

    is_limited = await rate_limiter.is_rate_limited(db=db, user_id=user_id, path=path, limit=limit, period=period)
    if is_limited:
        raise RateLimitException("Rate limit exceeded.")
