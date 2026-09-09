from typing import Annotated, Any, Literal, NoReturn
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import ForbiddenException, UnauthorizedException
from src.app.core.logger import get_logger
from src.app.core.schemas import TokenData
from src.app.core.security import TokenType, api_key_scheme, oauth2_scheme, verify_token
from src.app.core.security.jwt_utils import get_cached_jwt_payload
from src.app.core.tenancy.context import TenantContext, get_current_tenant
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

    # Q-M2: dict-compat shim (__getitem__/get/keys/__contains__/__len__)
    # deleted. All call sites use attribute access (or get_user_attr for
    # dict|model unions). Do NOT re-add subscript access — it hid type
    # errors on the hottest auth path.


class AuthContext(BaseModel):
    user: AuthenticatedUser
    # Q-M7: token_data is either the TokenData object returned by verify_token
    # (core.schemas.TokenData) or the raw cached JWT payload dict (same claim
    # fields as keys); tenant is the lightweight TenantContext (or None).
    token_data: TokenData | dict[str, Any] | None
    tenant: TenantContext | None
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


# ---------------------------------------------------------------------------
# H-7: shared auth primitives. One implementation, parameterized by
# on_failure: "raise" surfaces the domain exception (strict path);
# "return_none" raises a silent _OptionalAuthAbort that ONLY the optional
# flow converts to None (degrade to anonymous). This kills the drift risk of
# maintaining two parallel user-loading / session-validation / tenant-
# matching stacks — the logic is shared; only the failure PLUMBING differs.
# ---------------------------------------------------------------------------


class _OptionalAuthAbort(Exception):
    """Internal control-flow signal for the optional path (H-7).

    Raised by shared primitives in return_none mode; caught ONLY by
    _resolve_optional_bearer_flow and converted to None. Never escapes.
    """


_FailureMode = Literal["raise", "return_none"]


def _auth_fail(on_failure: _FailureMode, message: str, exc: type[Exception] = UnauthorizedException) -> NoReturn:
    """Signal an auth failure per the caller's failure mode.

    Always raises (domain exception in raise mode, _OptionalAuthAbort in
    return_none mode) — typed NoReturn so mypy knows callers never see a
    normal return and downstream code is unreachable after a failure.
    """
    if on_failure == "raise":
        raise exc(message)
    logger.debug("optional_auth_degraded_to_anonymous", reason=message)
    raise _OptionalAuthAbort(message)


async def _resolve_token_data(
    request: Request | None,
    token: str,
    cached_payload: dict[str, Any] | None,
    db: AsyncSession,
    on_failure: _FailureMode,
) -> tuple[Any, str, int, str | None, int | None, str | None]:
    """Shared token-data resolution: cached payload or verify_token.

    Returns (token_data, username_or_email, payload_version, jti, jwt_exp,
    session_id).
    """
    if cached_payload:
        cached_blacklist = getattr(request.state, "is_token_blacklisted", None) if request is not None else None
        if cached_blacklist is False:
            pass  # Verified clean by middleware probe
        elif cached_blacklist is True or await _is_token_blacklisted_with_fallback(
            token, cached_payload.get("jti"), db
        ):
            _auth_fail(on_failure, "Token has been revoked.")

        if cached_payload.get("token_type") != TokenType.ACCESS:
            _auth_fail(on_failure, "User not authenticated.")
        username_or_email = cached_payload.get("sub")
        if not username_or_email:
            _auth_fail(on_failure, "User not authenticated.")
        return (
            cached_payload,
            username_or_email,
            cached_payload.get("token_version", 0),
            cached_payload.get("jti"),
            cached_payload.get("exp"),
            cached_payload.get("sid") or cached_payload.get("jti"),
        )

    # No cached payload — verify the token directly. The optional path
    # degrades verify_token errors to anonymous rather than re-raising.
    try:
        token_data = await verify_token(token, TokenType.ACCESS, db)
    except Exception as e:
        if on_failure == "raise":
            raise
        logger.warning("optional_token_verify_failed_degrading_to_anonymous", error=str(e))
        raise _OptionalAuthAbort("token_verify_failed") from e
    if token_data is None:
        _auth_fail(on_failure, "User not authenticated.")
    return (
        token_data,
        token_data.username_or_email,
        getattr(token_data, "token_version", 0) or 0,
        getattr(token_data, "jti", None),
        getattr(token_data, "exp", None),
        getattr(token_data, "sid", None) or getattr(token_data, "jti", None),
    )


