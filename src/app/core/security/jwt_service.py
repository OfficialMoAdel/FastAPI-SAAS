from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from enum import Enum
from functools import lru_cache
from typing import Any, Protocol
from uuid import UUID, uuid4

import jwt
from cachetools import TTLCache
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from jwt.exceptions import PyJWTError as JWTError
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.db.crud_token_blacklist import crud_token_blacklist
from src.app.core.db.token_blacklist import TokenBlacklist
from src.app.core.logger import get_logger
from src.app.core.schemas import TokenBlacklistCreate, TokenData
from src.app.core.security.pending_token_store import PendingTokenStore
from src.app.core.security.token_blacklist_cache import (
    add_token_to_blacklist,
    add_tokens_to_blacklist,
    is_token_blacklisted,
)

# P2.21: In-memory short-TTL cache for DB token lookups to prevent thundering herds on Redis miss
# H3 FIX: ttl=5 for negative (not blacklisted) poisons revocation for 5s. Cache only
# positive (blacklisted=True) entries; negative results are not cached (fail-closed per call).
_db_fallback_cache: TTLCache[str, bool] = TTLCache(maxsize=10000, ttl=300)
_in_flight_db_checks: dict[str, asyncio.Task[bool]] = {}
_db_fallback_lock = asyncio.Lock()

# P6: singleflight for token_version DB lookups on Redis miss — collapses a
# cache-miss herd for the same user into ONE SELECT (mirrors the
# _in_flight_db_checks pattern above).
_in_flight_token_version: dict[str, asyncio.Task[int | None]] = {}
_token_version_lock = asyncio.Lock()

_jwt_logger = get_logger(__name__)
class TokenVersionLookup(Protocol):
    """Protocol for resolving user token_version without core importing modules (PR-12 / DIP)."""

    async def __call__(self, username_or_email: str) -> int | None:
        ...


class UserTenantLookup(Protocol):
    """Protocol for resolving user tenant_id without core importing modules (PR-12 / DIP)."""

    async def __call__(self, username_or_email: str, db: AsyncSession) -> Any | None:
        ...


_token_version_lookup: Callable[[str], Awaitable[int | None]] | None = None
_user_tenant_lookup: Callable[[str, AsyncSession], Awaitable[Any | None]] | None = None


def register_token_version_lookup(lookup: Callable[[str], Awaitable[int | None]]) -> None:
    """Register the persistence provider for user token_version lookups (DIP seam)."""
    global _token_version_lookup
    _token_version_lookup = lookup


def register_user_tenant_lookup(lookup: Callable[[str, AsyncSession], Awaitable[Any | None]]) -> None:
    """Register the persistence provider for user tenant_id lookups (DIP seam)."""
    global _user_tenant_lookup
    _user_tenant_lookup = lookup


def reset_jwt_security_lookups() -> None:
    """Reset registered security lookups (test isolation seam).

    TEST-ONLY: guarded to non-production environments to prevent accidental
    unwiring in production (P0 fail-open fix).
    """
    import os

    env = os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "local")).lower()
    if env in ("production", "prod"):
        raise RuntimeError("reset_jwt_security_lookups is test-only and blocked in production")
    global _token_version_lookup, _user_tenant_lookup
    _token_version_lookup = None
    _user_tenant_lookup = None


def ensure_jwt_lookups_registered() -> None:
    """Assert DIP seams are wired; call once at startup after imports (P0).

    Raises ServiceUnavailableError when either lookup is missing so the app
    fails fast instead of silently skipping token_version enforcement.
    """
    if _token_version_lookup is None or _user_tenant_lookup is None:
        from src.app.modules._base.exceptions import ServiceUnavailableError

        missing = []
        if _token_version_lookup is None:
            missing.append("token_version")
        if _user_tenant_lookup is None:
            missing.append("user_tenant")
        _jwt_logger.error("jwt_security_lookups_unwired", missing=missing)
        raise ServiceUnavailableError(f"JWT security lookups unwired: {', '.join(missing)}")

@lru_cache(maxsize=1)
def _get_signing_key() -> str:
    """Get the key for signing tokens (private key for RS256, secret for HS256)."""
    from src.app.core.security.jwt_utils import _is_symmetric_algorithm

    if not _is_symmetric_algorithm():
        return str(settings.crypt.JWT_PRIVATE_KEY.get_secret_value())  # type: ignore[union-attr]
    return str(settings.SECRET_KEY.get_secret_value())


