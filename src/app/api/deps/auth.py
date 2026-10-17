from typing import Annotated, Any, NamedTuple

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.logger import get_logger
from src.app.core.principal import (
    OPTIONAL,
    REQUIRED,
    AuthContext,
    AuthenticatedUser,  # noqa: F401 — canonical home; re-exported for legacy importers
    DenialReason,
    PrincipalPolicy,
    PrincipalPresence,
    denial,
    get_auth_context,  # noqa: F401 — re-exported; the dependency itself lives in core
    register_principal_provider,
)
from src.app.core.rbac.superuser_tenant import enforce_superuser_tenant_rule
from src.app.core.security import TokenType, api_key_scheme, oauth2_scheme, verify_token
from src.app.core.security.jwt_utils import get_cached_jwt_payload
from src.app.core.tenancy.context import NO_TENANT, get_current_tenant
from src.app.modules.users import UserDirectory
from src.app.shared.exceptions import ForbiddenError as ForbiddenException
from src.app.shared.exceptions import TenantContextError
from src.app.shared.exceptions import UnauthorizedError as UnauthorizedException

logger = get_logger(__name__)

# Q-M7: ``AuthContext.token_data`` is either the TokenData object returned by
# verify_token (core.schemas.TokenData) or the raw cached JWT payload dict
# (same claim fields as keys); ``tenant`` is the lightweight TenantContext
# (or None). The model itself now lives in ``core.principal`` — the ONE
# principal seam — and is re-exported above.
#
# Every refusal below is raised through ``core.principal.denial``, so the
# status a given condition produces is decided in ONE table rather than at
# each raise site. The messages are byte-identical to the hand-written ones
# they replaced.