async def _load_and_validate_user(
    db: AsyncSession,
    username_or_email: str,
    payload_version: int,
    on_failure: _FailureMode,
) -> dict[str, Any]:
    """Shared user loading: fetch by email/username, validate liveness + version."""
    user_service = UserService(db)
    if "@" in username_or_email:
        user = await user_service.get_user_auth_info(email=username_or_email)
    else:
        user = await user_service.get_user_auth_info(username=username_or_email)

    if not user:
        _auth_fail(on_failure, "User not authenticated.")
    if not user.get("is_active", False):
        _auth_fail(on_failure, "User account is no longer active.")
    if user.get("is_deleted", False):
        _auth_fail(on_failure, "User account has been deleted.")
    if payload_version != user.get("token_version", 0):
        _auth_fail(on_failure, "Session expired. Please log in again.")
    return user


async def _enforce_session_validity(
    session_id: str | None,
    user_id: Any,
    jwt_exp: int | None,
    on_failure: _FailureMode,
) -> None:
    """Shared session-validity check (revoked sid → failure)."""
    if not session_id:
        return
    try:
        from src.app.core.security.session_manager import session_manager

        sess_ok = await session_manager.is_session_valid(user_id=user_id, session_id=session_id, jwt_exp=jwt_exp)
    except Exception as e:
        if on_failure == "raise":
            raise
        logger.warning("optional_session_check_failed_degrading_to_anonymous", error=str(e))
        raise _OptionalAuthAbort("session_check_failed") from e
    if not sess_ok:
        _auth_fail(on_failure, "Session has been revoked.")


def _enforce_user_tenant_match(
    user: dict[str, Any],
    current_tenant: Any,
    on_failure: _FailureMode,
) -> None:
    """Shared tenant-mismatch check (superusers bypass)."""
    if not current_tenant or user.get("is_superuser", False):
        return
    user_tid = str(user.get("tenant_id")) if user.get("tenant_id") is not None else None
    curr_tid = str(getattr(current_tenant, "id", getattr(current_tenant, "tenant_id", None)))
    if user_tid != curr_tid:
        if on_failure == "raise":
            logger.warning(
                "tenant_mismatch_detected",
                user_id=user["id"],
                user_tenant_id=user.get("tenant_id"),
                requested_tenant_id=current_tenant.id,
                requested_tenant_slug=current_tenant.slug,
            )
            raise ForbiddenException("User does not belong to the current tenant.")
        logger.debug("optional_auth_degraded_to_anonymous", reason="tenant_mismatch")
        raise _OptionalAuthAbort("tenant_mismatch")


async def _resolve_optional_bearer_flow(
    request: Request,
    db: AsyncSession,
    token: str,
    cached_payload: dict[str, Any] | None,
    current_tenant: Any,
) -> AuthContext | None:
    """Non-strict (optional auth) path: returns None on any verification failure.

    H-7: runs the SAME primitives as the strict path with
    on_failure="return_none"; their _OptionalAuthAbort signals degrade here.
    """
    try:
        resolved = await _resolve_token_data(request, token, cached_payload, db, on_failure="return_none")
        token_data, username_or_email, payload_version, _jti, jwt_exp, session_id = resolved

        user = await _load_and_validate_user(db, username_or_email, payload_version, on_failure="return_none")

        await _enforce_session_validity(session_id, user.get("id"), jwt_exp, on_failure="return_none")
        _enforce_user_tenant_match(user, current_tenant, on_failure="return_none")

        return await _create_auth_context(user, token_data, current_tenant, db)
    except _OptionalAuthAbort:
        return None
    except Exception as e:
        logger.warning("optional_auth_context_verify_failed_degrading_to_anonymous", error=str(e))
        return None


