"""JWT creation, verification, and blacklisting services."""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from enum import Enum
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

import jwt
from cachetools import TTLCache
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from jwt.exceptions import PyJWTError as JWTError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.db.crud_token_blacklist import crud_token_blacklist
from src.app.core.db.token_blacklist import TokenBlacklist
from src.app.core.logger import get_logger
from src.app.core.schemas import TokenBlacklistCreate, TokenData
from src.app.core.security.jwt_utils import _get_parsed_verification_key
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

_jwt_logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _get_signing_key() -> str:
    """Get the key for signing tokens (private key for RS256, secret for HS256)."""
    if settings.ALGORITHM == "RS256" and settings.JWT_PRIVATE_KEY:
        return str(settings.JWT_PRIVATE_KEY.get_secret_value())
    return str(settings.SECRET_KEY.get_secret_value())


def clear_signing_key_cache() -> None:
    """Clear signing key cache (call on key rotation). H3: prevents lru_cache staleness."""
    _get_signing_key.cache_clear()


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/account/login", auto_error=False)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

# Export settings constants for module-level access
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES
MFA_PENDING_EXPIRY_SECONDS = settings.auth.MFA_PENDING_EXPIRY_SECONDS


def _is_symmetric_algorithm() -> bool:
    """Check if current JWT algorithm is symmetric (HS256/HS384/HS512)."""
    return settings.ALGORITHM.startswith("HS")

async def _jwt_encode(payload: dict, key: str, algorithm: str) -> str:
    """Encode JWT, inlining for symmetric algorithms."""
    if _is_symmetric_algorithm():
        return jwt.encode(payload, key, algorithm)
    return await asyncio.to_thread(jwt.encode, payload, key, algorithm)

async def _jwt_decode(token: str, key: str | Any, algorithms: list[str], **kwargs: Any) -> dict[str, Any]:
    """Decode JWT, inlining for symmetric algorithms."""
    if _is_symmetric_algorithm():
        return jwt.decode(token, key, algorithms=algorithms, **kwargs)  # type: ignore[no-any-return]
    return await asyncio.to_thread(jwt.decode, token, key, algorithms=algorithms, **kwargs)  # type: ignore[no-any-return]


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
        expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    # SECURITY FIX: Add JTI (JWT ID) for precise blacklist tracking
    # SECURITY FIX: Include token_version for session invalidation on password change
    token_version = to_encode.pop("token_version", 0)
    to_encode.update(
        {
            "exp": expire,
            "token_type": TokenType.ACCESS,
            "aud": getattr(settings, "JWT_AUDIENCE", "fastapi-saas-api"),
            "iss": getattr(settings, "JWT_ISSUER", "fastapi-saas"),
            "jti": str(uuid4()),  # Unique token ID for blacklist
            "iat": datetime.now(UTC).timestamp(),  # Issued at
            "token_version": token_version,
        }
    )

    # Use _jwt_encode to inline symmetric algorithms or offload asymmetric algorithms
    encoded_jwt: str = await _jwt_encode(to_encode, _get_signing_key(), settings.ALGORITHM)
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
        expire = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    # SECURITY FIX: Add JTI (JWT ID) for precise blacklist tracking
    # SECURITY FIX: Add absolute_exp for maximum session lifetime enforcement
    # SECURITY FIX: Include token_version for session invalidation on password change
    token_version = to_encode.pop("token_version", 0)
    to_encode.update(
        {
            "exp": expire,
            "token_type": TokenType.REFRESH,
            "aud": getattr(settings, "JWT_AUDIENCE", "fastapi-saas-api"),
            "iss": getattr(settings, "JWT_ISSUER", "fastapi-saas"),
            "jti": str(uuid4()),  # Unique token ID for blacklist
            "iat": datetime.now(UTC).timestamp(),  # Issued at
            "token_version": token_version,
            "absolute_exp": (datetime.now(UTC) + timedelta(days=settings.MAX_SESSION_DURATION_DAYS)).timestamp(),
        }
    )

    encoded_jwt: str = await _jwt_encode(to_encode, _get_signing_key(), settings.ALGORITHM)
    return encoded_jwt


async def _check_db_fallback(jti: str | None, token: str, db: AsyncSession) -> bool | None:
    """Helper to perform DB fallback check for token blacklisting."""
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
                    _is_bl = False
                    if jti:
                        _is_bl = await crud_token_blacklist.exists(db, token=f"jti:{jti}")
                    if not _is_bl:
                        _is_bl = await crud_token_blacklist.exists(db, token=token)
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