def clear_signing_key_cache() -> None:
    """Clear signing key cache (call on key rotation). H3: prevents lru_cache staleness."""
    _get_signing_key.cache_clear()


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/account/login", auto_error=False)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

# Export settings constants for module-level access
ACCESS_TOKEN_EXPIRE_MINUTES = settings.crypt.ACCESS_TOKEN_EXPIRE_MINUTES
MFA_PENDING_EXPIRY_SECONDS = settings.auth.MFA_PENDING_EXPIRY_SECONDS


async def _jwt_encode(payload: dict, key: str, algorithm: str) -> str:
    """Encode JWT, inlining for symmetric algorithms."""
    from src.app.core.security.jwt_utils import _is_symmetric_algorithm

    if _is_symmetric_algorithm():
        return jwt.encode(payload, key, algorithm=algorithm)
    return await asyncio.to_thread(jwt.encode, payload, key, algorithm)


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"


async def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create access token with JTI (JWT ID) for blacklist tracking.

    SECURITY FIX: Includes JTI (JWT ID) to enable precise blacklist checking
    without race conditions.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=settings.crypt.ACCESS_TOKEN_EXPIRE_MINUTES)

    # SECURITY FIX: Add JTI (JWT ID) for precise blacklist tracking
    # SECURITY FIX: Include token_version for session invalidation on password change
    token_version = to_encode.pop("token_version", 0)
    to_encode.update(
        {
            "exp": expire,
            "token_type": TokenType.ACCESS,
            "aud": settings.crypt.JWT_AUDIENCE,
            "iss": settings.crypt.JWT_ISSUER,
            "jti": str(uuid4()),  # Unique token ID for blacklist
            "iat": datetime.now(UTC).timestamp(),  # Issued at
            "token_version": token_version,
        }
    )
    # A3: stable session id shared with the refresh token; copied from input data
    # when present (rotation), minted fresh at login. Kept separate from jti so
    # blacklist identity stays per-token while session identity stays per-login.
    if "sid" not in to_encode:
        to_encode["sid"] = str(uuid4())

    # Use _jwt_encode to inline symmetric algorithms or offload asymmetric algorithms
    encoded_jwt: str = await _jwt_encode(to_encode, _get_signing_key(), settings.crypt.ALGORITHM)
    return encoded_jwt


async def create_refresh_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create refresh token with JTI (JWT ID) for blacklist tracking.

    SECURITY FIX: Includes JTI (JWT ID) to enable precise blacklist checking
    without race conditions. Also includes absolute_exp for session lifetime limits.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(days=settings.crypt.REFRESH_TOKEN_EXPIRE_DAYS)

    # A2: honor caller-provided sid/absolute_exp/iat (rotation carries them
    # forward); only mint fresh values for a brand-new session.
    # A3: same rule for sid — copied verbatim through rotation.
    token_version = to_encode.pop("token_version", 0)
    if "sid" not in to_encode:
        to_encode["sid"] = str(uuid4())
    if "absolute_exp" not in to_encode:
        absolute_exp_dt = datetime.now(UTC) + timedelta(days=settings.crypt.MAX_SESSION_DURATION_DAYS)
        to_encode["absolute_exp"] = absolute_exp_dt.timestamp()
    if "iat" not in to_encode:
        to_encode["iat"] = datetime.now(UTC).timestamp()
    to_encode.update(
        {
            "exp": expire,
            "token_type": TokenType.REFRESH,
            "aud": settings.crypt.JWT_AUDIENCE,
            "iss": settings.crypt.JWT_ISSUER,
            "jti": str(uuid4()),  # Unique token ID for blacklist
            "token_version": token_version,
        }
    )

    encoded_jwt: str = await _jwt_encode(to_encode, _get_signing_key(), settings.crypt.ALGORITHM)
    return encoded_jwt