async def resolve_principal(
    request: Request,
    db: AsyncSession,
    token: str | None = None,
    api_key_user: AuthenticatedUser | None = None,
    *,
    strict: bool = True,
    current_tenant: Any = None,
) -> AuthContext | None:
    """Unified engine to resolve and verify an authenticated principal.

    This is the ONE resolve-principal seam (architecture candidate 2) unifying:
    - required Bearer token auth (`get_auth_context`)
    - optional Bearer token auth (`_verify_optional_bearer_token`)
    - API key auth (`get_user_by_api_key`)
    - optional auth dispatcher (`get_optional_auth_context`)

    Parameters:
        request: FastAPI/ASGI Request
        db: Database session
        token: Bearer token string (if known)
        api_key_user: Pre-validated AuthenticatedUser from API key (if any)
        strict: When True, raises on any invalid/revoked state. When False,
                returns None (degrading to anonymous).
        current_tenant: Tenant context (auto-resolved from context if omitted).
    """
    if current_tenant is None:
        current_tenant = get_current_tenant()

    # 1. API-Key flavor
    has_api_key = "X-API-Key" in request.headers if request is not None else False
    if has_api_key:
        if api_key_user is None or not isinstance(api_key_user, AuthenticatedUser):
            api_key = request.headers.get("X-API-Key") if request is not None else None
            if api_key:
                api_key_user = await get_user_by_api_key(api_key, db, request)
        if api_key_user is None:
            raise UnauthorizedException("Invalid API key.")
        return await _create_auth_context(api_key_user, None, current_tenant, db)

    # 2. Bearer Token extraction & length check
    if token is None or not token:
        token = await _resolve_bearer_token(request, token or "")
    if not token:
        if strict:
            raise UnauthorizedException("User not authenticated.")
        return None

    _enforce_token_length(token)

    # 3. Resolve token data (cached payload or verify_token)
    cached_payload = get_cached_jwt_payload(request) if request is not None else None

    if strict:
        # H-7: the strict path now consumes the SAME primitives as the
        # optional path (on_failure="raise"). Steps 3-6 collapse into the
        # shared implementations; the composite prefetch stays inline
        # because it is strict-only (RBAC bitmask warm).
        resolved = await _resolve_token_data(request, token, cached_payload, db, on_failure="raise")
        if resolved is None:
            raise UnauthorizedException("User not authenticated.")
        token_data, username_or_email, payload_version, jti, jwt_exp, session_id = resolved

        # 4. User loading & verification
        user = await _load_and_validate_user(db, username_or_email, payload_version, on_failure="raise")
        if user is None:
            raise UnauthorizedException("User not authenticated.")
        user_id = user["id"]
        user_tenant_id = user.get("tenant_id")

        # 5. Composite auth-prefetch & revocation enforcement
        from src.app.core.security.auth_prefetch import execute_composite_auth_prefetch

        is_blacklisted, session_valid, bitmask = await execute_composite_auth_prefetch(
            token=token,
            jti=jti,
            user_id=user_id,
            session_id=session_id,
            tenant_id=user_tenant_id,
            check_bitmask=True,
        )

        cached_blacklist = getattr(request.state, "is_token_blacklisted", None) if request is not None else None
        await _enforce_token_not_revoked(request, token, jti, cached_blacklist, is_blacklisted, db)
        await _enforce_session_valid(session_id, session_valid, user_id, jwt_exp)

        if bitmask is not None and request is not None and user_tenant_id is not None:
            request.state.cached_permissions_bitmask = (user_id, str(user_tenant_id), bitmask)

        # 6. Tenant isolation check
        _enforce_user_tenant_match(user, current_tenant, on_failure="raise")

        return await _create_auth_context(user, token_data, current_tenant, db)

    else:
        return await _resolve_optional_bearer_flow(request, db, token, cached_payload, current_tenant)


async def get_auth_context(
    request: Request,
    db: AsyncSession = Depends(async_get_db),
    token: str = Depends(oauth2_scheme),
) -> AuthContext:
    """Get current authenticated user's context, cached per request by FastAPI."""
    ctx = await resolve_principal(request, db, token=token, strict=True)
    assert ctx is not None
    return ctx


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
    current_tenant: Any = None,
) -> AuthContext | None:
    """Verify optional bearer token using the unified resolve_principal seam."""
    return await resolve_principal(
        request=request,
        db=db,
        token=token_value,
        strict=False,
        current_tenant=current_tenant,
    )


async def get_optional_auth_context(
    request: Request,
    db: AsyncSession = Depends(async_get_db),
    api_key_user: AuthenticatedUser | None = Depends(get_user_by_api_key),
) -> AuthContext | None:
    """Get optional auth context from API key or token, validating credentials if provided."""
    return await resolve_principal(request, db, api_key_user=api_key_user, strict=False)


async def get_optional_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    api_key_user: Annotated[AuthenticatedUser | None, Depends(get_user_by_api_key)] = None,
    auth_context: Annotated[AuthContext | None, Depends(get_optional_auth_context)] = None,
) -> AuthenticatedUser | None:
    """Get optional user from Bearer token or API Key by consuming get_optional_auth_context."""
    if not isinstance(auth_context, AuthContext):
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
