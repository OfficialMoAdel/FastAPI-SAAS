import asyncio
import logging
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

import bcrypt
import jwt
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import PyJWTError as JWTError
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.crud_token_blacklist import crud_token_blacklist
from ..schemas import TokenBlacklistCreate, TokenData
from .token_blacklist_cache import (
    add_token_to_blacklist,
    add_tokens_to_blacklist,
    is_token_blacklisted,
)

SECRET_KEY: SecretStr = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS = settings.REFRESH_TOKEN_EXPIRE_DAYS

# JWT Audience for token validation
JWT_AUDIENCE = "fastapi-saas-api"

_jwt_logger = logging.getLogger(__name__)


def _get_signing_key() -> str:
    """Get the key for signing tokens (private key for RS256, secret for HS256)."""
    if ALGORITHM == "RS256" and settings.JWT_PRIVATE_KEY:
        return settings.JWT_PRIVATE_KEY.get_secret_value()
    return SECRET_KEY.get_secret_value()


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
    """Verify password asynchronously without blocking the event loop."""
    correct_password: bool = await asyncio.to_thread(bcrypt.checkpw, plain_password.encode(), hashed_password.encode())
    return correct_password


async def get_password_hash(password: str) -> str:
    """Hash password asynchronously without blocking the event loop."""
    hashed_password: bytes = await asyncio.to_thread(
        bcrypt.hashpw,
        password.encode(),
        bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS),
    )
    return hashed_password.decode()


async def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC).replace(tzinfo=None) + expires_delta
    else:
        expire = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "token_type": TokenType.ACCESS, "aud": JWT_AUDIENCE})
    # Use thread pool for CPU-bound JWT signing to avoid blocking event loop
    encoded_jwt: str = await asyncio.to_thread(jwt.encode, to_encode, _get_signing_key(), ALGORITHM)
    return encoded_jwt


async def create_refresh_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC).replace(tzinfo=None) + expires_delta
    else:
        expire = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "token_type": TokenType.REFRESH, "aud": JWT_AUDIENCE})
    # Use thread pool for CPU-bound JWT signing to avoid blocking event loop
    encoded_jwt: str = await asyncio.to_thread(jwt.encode, to_encode, _get_signing_key(), ALGORITHM)
    return encoded_jwt


async def verify_token(token: str, expected_token_type: TokenType, db: AsyncSession) -> TokenData | None:
    """Verify a JWT token and return TokenData if valid.

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
    # Check Redis cache first (O(1) lookup)
    cache_result = await is_token_blacklisted(token)
    if cache_result is True:
        return None
    elif cache_result is None:
        # Redis unavailable, fall back to DB check
        is_blacklisted = await crud_token_blacklist.exists(db, token=token)
        if is_blacklisted:
            return None
    # cache_result is False means not in cache, proceed with token validation

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
                expires_at = datetime.fromtimestamp(exp_timestamp, tz=UTC).replace(tzinfo=None)
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
            expires_at = datetime.fromtimestamp(exp_timestamp, tz=UTC).replace(tzinfo=None)
            await crud_token_blacklist.create(db, object=TokenBlacklistCreate(token=token, expires_at=expires_at))
            # Add to Redis cache
            await add_token_to_blacklist(token, expires_at)
    except JWTError as e:
        _jwt_logger.warning("Skipping blacklist for invalid token: %s", e)