async def _check_db_fallback(jti: str | None, token: str, db: AsyncSession) -> bool | None:
    """Helper to perform DB fallback check for token blacklisting.

    SECURITY FIX: the singleflight task is shared across concurrent requests,
    so it must NOT run on the winner's request-scoped session (the winner's
    request may commit/close mid-task, leaving waiters with a closed session
    during exactly the Redis outage this path exists for). The lookup uses its
    own short-lived session; blacklist entries are global jti/token strings,
    so no tenant context is needed. The ``db`` parameter is kept for signature
    compatibility.
    """
    try:
        cache_key = jti if jti else token
        # Fast path: positive cache (blacklisted=True only; negative not cached — H3)
        if cache_key in _db_fallback_cache:
            return _db_fallback_cache[cache_key]

        async with _db_fallback_lock:
            if cache_key in _db_fallback_cache:
                return _db_fallback_cache[cache_key]
            if cache_key in _in_flight_db_checks:
                task = _in_flight_db_checks[cache_key]
            else:

                async def _check_db() -> bool:
                    from src.app.core.db.database import local_session

                    async with local_session() as session:
                        _is_bl = False
                        if jti:
                            _is_bl = await crud_token_blacklist.exists(session, token=f"jti:{jti}")
                        if not _is_bl:
                            _is_bl = await crud_token_blacklist.exists(session, token=token)
                        return _is_bl

                task = asyncio.create_task(_check_db())
                _in_flight_db_checks[cache_key] = task

        try:
            is_blacklisted = await task
            # H3: only cache positive (True) to avoid poisoning revocation; negative stays uncached
            if is_blacklisted:
                _db_fallback_cache[cache_key] = True
            return is_blacklisted
        finally:
            async with _db_fallback_lock:
                _in_flight_db_checks.pop(cache_key, None)
    except Exception as e:
        _jwt_logger.error("DB fallback failed during token verification. Failing closed.", error=str(e))
        return None


async def _load_token_version(cache_key: str, username_or_email: str, redis_client: Any) -> int | None:
    """P6: one DB lookup for the user's token_version; backfills Redis (nx).

    Returns the stored token_version, or None when the user does not exist.
    Only the singleflight winner runs this; concurrent waiters await its task.

    SECURITY FIX: uses its own short-lived session instead of the winner's
    request session. The task is shared across concurrent requests; running it
    on the winner's request-scoped session meant waiters' awaits could execute
    on a session that was mid-commit, rolled back, or already closed
    (SessionClosedError / stale transaction state), rejecting valid tokens.

    P0 FIX (fail-closed): when the lookup is unwired, raise instead of
    returning None so verify_token rejects the token explicitly rather than
    silently skipping version enforcement.
    """
    if _token_version_lookup is None:
        from src.app.modules._base.exceptions import ServiceUnavailableError

        _jwt_logger.error("token_version_lookup_unwired", user=username_or_email)
        raise ServiceUnavailableError("Token version service unavailable")
    version = await _token_version_lookup(username_or_email)
    if version is None:
        return None
    if redis_client and version is not None:
        try:
            await redis_client.set(cache_key, str(version), ex=300, nx=True)
        except Exception as e:
            _jwt_logger.warning("token_version_cache_write_error", error=str(e), user=username_or_email)
    return version


async def is_token_revoked(token: str, jti: str | None, db: AsyncSession) -> bool:
    """Single revocation oracle — the ONE place that decides fail-open/fail-closed.

    Returns True when the token must be treated as revoked:

    - Redis blacklist says True  -> revoked.
    - Redis blacklist says False -> not revoked.
    - Redis unavailable (None)   -> the DB is authoritative via the
      thundering-herd-protected fallback; a DB error fails CLOSED (revoked)
      so a possibly-revoked token is never accepted during an outage.

    Every caller (``verify_token``, the auth dependencies) routes through this
    function so the failure semantics live in one module instead of leaking
    across each seam.
    """
    cache_result = await is_token_blacklisted(token, jti=jti)
    if cache_result is True:
        return True
    if cache_result is False:
        return False
    # Redis unavailable — fall back to the DB (authoritative).
    _jwt_logger.warning(
        "Redis unavailable for token blacklist check, falling back to DB. Restore Redis ASAP for optimal security."
    )
    db_result = await _check_db_fallback(jti, token, db)
    # Fail closed: an error (None) or a hit (True) both mean revoked.
    return db_result is None or db_result is True