async def verify_token(token: str, expected_token_type: TokenType, db: AsyncSession) -> TokenData | None:
    """Verify a JWT token and return TokenData if valid.

    SECURITY FIX: Eliminates race condition by decoding token FIRST (local operation),
    then checking blacklist using JTI (JWT ID). This ensures blacklist check happens
    atomically relative to the token content.
    """
    try:
        payload = await _jwt_decode(
            token,
            _get_parsed_verification_key(),
            algorithms=[settings.ALGORITHM],
            audience=getattr(settings, "JWT_AUDIENCE", "fastapi-saas-api"),
            issuer=getattr(settings, "JWT_ISSUER", "fastapi-saas"),
        )
    except JWTError:
        return None

    username_or_email: str | None = payload.get("sub")
    token_type: str | None = payload.get("token_type")
    tier_id: int | None = payload.get("tier_id")
    jti: str | None = payload.get("jti")

    if username_or_email is None or token_type != expected_token_type:
        return None

    if jti:
        cache_result = await is_token_blacklisted(f"jti:{jti}")
    else:
        cache_result = await is_token_blacklisted(token)

    if cache_result is True:
        return None
    elif cache_result is None:
        _jwt_logger.warning(
            "Redis unavailable for token blacklist check, falling back to DB. Restore Redis ASAP for optimal security."
        )
        is_blacklisted = await _check_db_fallback(jti, token, db)
        if is_blacklisted is None or is_blacklisted is True:
            return None

    token_version: int | None = payload.get("token_version")
    if token_version is not None:
        tenant_id = payload.get("tenant_id")
        from src.app.core.utils.cache import build_tenant_key
        cache_key = build_tenant_key(f"user:{username_or_email}:token_version", tenant_id)

        from src.app.core.utils.cache import client as redis_client

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
            from src.app.modules.users.crud import crud_users

            if "@" in username_or_email:
                user = await crud_users.get(db, email=username_or_email)
            else:
                user = await crud_users.get(db, username=username_or_email)
            if not user or user.get("token_version") != token_version:
                return None

            if redis_client:
                try:
                    await redis_client.set(cache_key, str(user.get("token_version")), ex=300, nx=True)
                except Exception as e:
                    _jwt_logger.warning("token_version_cache_write_error", error=str(e), user=username_or_email)

    absolute_exp: float | None = payload.get("absolute_exp")
    if absolute_exp is not None and absolute_exp < datetime.now(UTC).timestamp():
        _jwt_logger.info("Token absolute expiration reached")
        return None

    return TokenData(
        username_or_email=username_or_email,
        jti=jti,
        tier_id=tier_id,
        absolute_exp=absolute_exp,
        token_version=token_version or 0,
    )


async def _resolve_tenant_id_for_token(payload: dict[str, Any], db: AsyncSession) -> Any | None:
    """Helper to resolve tenant_id for a token from payload, context, or DB."""
    tenant_id = payload.get("tenant_id")
    if tenant_id:
        return tenant_id

    from src.app.core.tenancy.context import get_tenant_context

    tenant = get_tenant_context()
    if tenant:
        return tenant.id

    from src.app.modules.users.models import User

    sub = payload.get("sub")
    if sub:
        stmt = select(User.tenant_id).where((User.username == sub) | (User.email == sub)).limit(1)
        result = await db.execute(stmt)
        return result.scalar()

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
        try:
            if not payload:
                payload = await _jwt_decode(
                    token,
                    _get_parsed_verification_key(),
                    algorithms=[settings.ALGORITHM],
                    audience=getattr(settings, "JWT_AUDIENCE", "fastapi-saas-api"),
                    issuer=getattr(settings, "JWT_ISSUER", "fastapi-saas"),
                )
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
                            continue

                    if isinstance(tenant_id, str):
                        tenant_id = UUID(tenant_id)

                    db.add(TokenBlacklist(token=token, expires_at=expires_at, tenant_id=tenant_id))

                    if jti:
                        jti_key = f"jti:{jti}"
                        db.add(TokenBlacklist(token=jti_key, expires_at=expires_at, tenant_id=tenant_id))

                    cache_key = jti_key if jti else token
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
            _r = db.rollback()
            if inspect.isawaitable(_r):
                await _r
            _jwt_logger.info("Token already blacklisted in DB (IntegrityError during flush)")

        try:
            await db.commit()
        except Exception as e:
            _r = db.rollback()
            if inspect.isawaitable(_r):
                await _r
            _jwt_logger.warning("Failed to commit blacklisted tokens to DB", error=str(e))
            return

    if tokens_for_cache:
        await add_tokens_to_blacklist(tokens_for_cache)


async def blacklist_token(token: str, db: AsyncSession, payload: dict[str, Any] | None = None) -> None:
    """Blacklist a single token after validating its signature."""
    try:
        if not payload:
            payload = await _jwt_decode(
                token,
                _get_parsed_verification_key(),
                algorithms=[settings.ALGORITHM],
                audience=getattr(settings, "JWT_AUDIENCE", "fastapi-saas-api"),
            )
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
                        _jwt_logger.warning("Could not determine tenant_id for token blacklisting")
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
                await db.commit()
                if jti:
                    await add_token_to_blacklist(jti_key, expires_at)
                else:
                    await add_token_to_blacklist(token, expires_at)
            except IntegrityError:
                _r = db.rollback()
                if inspect.isawaitable(_r):
                    await _r
                _jwt_logger.info("Token already blacklisted in DB (IntegrityError)")
    except JWTError as e:
        _jwt_logger.warning("Skipping blacklist for invalid token", error=str(e))


def get_mfa_pending_token_manager(redis: Redis) -> PendingTokenStore:
    """Get PendingTokenStore configured for MFA pending tokens."""
    return PendingTokenStore(
        redis=redis, prefix="mfa_pending", ttl_seconds=settings.MFA_PENDING_EXPIRY_SECONDS, use_hash=True
    )
