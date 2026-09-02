from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Request
from fastapi.params import Depends as DependsClass
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import ForbiddenException, UnauthorizedException
from src.app.core.logger import get_logger
from src.app.core.security import TokenType, api_key_scheme, oauth2_scheme, verify_token
from src.app.core.security.jwt_utils import get_cached_jwt_payload
from src.app.core.tenancy.context import get_current_tenant
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules.users.service import UserService

logger = get_logger(__name__)


class AuthenticatedUser(BaseModel):
    """Strict typed user model for request dependencies.

    Provides IDE autocomplete while maintaining backward compatibility
    with dict-based access via __getitem__.
    """

    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True, from_attributes=True)

    id: int
    uuid: UUID | str | None = None
    email: str
    username: str | None = None
    is_superuser: bool = False
    is_active: bool = True
    is_deleted: bool = False
    tier_id: int | None = None
    tenant_id: UUID | str | int | None = None
    profile_image_url: str | None = None
    token_version: int = 0
    tier: dict[str, Any] | None = None
    api_key_id: str | int | None = None
    api_key_scopes: list[str] | None = None

    # M-1: dict-compat accessors used to call model_dump() on EVERY access —
    # a full serialization per lookup on the hot auth path. With extra="ignore"
    # the dump's key set is exactly the declared fields, so direct field lookup
    # (instance attrs) and model_fields (class-level) are behavior-identical
    # and allocation-free.
    def __getitem__(self, key: str) -> Any:
        if key in self.model_fields:
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.model_fields:
            return getattr(self, key)
        return default

    def keys(self) -> Any:
        return self.model_fields.keys()

    def __contains__(self, key: str) -> bool:
        return key in self.model_fields

    def __len__(self) -> int:
        return len(self.model_fields)


class AuthContext(BaseModel):
    user: AuthenticatedUser
    token_data: Any  # The token data returned from verify_token or cached JWT payload dict
    tenant: Any  # The tenant context (or None if no tenant context is available)
    is_dependency_override: bool = False


async def _create_auth_context(
    user: Any,
    token_data: Any,
    tenant: Any,
    db: AsyncSession,
    is_dependency_override: bool = False,
) -> AuthContext:
    if isinstance(user, dict):
        user_obj = AuthenticatedUser(**user)
    else:
        user_obj = user

    return AuthContext(
        user=user_obj,
        token_data=token_data,
        tenant=tenant,
        is_dependency_override=is_dependency_override,
    )


async def _is_token_blacklisted_with_fallback(token: str, jti: str | None, db: AsyncSession) -> bool:
    """Check if token is blacklisted, falling back to DB if Redis fails.

    Delegates to :func:`src.app.core.security.jwt_service.is_token_revoked`,
    the single revocation oracle that owns the fail-open/fail-closed decision:
    Redis unavailable -> DB authoritative; DB error -> fail closed (revoked),
    so a possibly-revoked token is never accepted during an outage.
    """
    from src.app.core.security.jwt_service import is_token_revoked

    return await is_token_revoked(token, jti, db)


async def _resolve_bearer_token(request: Request, token: str) -> str:
    """Pull the bearer token out of the Authorization header if oauth2_scheme didn't."""
    if token and isinstance(token, str):
        return token
    auth_header = request.headers.get("Authorization") if request is not None else None
    if auth_header:
        _, _, raw_token = auth_header.partition(" ")
        return raw_token
    return ""


def _enforce_token_length(token: str) -> None:
    """R4-02: length check at ingress (before blacklist/DB verify) -> 431.

    Prevents DoS where large tokens are decoded without verification.
    """
    if token and len(token) > 4096:
        from fastapi import HTTPException

        raise HTTPException(status_code=431, detail="Request header field too large")


async def _resolve_cached_token_data(
    request: Request,
    cached_payload: dict[str, Any] | None,
) -> tuple[Any, str, int, str | None, int | None, str | None] | None:
    """Return (token_data, username_or_email, payload_version, jti, jwt_exp, session_id) from cache, or None."""
    if not cached_payload:
        return None
    # A1: request.state.is_token_blacklisted is tri-state (True/False/None).
    # Only a DEFINITE True/False from the middleware's Redis probe
    # short-circuits; None (Redis down) or an absent attribute runs the
    # DB fallback so revoked tokens are still caught during outages.
    cached_blacklist = getattr(request.state, "is_token_blacklisted", None) if request is not None else None
    if cached_blacklist is True:
        raise UnauthorizedException("Token has been revoked.")

    token_type = cached_payload.get("token_type")
    if token_type != TokenType.ACCESS:
        raise UnauthorizedException("User not authenticated.")

    username_or_email = cached_payload.get("sub")
    if not username_or_email:
        raise UnauthorizedException("User not authenticated.")

    session_id = cached_payload.get("sid") or cached_payload.get("jti")
    return (
        cached_payload,
        username_or_email,
        cached_payload.get("token_version", 0),
        cached_payload.get("jti"),
        cached_payload.get("exp"),
        session_id,
    )