def _create_auth_context(
    user: Any,
    token_data: Any,
    tenant: Any,
    db: AsyncSession,
) -> AuthContext:
    if isinstance(user, dict):
        user_obj = AuthenticatedUser(**user)
    else:
        user_obj = user

    return AuthContext(
        user=user_obj,
        token_data=token_data,
        tenant=tenant,
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
    """Pull the bearer token out of the Authorization header if oauth2_scheme didn't.

    C3: only the ``Bearer`` scheme yields a token. Any other presented
    scheme (``Basic``, ``Digest``, ...) is fail-loud (401) rather than
    being sliced into a pseudo-token that then degrades to anonymous.
    """
    if token and isinstance(token, str):
        return token
    auth_header = request.headers.get("Authorization") if request is not None else None
    if auth_header:
        scheme, _, raw_token = auth_header.partition(" ")
        if not scheme or scheme.lower() != "bearer":
            raise denial(DenialReason.NO_CREDENTIAL)
        return raw_token
    return ""


def _enforce_token_length(token: str) -> None:
    """R4-02: length check at ingress (before blacklist/DB verify) -> 431.

    Prevents DoS where large tokens are decoded without verification.
    """
    if token and len(token) > 4096:
        from fastapi import HTTPException

        raise HTTPException(status_code=431, detail="Request header field too large")


class _BlacklistProbe(NamedTuple):
    """Bundled tri-state revocation inputs (strict path → _enforce_token_not_revoked)."""

    token: str
    jti: str | None
    cached_blacklist: bool | None
    is_blacklisted: bool | None


async def _enforce_token_not_revoked(
    probe: _BlacklistProbe,
    db: AsyncSession,
) -> None:
    """Tri-state blacklist check: middleware probe / composite Lua / DB fallback."""
    if probe.cached_blacklist is False:
        return  # Verified clean by middleware probe; skip redundant checks.
    if probe.is_blacklisted is True:
        raise denial(DenialReason.REVOKED_TOKEN)
    if probe.is_blacklisted is False:
        return  # Verified clean by composite Lua probe
    if probe.cached_blacklist is True or await _is_token_blacklisted_with_fallback(
        probe.token, probe.jti, db
    ):
        raise denial(DenialReason.REVOKED_TOKEN)


async def _enforce_session_valid(
    session_id: str | None,
    session_valid: bool | None,
    user_id: Any,
    jwt_exp: int | None,
) -> None:
    """Session validity with degraded fallback to is_session_valid when composite returned None."""
    if not session_id:
        return
    if session_valid is False:
        raise denial(DenialReason.REVOKED_SESSION)
    if session_valid is True:
        return  # Active per composite Lua probe
    from src.app.core.security.session_manager import session_manager

    sess_ok = await session_manager.is_session_valid(
        user_id=user_id,
        session_id=session_id,
        jwt_exp=jwt_exp,
    )
    if not sess_ok:
        raise denial(DenialReason.REVOKED_SESSION)


# ---------------------------------------------------------------------------
# H-7/C3: shared auth primitives. ONE implementation for both the strict and
# the optional path — the only difference is the optional path returns None
# when NO credential was presented (handled by the caller); every failure on
# a PRESENTED credential raises the domain exception (401/403).
# ---------------------------------------------------------------------------


async def _resolve_token_data(
    request: Request | None,
    token: str,
    cached_payload: dict[str, Any] | None,
    db: AsyncSession,
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
            raise denial(DenialReason.REVOKED_TOKEN)

        if cached_payload.get("token_type") != TokenType.ACCESS:
            raise denial(DenialReason.NO_CREDENTIAL)
        username_or_email = cached_payload.get("sub")
        if not username_or_email:
            raise denial(DenialReason.NO_CREDENTIAL)
        return (
            cached_payload,
            username_or_email,
            cached_payload.get("token_version", 0),
            cached_payload.get("jti"),
            cached_payload.get("exp"),
            cached_payload.get("sid") or cached_payload.get("jti"),
        )

    # No cached payload — verify the token directly. verify_token failures
    # propagate: a presented token that cannot be verified is never anonymous.
    token_data = await verify_token(token, TokenType.ACCESS, db)
    if token_data is None:
        raise denial(DenialReason.NO_CREDENTIAL)
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
) -> dict[str, Any]:
    """Shared user loading: fetch by email/username, validate liveness + version.

    The token_version comparison is the SOLE enforcement for the cached-
    payload path, which skips verify_token. For the verified path it is a
    cheap same-row re-check (harmless duplicate of verify_token's verdict).
    The registry inversion in jwt_service is untouched (PR-12 DIP).
    """
    directory = UserDirectory(db)
    if "@" in username_or_email:
        found = await directory.auth_info(email=username_or_email)
    else:
        found = await directory.auth_info(username=username_or_email)
    user = found.model_dump() if found else None

    if not user:
        raise denial(DenialReason.NO_CREDENTIAL)
    if not user.get("is_active", False):
        raise denial(DenialReason.INACTIVE_USER)
    if user.get("is_deleted", False):
        raise denial(DenialReason.DELETED_USER)
    if payload_version != user.get("token_version", 0):
        raise denial(DenialReason.STALE_TOKEN_VERSION)
    return user


def _enforce_user_tenant_match(
    user: dict[str, Any],
    current_tenant: Any,
) -> None:
    """Shared tenant-mismatch check (superusers bypass).

    P1-6: the NO_TENANT sentinel is falsy BY DESIGN, so a truthiness test
    silently skipped the binding check on tenant-exempt paths. Test identity
    instead. Identity — not ``is_active``, which is a real field: a
    legitimately deactivated tenant must still be bound to its own users.

    The superuser bypass runs FIRST, deliberately. Tenant-exempt prefixes
    such as /api/v1/tenants bind NO_TENANT and are gated by
    get_current_superuser; checking the sentinel first would 403 those
    cross-tenant operator routes. Superuser is the authorization decision for
    them, and a superuser is not tenant-scoped by definition.
    """
    if user.get("is_superuser", False):
        return
    if current_tenant is None or current_tenant is NO_TENANT:
        raise denial(DenialReason.TENANT_CONTEXT_MISSING)
    user_tid = str(user.get("tenant_id")) if user.get("tenant_id") is not None else None
    curr_tid = str(getattr(current_tenant, "id", getattr(current_tenant, "tenant_id", None)))
    if user_tid != curr_tid:
        logger.warning(
            "tenant_mismatch_detected",
            user_id=user["id"],
            user_tenant_id=user.get("tenant_id"),
            requested_tenant_id=getattr(current_tenant, "id", None),
            requested_tenant_slug=getattr(current_tenant, "slug", None),
        )
        raise denial(DenialReason.TENANT_MISMATCH)


async def _resolve_optional_bearer_flow(
    request: Request,
    db: AsyncSession | None,
    token: str,
    cached_payload: dict[str, Any] | None,
    current_tenant: Any,
) -> AuthContext | None:
    """Non-strict (optional auth) path.

    C3: anonymous is ONLY for "no credential presented" (empty token — the
    caller already returned None above). A PRESENTED bearer token that fails
    verification (bad signature, revoked, wrong type, unknown/dead user,
    dead session, tenant mismatch, unexpected error) is fail-loud (401/403),
    never silent anonymous — so token-forgery probes are visible and a
    caller cannot accidentally serve the anonymous view to an attacker with
    a garbage/revoked token. API keys follow the same rule in
    ``resolve_principal`` (invalid presented key → 401).
    """
    if db is None:
        from src.app.core.db.database import get_db_context

        async with get_db_context() as _db:
            return await _resolve_optional_bearer_flow(request, _db, token, cached_payload, current_tenant)
    try:
        resolved = await _resolve_token_data(request, token, cached_payload, db)
        token_data, username_or_email, payload_version, _jti, jwt_exp, session_id = resolved
        user = await _load_and_validate_user(db, username_or_email, payload_version)
        await _enforce_session_valid(session_id, None, user.get("id"), jwt_exp)
        _enforce_user_tenant_match(user, current_tenant)
        return _create_auth_context(user, token_data, current_tenant, db)
    except (UnauthorizedException, ForbiddenException, TenantContextError):
        # P1-6: TenantContextError is a sibling AppException, not an
        # Unauthorized/Forbidden one, so it used to fall through to the bare
        # except below and be rewritten as NO_CREDENTIAL — a caller holding a
        # valid token was served the anonymous view, violating the C3 rule
        # stated in this function's docstring.
        raise
    except Exception as e:  # noqa: BLE001 - deliberate fail-loud boundary: see below
        # DELIBERATE (muse 22 triage). The boundary is the whole point of the
        # function: C3 requires a PRESENTED bearer token that fails for ANY
        # reason to be rejected loudly (401), never served as anonymous. The
        # three domain errors above are re-raised with their own reasons; this
        # handler is the catch-all that keeps an UNEXPECTED fault (a DB
        # driver error, a serialization bug, a blacklisted-session store
        # failure) from escaping as a 500 and from masquerading as
        # "no credential" — the first is an outage, the second is a
        # fail-OPEN that would serve the anonymous view to a caller holding
        # a bad token. Both are unacceptable, so the fault is logged and
        # converted to a denial. It is broad on purpose: the set of things
        # token verification can raise is not knowable at this seam, and a
        # narrow list would let exactly the unanticipated fault it exists to
        # contain escape instead.
        logger.warning("optional_auth_context_verify_failed", error=str(e))
        raise denial(DenialReason.NO_CREDENTIAL) from e


# P1-1: API-key scope vocabulary, enforced at the resolve-principal seam
# (enforce_api_key_scope below). Coarse scopes "read"/"write" cover HTTP
# verbs ("write" implies "read" for safe methods); "admin" is a wildcard;
# resource-qualified "read:<resource>"/"write:<resource>" narrow to one
# top-level resource (first path segment after an optional vN prefix).
# Keys with no scopes are denied everywhere (fail-closed).
API_KEY_SCOPE_WILDCARD = "admin"
_API_KEY_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _required_api_key_scope(method: str) -> str:
    """Map an HTTP method to the coarse scope covering it."""
    return "read" if method.upper() in _API_KEY_READ_METHODS else "write"


def api_key_scope_allows(scopes: Any, method: str, path: str) -> bool:
    """Pure policy check: does an API key's scope set cover this request?"""
    if isinstance(scopes, str):
        scopes = [scopes]
    normalized = {s.lower() for s in (scopes or []) if isinstance(s, str)}
    if API_KEY_SCOPE_WILDCARD in normalized:
        return True
    if not normalized:
        return False
    required = _required_api_key_scope(method)
    if required in normalized:
        return True
    if required == "read" and "write" in normalized:
        return True
    segments = [seg for seg in path.split("/") if seg]
    # Skip global prefixes ("/api", "/vN") — the resource is the first
    # remaining segment, e.g. "posts" in "/api/v1/posts/1".
    resource = ""
    for seg in segments:
        lowered = seg.lower()
        if lowered == "api" or (lowered.startswith("v") and lowered[1:].isdigit()):
            continue
        resource = lowered
        break
    return bool(resource) and f"{required}:{resource}" in normalized


def enforce_api_key_scope(user: AuthenticatedUser, request: Request | None) -> None:
    """Fail-closed scope gate for API-key-authenticated principals.

    Raises ForbiddenException when the key's scopes do not cover the
    request. JWT principals never reach here (separate seam branch).
    """
    scopes = user.api_key_scopes
    method = getattr(request, "method", None)
    url = getattr(request, "url", None)
    path = getattr(url, "path", None)
    if not isinstance(method, str) or not isinstance(path, str):
        raise denial(DenialReason.SCOPE_UNVERIFIABLE)
    if not api_key_scope_allows(scopes, method, path):
        raise denial(DenialReason.SCOPE_MISSING, scope=_required_api_key_scope(method))


async def resolve_principal(
    request: Request,
    db: AsyncSession | None = None,
    token: str | None = None,
    api_key_user: AuthenticatedUser | None = None,
    *,
    policy: PrincipalPolicy = REQUIRED,
    current_tenant: Any = None,
) -> AuthContext | None:
    """Unified engine to resolve and verify an authenticated principal.

    This is the ONE resolve-principal seam (architecture candidate 2) unifying:
    - required Bearer token auth (`get_auth_context`)
    - optional Bearer token auth (`_verify_optional_bearer_token`)
    - API key auth (`get_user_by_api_key`)
    - optional auth dispatcher (`get_optional_auth_context`)

    Every refusal it raises comes from ``core.principal.DENIAL_TABLE``, so the
    entry points above it cannot disagree about which status a condition
    produces.

    Parameters:
        request: FastAPI/ASGI Request
        db: Database session
        token: Bearer token string (if known)
        api_key_user: Pre-validated AuthenticatedUser from API key (if any)
        policy: ``REQUIRED`` raises when no credential was presented;
                ``OPTIONAL`` returns None instead. A credential that IS
                presented and fails always raises, in both modes.
        current_tenant: Tenant context (auto-resolved from context if omitted).
    """
    if current_tenant is None:
        current_tenant = get_current_tenant()

    # 1. API-Key flavor
    has_api_key = "X-API-Key" in request.headers if request is not None else False
    if has_api_key:
        if db is None:
            from src.app.core.db.database import get_db_context

            async with get_db_context() as _db:
                if api_key_user is None or not isinstance(api_key_user, AuthenticatedUser):
                    api_key = request.headers.get("X-API-Key") if request is not None else None
                    if api_key:
                        api_key_user = await get_user_by_api_key(api_key, _db, request)
                if api_key_user is None:
                    raise denial(DenialReason.INVALID_API_KEY)
                enforce_api_key_scope(api_key_user, request)
                return _create_auth_context(api_key_user, None, current_tenant, _db)
        if api_key_user is None or not isinstance(api_key_user, AuthenticatedUser):
            api_key = request.headers.get("X-API-Key") if request is not None else None
            if api_key:
                api_key_user = await get_user_by_api_key(api_key, db, request)  # type: ignore[arg-type]
        if api_key_user is None:
            raise denial(DenialReason.INVALID_API_KEY)
        enforce_api_key_scope(api_key_user, request)
        return _create_auth_context(api_key_user, None, current_tenant, db)  # type: ignore[arg-type]
    # 2. Bearer Token extraction & length check
    if token is None or not token:
        token = await _resolve_bearer_token(request, token or "")
    if not token:
        if policy.presence is PrincipalPresence.REQUIRED:
            raise denial(DenialReason.NO_CREDENTIAL)
        return None

    _enforce_token_length(token)

    # 3. Resolve token data (cached payload or verify_token)
    cached_payload = get_cached_jwt_payload(request) if request is not None else None

    if policy.presence is PrincipalPresence.REQUIRED:
        # H-7: the strict path consumes the SAME primitives as the optional
        # path (steps 3-4 shared); the composite prefetch stays inside the
        # strict helper because it is strict-only (RBAC bitmask warm).
        return await _resolve_strict_bearer_flow(request, db, token, cached_payload, current_tenant)

    return await _resolve_optional_bearer_flow(request, db, token, cached_payload, current_tenant)


async def _resolve_strict_bearer_flow(
    request: Request,
    db: AsyncSession | None,
    token: str,

    cached_payload: Any = None,
    current_tenant: Any = None,
) -> AuthContext:
    if db is None:
        from src.app.core.db.database import get_db_context

        async with get_db_context() as _db:
            return await _resolve_strict_bearer_flow(request, _db, token, cached_payload, current_tenant)
    resolved = await _resolve_token_data(request, token, cached_payload, db)
    token_data, username_or_email, payload_version, jti, jwt_exp, session_id = resolved

    # 4. User loading & verification
    user = await _load_and_validate_user(db, username_or_email, payload_version)
    user_id = user["id"]
    user_tenant_id = user.get("tenant_id")

    # 5. Composite auth-prefetch & revocation enforcement
    from src.app.core.security.auth_prefetch import execute_composite_auth_prefetch

    # P0-3: the middleware's Redis blacklist probe (stored tri-state on
    # request.state) makes the composite's blacklist leg redundant when
    # definite — skip it and save those ops. None (Redis down at
    # middleware time) keeps the full composite check.
    cached_blacklist = getattr(request.state, "is_token_blacklisted", None) if request is not None else None
    is_blacklisted, session_valid, bitmask = await execute_composite_auth_prefetch(
        token=token,
        jti=jti,
        user_id=user_id,
        session_id=session_id,
        tenant_id=user_tenant_id,
        check_bitmask=True,
        check_blacklist=(cached_blacklist is None),
    )

    await _enforce_token_not_revoked(
        _BlacklistProbe(token, jti, cached_blacklist, is_blacklisted), db
    )
    await _enforce_session_valid(session_id, session_valid, user_id, jwt_exp)

    if bitmask is not None and request is not None and user_tenant_id is not None:
        request.state.cached_permissions_bitmask = (user_id, str(user_tenant_id), bitmask)

    # 6. Tenant isolation check
    _enforce_user_tenant_match(user, current_tenant)

    return _create_auth_context(user, token_data, current_tenant, db)


async def get_current_user(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
    auth_context: Annotated[AuthContext | None, Depends(get_auth_context)] = None,
) -> AuthenticatedUser:
    """Get current authenticated user by consuming get_auth_context."""
    if not token or not isinstance(token, str):
        token = await _resolve_bearer_token(request, token or "")

    if not auth_context or not isinstance(auth_context, AuthContext):
        auth_context = await get_auth_context(request, token=token)
    return auth_context.user


async def get_user_by_api_key(
    api_key: Annotated[str | None, Depends(api_key_scheme)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    request: Request,
) -> AuthenticatedUser | None:
    """Validate API key and return user info.

    This dependency allows authentication using 'X-API-Key' header.
    Returns user dict if valid, None otherwise.

    ``None`` conflates "no key presented" with "key did not validate"; the
    seam (``resolve_principal``) draws the distinction by raising 401 for a
    presented-but-invalid key. This dependency is therefore only consumed
    *through* the seam, never depended on directly by a route.
    """
    if not api_key:
        return None

    from src.app.modules.api_keys.service import APIKeyService

    api_key_service = APIKeyService(db)
    key_info = await api_key_service.validate_api_key(api_key)

    if not key_info:
        return None

    current_tenant = get_current_tenant()
    if current_tenant is None or current_tenant is NO_TENANT:
        raise denial(DenialReason.TENANT_CONTEXT_MISSING)
    key_tenant_id = key_info.get("tenant_id")
    if not key_tenant_id or str(key_tenant_id) != str(current_tenant.tenant_id):
        raise denial(DenialReason.API_KEY_TENANT_MISMATCH)

    directory = UserDirectory(db)
    found = await directory.by_id(key_info["user_id"])

    if not found:
        return None

    # by_id returns UserRead: dump then add the API-key keys.
    user_dict = found.model_dump()

    if user_dict.get("is_active", True) and not user_dict.get("is_deleted", False):
        user_dict["api_key_scopes"] = key_info.get("scopes", [])
        user_dict["api_key_id"] = key_info.get("key_id")
        return AuthenticatedUser(**user_dict)  # type: ignore[arg-type]

    return None


async def _verify_optional_bearer_token(
    token_value: str,
    request: Request,
    db: AsyncSession | None = None,
    current_tenant: Any = None,
) -> AuthContext | None:
    """Verify optional bearer token using the unified resolve_principal seam."""
    return await resolve_principal(
        request=request, db=db, token=token_value, policy=OPTIONAL,
        current_tenant=current_tenant,
    )
    return await resolve_principal(
        request=request,
        db=db,
        token=token_value,
        policy=OPTIONAL,
        current_tenant=current_tenant,
    )


async def get_optional_auth_context(
    request: Request,
    api_key_user: AuthenticatedUser | None = Depends(get_user_by_api_key),
) -> AuthContext | None:
    """Get optional auth context from API key or token, validating credentials if provided."""
    return await resolve_principal(request, api_key_user=api_key_user, policy=OPTIONAL)


async def get_optional_user(
    request: Request,
    api_key_user: Annotated[AuthenticatedUser | None, Depends(get_user_by_api_key)] = None,
    auth_context: Annotated[AuthContext | None, Depends(get_optional_auth_context)] = None,
) -> AuthenticatedUser | None:
    """Get optional user from Bearer token or API Key by consuming get_optional_auth_context."""
    if not isinstance(auth_context, AuthContext):
        auth_context = await get_optional_auth_context(request, api_key_user=api_key_user)
    if auth_context:
        return auth_context.user
    return None


async def get_current_superuser(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    auth_context: Annotated[AuthContext | None, Depends(get_auth_context)] = None,
) -> AuthenticatedUser:
    """Require an active superuser, bound to the tenant it acts in.

    P1: this gate checked only ``is_active``/``is_superuser`` while
    ``requires_permission`` pinned the tenant first — the same documented rule
    implemented twice, differently, on adjacent mutations in the same router.
    It now applies the SHARED assertion.

    Note the ordering difference from ``requires_permission``, and why it is
    correct here: a superuser is not tenant-scoped by definition, and the
    tenant-exempt operator surface (``/api/v1/tenants``) is exactly where a
    cross-tenant principal must be admitted. So on a tenant-BOUND request the
    tenant is asserted; on a tenant-less one there is nothing to compare and
    the superuser check stands alone.

    The tenant comes from ``auth_context.tenant`` — the same resolved principal
    ``requires_permission`` reads — NOT from ``get_current_tenant()``. Reading
    the ContextVar here is the second, divergent resolution path the shared
    rule exists to delete: under the sanctioned
    ``app.dependency_overrides[get_auth_context]`` seam the two sources can
    disagree, and the two gates would then enforce different rules on adjacent
    mutations. ``get_auth_context`` is already a sub-dependency of
    ``get_current_user``, so this adds no extra principal resolution (FastAPI
    caches it per request). ``auth_context`` is optional only so the gate stays
    callable directly from unit tests; in production it is always supplied.
    """
    if not current_user.is_active:
        raise denial(DenialReason.INACTIVE_SUPERUSER)
    if not current_user.is_superuser:
        raise denial(DenialReason.NOT_SUPERUSER)

    enforce_superuser_tenant_rule(
        user_tenant_id=current_user.tenant_id,
        request_tenant=auth_context.tenant if auth_context is not None else None,
        user_id=current_user.id,
        gate="get_current_superuser",
    )

    return current_user


# ---------------------------------------------------------------------------
# DIP wiring. ``core.principal`` owns the ``get_auth_context`` dependency so
# that ``core.rbac`` can depend on it without importing this layer (H-5); the
# actual resolution is delegated back here, where importing the module
# facades is legal. Same registry shape as ``core.security.jwt_service``'s
# lookup seams (PR-12) — registered at import, asserted at startup.
# ---------------------------------------------------------------------------


async def _principal_provider(
    request: Request, token: str | None = None
) -> AuthContext | None:
    return await resolve_principal(request, token=token, policy=REQUIRED)


register_principal_provider(_principal_provider)
