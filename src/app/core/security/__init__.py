import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from enum import Enum
from functools import lru_cache
from typing import Any

import bcrypt
import jwt
from cachetools import TTLCache
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from jwt.exceptions import PyJWTError as JWTError
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.db.crud_token_blacklist import crud_token_blacklist
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.schemas import TokenBlacklistCreate, TokenData
from src.app.core.security.token_blacklist_cache import (
    add_token_to_blacklist,
    add_tokens_to_blacklist,
    is_token_blacklisted,
)
from src.app.core.security.tokens import TokenGenerator

# P2.21: In-memory short-TTL cache for DB token lookups to prevent thundering herds on Redis miss
_db_fallback_cache: TTLCache[str, bool] = TTLCache(maxsize=10000, ttl=5)
_in_flight_db_checks: dict[str, asyncio.Task[bool]] = {}

_jwt_logger = get_logger(__name__)

_bcrypt_executor: ThreadPoolExecutor | None = None


def _get_bcrypt_executor() -> ThreadPoolExecutor:
    global _bcrypt_executor
    if _bcrypt_executor is None:
        _bcrypt_executor = ThreadPoolExecutor(
            max_workers=settings.BCRYPT_THREAD_POOL_SIZE, thread_name_prefix="bcrypt_auth"
        )
    return _bcrypt_executor


from src.app.core.security.jwt_utils import _get_parsed_verification_key


@lru_cache(maxsize=1)
def _get_signing_key() -> str:
    """Get the key for signing tokens (private key for RS256, secret for HS256)."""
    if settings.ALGORITHM == "RS256" and settings.JWT_PRIVATE_KEY:
        return str(settings.JWT_PRIVATE_KEY.get_secret_value())
    return str(settings.SECRET_KEY.get_secret_value())


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/account/login", auto_error=False)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

# Export settings constants for module-level access
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES
MFA_PENDING_EXPIRY_SECONDS = settings.auth.MFA_PENDING_EXPIRY_SECONDS


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password asynchronously using dedicated thread pool.

    Uses the bcrypt thread pool to prevent blocking the event loop
    during password verification. This is critical under high concurrency
    as bcrypt operations take ~50-100ms.

    SECURITY FIX: Added exception handling for malformed hashes to prevent crashes.
    """
    try:
        loop = asyncio.get_running_loop()
        result: bool = await loop.run_in_executor(
            _get_bcrypt_executor(), bcrypt.checkpw, plain_password.encode(), hashed_password.encode()
        )
        return result
    except (ValueError, TypeError) as e:
        # Only catch specific bcrypt errors, not all exceptions
        _jwt_logger.error("Password verification failed", error=e)
        return False


async def get_password_hash(password: str) -> str:
    """Hash password asynchronously using dedicated thread pool.

    Uses the bcrypt thread pool to prevent blocking the event loop
    during password hashing. The configured BCRYPT_ROUNDS determines
    the computational cost (default: 12 rounds).
    """
    loop = asyncio.get_running_loop()
    hashed: bytes = await loop.run_in_executor(
        _get_bcrypt_executor(), bcrypt.hashpw, password.encode(), bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    )
    return hashed.decode()


async def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create access token with JTI (JWT ID) for blacklist tracking.

    SECURITY FIX: Includes JTI (JWT ID) to enable precise blacklist checking
    without race conditions.
    """
    from uuid import uuid4

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
            "jti": str(uuid4()),  # Unique token ID for blacklist
            "iat": datetime.now(UTC).timestamp(),  # Issued at
            "token_version": token_version,
        }
    )

    # Use to_thread to prevent blocking the event loop regardless of the algorithm
    encoded_jwt: str = await asyncio.to_thread(jwt.encode, to_encode, _get_signing_key(), settings.ALGORITHM)
    return encoded_jwt