async def verify_token(token: str, expected_token_type: TokenType, db: AsyncSession) -> TokenData | None:
    """Verify a JWT token and return TokenData if valid.

    SECURITY FIX: Eliminates race condition by decoding token FIRST (local operation),
    then checking blacklist using JTI (JWT ID). This ensures blacklist check happens
    atomically relative to the token content.
    """
    from src.app.core.security.jwt_utils import decode_verified_token

    payload = await decode_verified_token(token)
    if payload is None:
        return None

    username_or_email: str | None = payload.get("sub")
    token_type: str | None = payload.get("token_type")
    tier_id: int | None = payload.get("tier_id")
    jti: str | None = payload.get("jti")

    if username_or_email is None or token_type != expected_token_type:
        return None

    if await is_token_revoked(token, jti, db):
        return None

    token_version: int | None = payload.get("token_version")
    if token_version is not None:
        tenant_id = payload.get("tenant_id")
        from src.app.core.utils.cache import build_tenant_key

        cache_key = build_tenant_key(f"user:{username_or_email}:token_version", tenant_id)

        from src.app.core.redis_provider import get_redis

        redis_client = get_redis()

        cached_version = None
        if redis_client:
            try:
                cached_value = await redis_client.get(cache_key)
                if cached_value is not None:
                    cached_version = int(cached_value)
            except Exception as e:
                _jwt_logger.warning("token_version_cache_read_error", error=str(e), user=username_or_email)

        if cached_version is not None:
            if cached_version != token_version:
                return None
        else:
            # P6: singleflight — concurrent Redis misses for the same user
            # share one DB lookup instead of stampeding SELECT users.
            async with _token_version_lock:
                task = _in_flight_token_version.get(cache_key)
                if task is None:
                    task = asyncio.create_task(_load_token_version(cache_key, username_or_email, redis_client))
                    _in_flight_token_version[cache_key] = task
            try:
                db_version = await task
            except Exception as e:
                # Fail closed: an unloadable token_version rejects the token.
                _jwt_logger.error("token_version lookup failed, rejecting", error=str(e), user=username_or_email)
                return None
            finally:
                async with _token_version_lock:
                    _in_flight_token_version.pop(cache_key, None)
            if db_version != token_version:
                return None

    absolute_exp: float | None = payload.get("absolute_exp")
    if absolute_exp is not None and absolute_exp < datetime.now(UTC).timestamp():
        _jwt_logger.info("Token absolute expiration reached")
        return None

    return TokenData(
        username_or_email=username_or_email,
        jti=jti,
        tier_id=tier_id,
        absolute_exp=absolute_exp,
        exp=payload.get("exp"),
        token_version=token_version or 0,
        sid=payload.get("sid") or jti,  # A3: fall back to jti for pre-sid tokens
        iat=payload.get("iat"),  # A2: original login iat
    )


async def _resolve_tenant_id_for_token(payload: dict[str, Any], db: AsyncSession) -> Any | None:
    """Helper to resolve tenant_id for a token from payload, context, or DB."""
    tenant_id = payload.get("tenant_id")
    if tenant_id:
        return tenant_id

    from src.app.core.tenancy.context import get_current_tenant

    tenant = get_current_tenant()
    if tenant:
        return tenant.id

    sub = payload.get("sub")
    if sub and _user_tenant_lookup is not None:
        return await _user_tenant_lookup(sub, db)

    if sub:
        _jwt_logger.warning("user_tenant_lookup_unwired", user=sub)
    return None