async def _resolve_verified_token_data(
    token: str,
    db: AsyncSession,
) -> tuple[Any, str, int, str | None, int | None, str | None] | None:
    """Return (token_data, username_or_email, payload_version, jti, jwt_exp, session_id) via verify_token, or None."""
    token_data = await verify_token(token, TokenType.ACCESS, db)
    if token_data is None:
        return None
    session_id = getattr(token_data, "sid", None) or getattr(token_data, "jti", None)
    return (
        token_data,
        token_data.username_or_email,
        getattr(token_data, "token_version", 0) or 0,
        getattr(token_data, "jti", None),
        getattr(token_data, "exp", None),
        session_id,
    )


async def _load_user_for_token(
    db: AsyncSession,
    username_or_email: str,
    payload_version: int,
) -> dict[str, Any]:
    """Fetch user by email/username and validate token_version + liveness."""
    user_service = UserService(db)
    if "@" in username_or_email:
        user = await user_service.get_user_auth_info(email=username_or_email)
    else:
        user = await user_service.get_user_auth_info(username=username_or_email)

    if not user:
        raise UnauthorizedException("User not authenticated.")
    if not user.get("is_active", False):
        raise UnauthorizedException("User account is no longer active.")
    if user.get("is_deleted", False):
        raise UnauthorizedException("User account has been deleted.")
    if payload_version != user.get("token_version", 0):
        raise UnauthorizedException("Session expired. Please log in again.")
    return user


async def _enforce_token_not_revoked(
    request: Request,
    token: str,
    jti: str | None,
    cached_blacklist: bool | None,
    is_blacklisted: bool | None,
    db: AsyncSession,
) -> None:
    """Tri-state blacklist check: middleware probe / composite Lua / DB fallback."""
    if cached_blacklist is False:
        return  # Verified clean by middleware probe; skip redundant checks.
    if is_blacklisted is True:
        raise UnauthorizedException("Token has been revoked.")
    if is_blacklisted is False:
        return  # Verified clean by composite Lua probe
    if cached_blacklist is True or await _is_token_blacklisted_with_fallback(token, jti, db):
        raise UnauthorizedException("Token has been revoked.")


async def _enforce_session_valid(
    session_id: str | None,
    session_valid: bool | None,
    user_id: int,
    jwt_exp: int | None,
) -> None:
    """Session validity with degraded fallback to is_session_valid when composite returned None."""
    if not session_id:
        return
    if session_valid is False:
        raise UnauthorizedException("Session has been revoked.")
    if session_valid is True:
        return  # Active per composite Lua probe
    from src.app.core.security.session_manager import session_manager

    sess_ok = await session_manager.is_session_valid(
        user_id=user_id,
        session_id=session_id,
        jwt_exp=jwt_exp,
    )
    if not sess_ok:
        raise UnauthorizedException("Session has been revoked.")


def _enforce_tenant_match(
    request: Request,
    user: dict[str, Any],
    current_tenant: Any,
) -> None:
    """Tenant isolation guard for non-superusers."""
    if not (current_tenant and not user.get("is_superuser", False)):
        return
    if str(user.get("tenant_id")) == str(current_tenant.id):
        return
    logger.warning(
        "tenant_mismatch_detected",
        user_id=user["id"],
        user_tenant_id=user.get("tenant_id"),
        requested_tenant_id=current_tenant.id,
        requested_tenant_slug=current_tenant.slug,
    )
    raise ForbiddenException("User does not belong to the current tenant.")