async def create_refresh_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create refresh token with JTI (JWT ID) for blacklist tracking.

    SECURITY FIX: Includes JTI (JWT ID) to enable precise blacklist checking
    without race conditions. Also includes absolute_exp for session lifetime limits.
    """
    from uuid import uuid4

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
            "jti": str(uuid4()),  # Unique token ID for blacklist
            "iat": datetime.now(UTC).timestamp(),  # Issued at
            "token_version": token_version,
            "absolute_exp": (datetime.now(UTC) + timedelta(days=settings.MAX_SESSION_DURATION_DAYS)).timestamp(),
        }
    )

    encoded_jwt: str = await asyncio.to_thread(jwt.encode, to_encode, _get_signing_key(), settings.ALGORITHM)
    return encoded_jwt


async def _check_db_fallback(jti: str | None, token: str, db: AsyncSession) -> bool | None:
    """Helper to perform DB fallback check for token blacklisting."""
    try:
        cache_key = jti if jti else token
        if cache_key in _db_fallback_cache:
            return _db_fallback_cache[cache_key]
        elif cache_key in _in_flight_db_checks:
            return await _in_flight_db_checks[cache_key]

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
            _db_fallback_cache[cache_key] = is_blacklisted
            return is_blacklisted
        finally:
            _in_flight_db_checks.pop(cache_key, None)
    except Exception as e:
        _jwt_logger.error("DB fallback failed during token verification. Failing closed.", error=str(e))
        return None


async def verify_token(token: str, expected_token_type: TokenType, db: AsyncSession) -> TokenData | None:
    """Verify a JWT token and return TokenData if valid.

    SECURITY FIX: Eliminates race condition by decoding token FIRST (local operation),
    then checking blacklist using JTI (JWT ID). This ensures blacklist check happens
    atomically relative to the token content.

    Parameters
    ----------
    token: str
        The JWT token to be verified.
    expected_token_type: TokenType
        The expected type of token (access or refresh)
    db: AsyncSession
        Database session for performing database operations.

    Returns
    -------
    TokenData | None
        TokenData instance if the token is valid, None otherwise.
    """
    # SECURITY FIX: Decode token FIRST (local operation, no race condition)
    # This eliminates the race condition window between blacklist check and decode
    try:
        payload = await asyncio.to_thread(
            jwt.decode,
            token,
            _get_parsed_verification_key(),
            algorithms=[settings.ALGORITHM],
            audience=getattr(settings, "JWT_AUDIENCE", "fastapi-saas-api"),
        )
    except JWTError:
        return None

    # Extract token metadata
    username_or_email: str | None = payload.get("sub")
    token_type: str | None = payload.get("token_type")
    tier_id: int | None = payload.get("tier_id")
    jti: str | None = payload.get("jti")  # JWT ID for blacklist check

    if username_or_email is None or token_type != expected_token_type:
        return None

    # SECURITY FIX: Check blacklist AFTER decode using JTI (JWT ID)
    # This ensures we're checking the specific token, not just any token
    if jti:
        # Check by JTI (preferred - more precise)
        cache_result = await is_token_blacklisted(f"jti:{jti}")
    else:
        # Fallback: check by full token hash (backward compatibility)
        cache_result = await is_token_blacklisted(token)

    if cache_result is True:
        # Token confirmed blacklisted
        return None
    elif cache_result is None:
        # Redis unavailable - fall back to DB check
        # SECURITY FIX: Check by JTI first (primary blacklist method), then full token hash.
        # Previously only checked token hash, which would miss JTI-blacklisted tokens during outages.
        _jwt_logger.warning(
            "Redis unavailable for token blacklist check, falling back to DB. Restore Redis ASAP for optimal security."
        )
        is_blacklisted = await _check_db_fallback(jti, token, db)
        if is_blacklisted is None or is_blacklisted is True:
            return None

    # SECURITY FIX: Check token_version for session invalidation on password change
    token_version: int | None = payload.get("token_version")
    if token_version is not None:
        # P1-4: Cache token_version in Redis to avoid DB query on every request
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
                # Cache miss or error - fall back to DB
                _jwt_logger.warning("token_version_cache_read_error", error=str(e), user=username_or_email)

        if cached_version is not None:
            # Use cached version for O(1) validation
            if cached_version != token_version:
                return None
        else:
            # Cache miss - fetch from DB and cache for 5 minutes
            from src.app.modules.users.crud import crud_users

            if "@" in username_or_email:
                user = await crud_users.get(db, email=username_or_email)
            else:
                user = await crud_users.get(db, username=username_or_email)
            if not user or user.get("token_version") != token_version:
                # Token version mismatch - user changed password or was invalidated
                return None

            # Cache the token_version using atomic SET with NX and EX to prevent race conditions
            # Multiple concurrent requests may fetch from DB, but only first wins cache write
            if redis_client:
                try:
                    # Cache the token version for O(1) lookups. Atomic with EX.
                    # Removed nx=True to allow cache updates on password change.
                    await redis_client.set(cache_key, str(user.get("token_version")), ex=300)
                except Exception as e:
                    _jwt_logger.warning("token_version_cache_write_error", error=str(e), user=username_or_email)

    # Extract absolute expiration for session lifetime enforcement
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

    from sqlalchemy import select

    from src.app.modules.users.models import User

    sub = payload.get("sub")
    if sub:
        stmt = select(User.tenant_id).where((User.username == sub) | (User.email == sub))
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
    """Blacklist both access and refresh tokens.

    Validates token signature before blacklisting to prevent database pollution.
    Adds tokens to both Redis cache (for fast lookup) and database (for persistence).

    Parameters
    ----------
    access_token: str | None
        The access token to blacklist
    refresh_token: str | None
        The refresh token to blacklist
    db: AsyncSession
        Database session for performing database operations.
    access_payload: dict[str, Any] | None
        Optional pre-decoded payload for the access token to avoid re-decoding.
    refresh_payload: dict[str, Any] | None
        Optional pre-decoded payload for the refresh token to avoid re-decoding.
    """
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
                payload = await asyncio.to_thread(
                    jwt.decode,
                    token,
                    _get_parsed_verification_key(),
                    algorithms=[settings.ALGORITHM],
                    audience=getattr(settings, "JWT_AUDIENCE", "fastapi-saas-api"),
                )
            if payload is None:
                continue
            exp_timestamp = payload.get("exp")
            jti = payload.get("jti")
            tenant_id = payload.get("tenant_id")

            if exp_timestamp is not None:
                expires_at = datetime.fromtimestamp(exp_timestamp, tz=UTC)
                try:
                    # BUG FIX: tenant_id is now required for blacklisting (Issue 17)
                    if not tenant_id:
                        tenant_id = await _resolve_tenant_id_for_token(payload, db)

                        if not tenant_id:
                            _jwt_logger.warning("Could not determine tenant_id for token blacklisting")
                            continue

                    # Accumulate tokens for bulk insert
                    from uuid import UUID

                    from src.app.core.db.token_blacklist import TokenBlacklist

                    # Ensure tenant_id is a UUID object (since Pydantic isn't auto-converting it here)
                    if isinstance(tenant_id, str):
                        tenant_id = UUID(tenant_id)

                    # Add full token
                    db.add(TokenBlacklist(token=token, expires_at=expires_at, tenant_id=tenant_id))

                    if jti:
                        jti_key = f"jti:{jti}"
                        db.add(TokenBlacklist(token=jti_key, expires_at=expires_at, tenant_id=tenant_id))

                    # Cache by JTI if available for precise blacklisting
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
            # If bulk insert fails due to a conflict, we skip DB blacklisting
            # (the token is already blacklisted in DB) but still update cache
            _jwt_logger.info("Token already blacklisted in DB (IntegrityError during flush)")

        # Persist to DB for fallback during Redis outages.
        # Only write to Redis AFTER a successful DB commit: if the DB write
        # fails, we must not advertise the token as blacklisted in Redis either,
        # otherwise the DB fallback (used during Redis outages) would miss it.
        try:
            await db.commit()
        except Exception as e:
            _jwt_logger.warning("Failed to commit blacklisted tokens to DB", error=str(e))
            return

    # Bulk add to Redis cache (only after successful DB commit)
    if tokens_for_cache:
        await add_tokens_to_blacklist(tokens_for_cache)


async def blacklist_token(token: str, db: AsyncSession, payload: dict[str, Any] | None = None) -> None:
    """Blacklist a single token after validating its signature.

    Adds token to both Redis cache (for fast lookup) and database (for persistence).
    Uses JTI (JWT ID) for precise blacklisting when available.
    Also stores the JTI-prefixed key in DB so the DB fallback can find it during Redis outages.
    """
    try:
        if not payload:
            payload = await asyncio.to_thread(
                jwt.decode,
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
                # BUG FIX: tenant_id is now required for blacklisting (Issue 17)
                if not tenant_id:
                    tenant_id = await _resolve_tenant_id_for_token(payload, db)

                    if not tenant_id:
                        _jwt_logger.warning("Could not determine tenant_id for token blacklisting")
                        raise ValueError(
                            "Cannot blacklist token: tenant_id could not be determined. "
                            "Ensure user has a valid tenant association."
                        )

                async with db.begin_nested():
                    await crud_token_blacklist.create(
                        db, object=TokenBlacklistCreate(token=token, expires_at=expires_at, tenant_id=tenant_id)
                    )
                    if jti:
                        # SECURITY FIX: Also store JTI-prefixed key in DB for Redis-outage fallback.
                        jti_key = f"jti:{jti}"
                        await crud_token_blacklist.create(
                            db, object=TokenBlacklistCreate(token=jti_key, expires_at=expires_at, tenant_id=tenant_id)
                        )
                # Commit the savepoint to persist DB fallback blacklist
                await db.commit()
                if jti:
                    await add_token_to_blacklist(jti_key, expires_at)
                else:
                    # Fallback for tokens without JTI (backward compatibility)
                    await add_token_to_blacklist(token, expires_at)
            except IntegrityError:
                _jwt_logger.info("Token already blacklisted in DB (IntegrityError)")
    except JWTError as e:
        _jwt_logger.warning("Skipping blacklist for invalid token", error=str(e))


# MFA pending token constants (configured via environment)


from src.app.core.security.pending_token_store import PendingTokenStore


def get_mfa_pending_token_manager(redis: Redis) -> PendingTokenStore:
    """Get PendingTokenStore configured for MFA pending tokens.

    Args:
        redis: Async Redis client from dependency.

    Returns:
        PendingTokenStore instance configured for MFA.
    """
    return PendingTokenStore(
        redis=redis, prefix="mfa_pending", ttl_seconds=settings.MFA_PENDING_EXPIRY_SECONDS, use_hash=True
    )
