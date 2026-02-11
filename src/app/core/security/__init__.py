import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from enum import Enum
from functools import lru_cache
from typing import Any, cast

import bcrypt
import jwt
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import PyJWTError as JWTError
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.crud_token_blacklist import crud_token_blacklist
from ..json_utils import json_dumps, json_loads
from ..schemas import TokenBlacklistCreate, TokenData
from .token_blacklist_cache import (
    add_token_to_blacklist,
    add_tokens_to_blacklist,
    is_token_blacklisted,
)
from .tokens import TokenGenerator

SECRET_KEY: SecretStr = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS = settings.REFRESH_TOKEN_EXPIRE_DAYS

# JWT Audience for token validation
JWT_AUDIENCE = "fastapi-saas-api"

_jwt_logger = logging.getLogger(__name__)

# Dedicated thread pool for bcrypt operations to prevent event loop blocking
# bcrypt.checkpw takes ~50-100ms depending on rounds, so we use a thread pool
# to avoid blocking the async event loop during high-concurrency authentication
_BCRYPT_MAX_WORKERS = 10
_bcrypt_executor = ThreadPoolExecutor(
    max_workers=_BCRYPT_MAX_WORKERS,
    thread_name_prefix="bcrypt_auth"
)


@lru_cache(maxsize=1)
def _get_signing_key() -> str:
    """Get the key for signing tokens (private key for RS256, secret for HS256)."""
    if ALGORITHM == "RS256" and settings.JWT_PRIVATE_KEY:
        return settings.JWT_PRIVATE_KEY.get_secret_value()
    return SECRET_KEY.get_secret_value()


@lru_cache(maxsize=1)
def _get_verification_key() -> str:
    """Get the key for verifying tokens (public key for RS256, secret for HS256)."""
    if ALGORITHM == "RS256" and settings.JWT_PUBLIC_KEY:
        return settings.JWT_PUBLIC_KEY
    return SECRET_KEY.get_secret_value()


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login")


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password asynchronously using dedicated thread pool.
    
    Uses the bcrypt thread pool to prevent blocking the event loop
    during password verification. This is critical under high concurrency
    as bcrypt operations take ~50-100ms.
    """
    loop = asyncio.get_event_loop()
    result: bool = await loop.run_in_executor(
        _bcrypt_executor,
        bcrypt.checkpw,
        plain_password.encode(),
        hashed_password.encode()
    )
    return result


async def get_password_hash(password: str) -> str:
    """Hash password asynchronously using dedicated thread pool.
    
    Uses the bcrypt thread pool to prevent blocking the event loop
    during password hashing. The configured BCRYPT_ROUNTS determines
    the computational cost (default: 12 rounds).
    """
    loop = asyncio.get_event_loop()
    hashed: bytes = await loop.run_in_executor(
        _bcrypt_executor,
        bcrypt.hashpw,
        password.encode(),
        bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    )
    return hashed.decode()


async def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "token_type": TokenType.ACCESS, "aud": JWT_AUDIENCE})
    # HS256 is fast (~10-50us) — to_thread overhead (~50-200us) exceeds the operation itself.
    # Only use to_thread for CPU-heavy RS256 signing.
    if ALGORITHM == "RS256":
        encoded_jwt: str = await asyncio.to_thread(jwt.encode, to_encode, _get_signing_key(), ALGORITHM)
    else:
        encoded_jwt = jwt.encode(to_encode, _get_signing_key(), ALGORITHM)
    return encoded_jwt


async def create_refresh_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "token_type": TokenType.REFRESH, "aud": JWT_AUDIENCE})
    if ALGORITHM == "RS256":
        encoded_jwt: str = await asyncio.to_thread(jwt.encode, to_encode, _get_signing_key(), ALGORITHM)
    else:
        encoded_jwt = jwt.encode(to_encode, _get_signing_key(), ALGORITHM)
    return encoded_jwt


async def verify_token(token: str, expected_token_type: TokenType, db: AsyncSession) -> TokenData | None:
    """Verify a JWT token and return TokenData if valid.

    Implements a two-layer blacklist check (Redis cache + DB fallback) to prevent
    use of revoked tokens. When Redis is unavailable, falls back to DB check.

    SECURITY NOTE: When using DB fallback mode (Redis unavailable), there is a
    theoretical race condition window between the blacklist check and token
    validation. In production, ensure Redis high availability to minimize risk.
    If Redis is down, the system gracefully degrades to DB checks with slightly
    higher latency but maintains security.

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
    # Layer 1: Check Redis cache first (O(1) lookup, sub-millisecond)
    cache_result = await is_token_blacklisted(token)
    if cache_result is True:
        # Token confirmed blacklisted in Redis
        return None
    elif cache_result is None:
        # Redis unavailable - fall back to DB check with warning
        _jwt_logger.warning(
            "Redis unavailable for token blacklist check, falling back to DB. "
            "This increases latency and creates a small race condition window. "
            "Restore Redis ASAP for optimal security."
        )
        is_blacklisted = await crud_token_blacklist.exists(db, token=token)
        if is_blacklisted:
            return None
    # cache_result is False means token not in blacklist, proceed with validation

    try:
        payload = jwt.decode(token, _get_verification_key(), algorithms=[ALGORITHM], audience=JWT_AUDIENCE)
    except JWTError:
        return None

    try:
        username_or_email: str | None = payload.get("sub")
        token_type: str | None = payload.get("token_type")
        tier_id: int | None = payload.get("tier_id")

        if username_or_email is None or token_type != expected_token_type:
            return None

        return TokenData(username_or_email=username_or_email, tier_id=tier_id)
    except Exception:
        return None