async def get_auth_context(
    request: Request,
    db: AsyncSession = Depends(async_get_db),
    token: str = Depends(oauth2_scheme),
) -> AuthContext:
    """Get current authenticated user's context, cached per request by FastAPI."""
    # H4 residual FIX: the manual app.dependency_overrides inspection was
    # removed — it re-implemented (per request, in production) what FastAPI
    # already does natively at the DI layer. Tests that need a synthetic
    # context override get_auth_context itself (conftest does exactly this);
    # no production code path needs to know about test overrides.
    token = await _resolve_bearer_token(request, token)
    _enforce_token_length(token)

    cached_payload = get_cached_jwt_payload(request) if request is not None else None
    resolved = await (
        _resolve_cached_token_data(request, cached_payload)
        if cached_payload
        else _resolve_verified_token_data(token, db)
    )
    if resolved is None:
        raise UnauthorizedException("User not authenticated.")
    token_data, username_or_email, payload_version, jti, jwt_exp, session_id = resolved

    user = await _load_user_for_token(db, username_or_email, payload_version)
    user_id = user["id"]
    user_tenant_id = user.get("tenant_id")

    # P1-1: Composite Auth-Prefetch Architecture (Option C: Dep composite + Middleware probe integration)
    # - If middleware probe already verified clean blacklist state (cached_blacklist is False),
    #   composite prefetch collapses session check + RBAC bitmask prefetch into 1 single RTT.
    # - If cached_blacklist is None (middleware skipped/errored), composite Lua evaluates all 3 legs atomically.
    # - Tri-state handling: On Redis error (is_blacklisted is None), falls through to DB-backed
    #   _is_token_blacklisted_with_fallback; session_valid=None falls back to is_session_valid (degraded accept).
    from src.app.core.security.auth_prefetch import execute_composite_auth_prefetch

    is_blacklisted, session_valid, bitmask = await execute_composite_auth_prefetch(
        token=token,
        jti=jti,
        user_id=user_id,
        session_id=session_id,
        tenant_id=user_tenant_id,
        check_bitmask=True,
    )

    # A1: Check if middleware already resolved a definite blacklist state
    cached_blacklist = getattr(request.state, "is_token_blacklisted", None) if request is not None else None

    await _enforce_token_not_revoked(request, token, jti, cached_blacklist, is_blacklisted, db)
    await _enforce_session_valid(session_id, session_valid, user_id, jwt_exp)

    # 3. Cache pre-fetched RBAC bitmask on request state for 0-RTT downstream checks
    if bitmask is not None and request is not None and user_tenant_id is not None:
        request.state.cached_permissions_bitmask = (user_id, str(user_tenant_id), bitmask)

    current_tenant = get_current_tenant()
    _enforce_tenant_match(request, user, current_tenant)

    return await _create_auth_context(user, token_data, current_tenant, db)