async def blacklist_tokens(
    access_token: str | None,
    refresh_token: str | None,
    db: AsyncSession,
    access_payload: dict[str, Any] | None = None,
    refresh_payload: dict[str, Any] | None = None,
) -> None:
    """Blacklist both access and refresh tokens."""
    tokens_for_cache: list[tuple[str, datetime]] = []

    tokens_and_payloads = [
        (access_token, access_payload),
        (refresh_token, refresh_payload),
    ]

    for token, payload in tokens_and_payloads:
        if not token:
            continue
        from src.app.core.security.jwt_utils import decode_verified_token

        try:
            if not payload:
                payload = await decode_verified_token(token)
            if payload is None:
                continue
            exp_timestamp = payload.get("exp")
            jti = payload.get("jti")
            tenant_id = payload.get("tenant_id")

            if exp_timestamp is not None:
                expires_at = datetime.fromtimestamp(exp_timestamp, tz=UTC)
                try:
                    if not tenant_id:
                        tenant_id = await _resolve_tenant_id_for_token(payload, db)
                        if not tenant_id:
                            _jwt_logger.warning("Could not determine tenant_id for token blacklisting")
                            # P0 FIX (fail-closed): still blacklist in Redis even without
                            # tenant_id so the token is revoked on the hot path.
                            if jti:
                                jti_key = f"jti:{jti}"
                                tokens_for_cache.append((jti_key, expires_at))
                            else:
                                tokens_for_cache.append((token, expires_at))
                            continue

                    if isinstance(tenant_id, str):
                        tenant_id = UUID(tenant_id)

                    db.add(TokenBlacklist(token=token, expires_at=expires_at, tenant_id=tenant_id))

                    if jti:
                        jti_key = f"jti:{jti}"
                        db.add(TokenBlacklist(token=jti_key, expires_at=expires_at, tenant_id=tenant_id))
                        cache_key = jti_key
                    else:
                        cache_key = token
                    tokens_for_cache.append((cache_key, expires_at))
                except Exception as e:
                    _jwt_logger.warning("Error preparing token for blacklist", error=str(e))
        except JWTError as e:
            _jwt_logger.warning("Skipping blacklist for invalid token", error=str(e))

    if tokens_for_cache:
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # C-2: AsyncSession.rollback() is always awaitable — the
            # inspect.isawaitable guard was dead defensive code.
            await db.rollback()
            _jwt_logger.info("Token already blacklisted in DB (IntegrityError during flush)")

        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            # A4 FIX: previously this early-returned BEFORE the Redis write,
            # leaving the token blacklisted NOWHERE while the user believed
            # they were logged out. Fall through so the cache blacklist (the
            # hot-path check) still records it.
            _jwt_logger.error(
                "Failed to commit blacklisted tokens to DB; still blacklisting in cache",
                error=str(e),
            )

    if tokens_for_cache:
        await add_tokens_to_blacklist(tokens_for_cache)


async def blacklist_token(token: str, db: AsyncSession, payload: dict[str, Any] | None = None) -> None:
    """Blacklist a single token after validating its signature."""
    try:
        if not payload:
            from src.app.core.security.jwt_utils import decode_verified_token

            payload = await decode_verified_token(token)
        if payload is None:
            return
        exp_timestamp = payload.get("exp")
        jti = payload.get("jti")
        tenant_id = payload.get("tenant_id")

        if exp_timestamp is not None:
            expires_at = datetime.fromtimestamp(exp_timestamp, tz=UTC)
            try:
                if not tenant_id:
                    tenant_id = await _resolve_tenant_id_for_token(payload, db)
                    if not tenant_id:
                        _jwt_logger.warning(
                            "Could not determine tenant_id for token blacklisting, using Redis fallback"
                        )
                        # Fail-closed: still blacklist in Redis even without tenant_id
                        if jti:
                            jti_key = f"jti:{jti}"
                            await add_token_to_blacklist(jti_key, expires_at)
                        else:
                            await add_token_to_blacklist(token, expires_at)
                        return

                async with db.begin_nested():
                    await crud_token_blacklist.create(
                        db, object=TokenBlacklistCreate(token=token, expires_at=expires_at, tenant_id=tenant_id)
                    )
                    if jti:
                        jti_key = f"jti:{jti}"
                        await crud_token_blacklist.create(
                            db, object=TokenBlacklistCreate(token=jti_key, expires_at=expires_at, tenant_id=tenant_id)
                        )
                        cache_key = jti_key
                    else:
                        cache_key = token
                await db.commit()
                await add_token_to_blacklist(cache_key, expires_at)
            except IntegrityError:
                # C-2: AsyncSession.rollback() is always awaitable.
                await db.rollback()
                _jwt_logger.info("Token already blacklisted in DB (IntegrityError)")
    except JWTError as e:
        _jwt_logger.warning("Skipping blacklist for invalid token", error=str(e))


def get_mfa_pending_token_manager(redis: Redis) -> PendingTokenStore:
    """Get PendingTokenStore configured for MFA pending tokens."""
    return PendingTokenStore(
        redis=redis, prefix="mfa_pending", ttl_seconds=settings.auth.MFA_PENDING_EXPIRY_SECONDS, use_hash=True
    )