async def blacklist_tokens(access_token: str, refresh_token: str, db: AsyncSession) -> None:
    """Blacklist both access and refresh tokens.

    Validates token signature before blacklisting to prevent database pollution.
    Adds tokens to both Redis cache (for fast lookup) and database (for persistence).

    Parameters
    ----------
    access_token: str
        The access token to blacklist
    refresh_token: str
        The refresh token to blacklist
    db: AsyncSession
        Database session for performing database operations.
    """
    tokens_for_cache: list[tuple[str, datetime]] = []

    for token in [access_token, refresh_token]:
        try:
            payload = jwt.decode(token, _get_verification_key(), algorithms=[ALGORITHM], audience=JWT_AUDIENCE)
            exp_timestamp = payload.get("exp")
            if exp_timestamp is not None:
                expires_at = datetime.fromtimestamp(exp_timestamp, tz=UTC)
                await crud_token_blacklist.create(db, object=TokenBlacklistCreate(token=token, expires_at=expires_at))
                tokens_for_cache.append((token, expires_at))
        except JWTError as e:
            _jwt_logger.warning("Skipping blacklist for invalid token: %s", e)

    # Bulk add to Redis cache
    if tokens_for_cache:
        await add_tokens_to_blacklist(tokens_for_cache)


async def blacklist_token(token: str, db: AsyncSession) -> None:
    """Blacklist a single token after validating its signature.

    Adds token to both Redis cache (for fast lookup) and database (for persistence).
    """
    try:
        payload = jwt.decode(token, _get_verification_key(), algorithms=[ALGORITHM], audience=JWT_AUDIENCE)
        exp_timestamp = payload.get("exp")
        if exp_timestamp is not None:
            expires_at = datetime.fromtimestamp(exp_timestamp, tz=UTC)
            await crud_token_blacklist.create(db, object=TokenBlacklistCreate(token=token, expires_at=expires_at))
            # Add to Redis cache
            await add_token_to_blacklist(token, expires_at)
    except JWTError as e:
        _jwt_logger.warning("Skipping blacklist for invalid token: %s", e)


# MFA pending token constants
MFA_PENDING_EXPIRY_SECONDS = 300  # 5 minutes


class MFAPendingTokenManager:
    """Manager for MFA pending tokens.

    These tokens are issued after successful password authentication
    when MFA is enabled. They allow completion of the login flow
    with MFA verification.

    Features:
    - 5-minute expiration
    - One-time use
    - Limited scope (only valid for MFA verification)
    """

    def __init__(self, redis: Redis):
        """Initialize MFA pending token manager.

        Args:
            redis: Async Redis client instance.
        """
        self._redis = redis

    async def create_token(self, user_id: int, username: str) -> str:
        """Create MFA pending token.

        Args:
            user_id: User ID for the pending login.
            username: Username for the pending login.

        Returns:
            Generated MFA pending token.
        """
        token = TokenGenerator.generate_magic_link_token()

        # Build token data
        token_data = {
            "user_id": user_id,
            "username": username,
            "scope": "mfa_pending",
        }

        # Store in Redis with expiration
        key = f"mfa_pending:{token}"
        await self._redis.setex(
            key,
            MFA_PENDING_EXPIRY_SECONDS,
            json_dumps(token_data, as_str=True),
        )

        return token

    async def validate_token(self, token: str) -> dict[str, Any] | None:
        """Validate MFA pending token (one-time use, 5-min expiry).

        Args:
            token: The MFA pending token to validate.

        Returns:
            Token metadata dict if valid, None if expired/used/invalid.
        """
        key = f"mfa_pending:{token}"

        # Get token data
        data = await self._redis.get(key)

        if data is None:
            return None

        # Delete immediately (one-time use)
        await self._redis.delete(key)

        try:
            token_data = cast(dict[str, Any], json_loads(data))
            return token_data
        except json.JSONDecodeError:
            return None


def get_mfa_pending_token_manager(redis: Redis) -> MFAPendingTokenManager:
    """Get MFAPendingTokenManager instance for dependency injection.

    Args:
        redis: Async Redis client from dependency.

    Returns:
        MFAPendingTokenManager instance.
    """
    return MFAPendingTokenManager(redis=redis)