async def get_current_user(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    auth_context: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthenticatedUser:
    """Get current authenticated user by consuming get_auth_context."""
    if not token or not isinstance(token, str):
        auth_header = request.headers.get("Authorization") if request is not None else None
        if auth_header:
            _, _, token = auth_header.partition(" ")
        else:
            token = ""

    if not auth_context or not isinstance(auth_context, AuthContext):
        auth_context = await get_auth_context(request, db, token)
    return auth_context.user


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

    from src.app.core.tenancy.context import get_current_tenant

    current_tenant = get_current_tenant()
    if current_tenant:
        key_tenant_id = key_info.get("tenant_id")
        if not key_tenant_id or str(key_tenant_id) != str(current_tenant.tenant_id):
            raise UnauthorizedException("API key does not match the requested tenant context.")

    user_service = UserService(db)
    user_data = await user_service.get(key_info["user_id"])

    if not user_data:
        return None

    if not isinstance(user_data, dict):
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
        user_dict["api_key_scopes"] = key_info.get("scopes", [])
        user_dict["api_key_id"] = key_info.get("key_id")
        return AuthenticatedUser(**user_dict)  # type: ignore[arg-type]

    return None


async def _verify_optional_bearer_token(
    token_value: str,
    db: AsyncSession,
    request: Request,
    current_tenant: Any,
) -> AuthContext | None:
    cached_payload = get_cached_jwt_payload(request) if request is not None else None
    token_data: Any

    if cached_payload:
        jti = cached_payload.get("jti")
        # A1: request.state.is_token_blacklisted is tri-state (True/False/None).
        # Only a DEFINITE True/False from the middleware's Redis probe
        # short-circuits; None (Redis down) or an absent attribute runs the
        # DB fallback so revoked tokens are still caught during outages.
        # (Previously this path used getattr(..., False), which treated a
        # stored None as "not blacklisted" and skipped the DB fallback —
        # a fail-open hole. Mirrors get_auth_context's fail-closed logic.)
        cached_blacklist = getattr(request.state, "is_token_blacklisted", None)
        if cached_blacklist is False:
            pass  # Verified clean by the middleware probe; skip redundant checks.
        elif cached_blacklist is True or await _is_token_blacklisted_with_fallback(token_value, jti, db):
            return None

        token_type = cached_payload.get("token_type")
        if token_type != TokenType.ACCESS:
            return None

        username_or_email = cached_payload.get("sub")
        if not username_or_email:
            return None
        token_data = cached_payload
        payload_version = cached_payload.get("token_version", 0)
    else:
        try:
            token_data = await verify_token(token_value, TokenType.ACCESS, db)
        except Exception as e:  # noqa: BLE001
            # R1-18: verify_token returns None for invalid tokens and only
            # raises on infra errors (DB/Redis). This is the OPTIONAL auth
            # path, so degrading to anonymous is correct — but it must not be
            # silent, or an outage reads as "everyone is logged out".
            logger.warning("optional_token_verify_failed_degrading_to_anonymous", error=str(e))
            return None
        if token_data is None:
            return None

        username_or_email = token_data.username_or_email
        payload_version = getattr(token_data, "token_version", 0) or 0

    user_service = UserService(db)
    if "@" in username_or_email:
        user = await user_service.get_user_auth_info(email=username_or_email)
    else:
        user = await user_service.get_user_auth_info(username=username_or_email)

    if not user:
        return None

    if not user.get("is_active", False):
        return None
    if user.get("is_deleted", False):
        return None

    user_version = user.get("token_version", 0)
    if payload_version != user_version:
        return None

    if current_tenant:
        if str(user.get("tenant_id")) != str(current_tenant.id):
            return None

    return await _create_auth_context(user, token_data, current_tenant, db)


async def get_optional_auth_context(
    request: Request,
    db: AsyncSession = Depends(async_get_db),
    api_key_user: AuthenticatedUser | None = Depends(get_user_by_api_key),
) -> AuthContext | None:
    """Get optional auth context from API key or token, validating credentials if provided."""
    if api_key_user is None or hasattr(api_key_user, "dependency") or type(api_key_user).__name__ == "Depends":
        api_key = request.headers.get("X-API-Key") if request is not None else None
        if api_key:
            api_key_user = await get_user_by_api_key(api_key, db, request)
        else:
            api_key_user = None

    current_tenant = get_current_tenant()

    has_api_key = "X-API-Key" in request.headers if request is not None else False
    if has_api_key:
        if api_key_user is None:
            raise UnauthorizedException("Invalid API key.")
        return await _create_auth_context(api_key_user, None, current_tenant, db)

    auth_header = request.headers.get("Authorization") if request is not None else None
    if not auth_header:
        return None

    token_type_val, _, token_value = auth_header.partition(" ")
    if token_type_val.lower() != "bearer" or not token_value:
        return None

    # R4-02: length check at ingress (before blacklist/DB verify) -> 431
    if len(token_value) > 4096:
        from fastapi import HTTPException

        raise HTTPException(status_code=431, detail="Request header field too large")

    try:
        return await _verify_optional_bearer_token(token_value, db, request, current_tenant)
    except Exception as e:  # noqa: BLE001
        # R1-18: safety net for the OPTIONAL auth path. Degrading to anonymous
        # is correct here, but log it so an outage is visible rather than
        # silently reading as "everyone is logged out".
        logger.warning("optional_auth_context_verify_failed_degrading_to_anonymous", error=str(e))
        return None


async def get_optional_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    api_key_user: Annotated[AuthenticatedUser | None, Depends(get_user_by_api_key)] = None,
    auth_context: Annotated[AuthContext | None, Depends(get_optional_auth_context)] = None,
) -> AuthenticatedUser | None:
    """Get optional user from Bearer token or API Key by consuming get_optional_auth_context."""
    if (
        auth_context is None
        or isinstance(auth_context, DependsClass)
        or hasattr(auth_context, "dependency")
        or type(auth_context).__name__ == "Depends"
    ):
        auth_context = await get_optional_auth_context(request, db, api_key_user)
    if auth_context:
        return auth_context.user
    return None


def requires_api_key_scope(required_scope: str) -> Any:
    """Dependency to check if the current API key has the required scope."""

    async def dependency(
        current_user: Annotated[AuthenticatedUser | None, Depends(get_optional_user)],
    ) -> AuthenticatedUser:
        if current_user is None:
            raise UnauthorizedException("Not authenticated")

        key_id = get_user_attr(current_user, "api_key_id")
        scopes = get_user_attr(current_user, "api_key_scopes", [])

        if key_id and required_scope not in scopes:
            raise ForbiddenException(f"API key missing required scope: {required_scope}")

        return current_user

    return dependency


async def get_current_superuser(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    if not get_user_attr(current_user, "is_active", False):
        raise ForbiddenException("User account is no longer active.")
    if not get_user_attr(current_user, "is_superuser", False):
        raise ForbiddenException("You do not have enough privileges.")

    return current_user
