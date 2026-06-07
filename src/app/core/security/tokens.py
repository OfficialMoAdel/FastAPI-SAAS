"""Secure token generation for authentication flows.

This module provides cryptographically secure token generation for:
- Magic link authentication
- SSO state parameters (CSRF protection)
- Email verification
- API key generation

Features:
- Async Redis operations for state management
- Proper connection cleanup
- One-time use tokens with automatic expiration
- Audit logging for security operations

Usage:
    # Via dependency injection (recommended)
    from src.app.core.security.tokens import get_sso_state_manager

    @router.get("/auth/google")
    async def google_login(
        sso_manager: SSOStateManager = Depends(get_sso_state_manager)
    ):
        state = await sso_manager.create_state(
            provider="google",
            redirect_uri=str(request.url)
        )
        ...
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any, cast

from redis.asyncio import Redis

from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger

logger = get_logger(__name__)

# Lua script for atomic GETDEL (get and delete) to prevent race conditions.
# Returns the value if key existed, None otherwise.
_GETDEL_LUA = """
    local value = redis.call('GET', KEYS[1])
    if value then
        redis.call('DEL', KEYS[1])
    end
    return value
"""


def hash_token(token: str) -> str:
    """Hash a token for secure storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenGenerator:
    """Static utilities for secure token generation.

    Uses Python's secrets module for cryptographically secure random generation.
    All tokens are URL-safe base64 encoded.
    """

    # Token lengths (bytes)
    MAGIC_LINK_LENGTH = 32  # 43 base64 characters
    SSO_STATE_LENGTH = 16  # 22 base64 characters
    VERIFICATION_LENGTH = 32  # 43 base64 characters
    API_KEY_LENGTH = 32  # 43 base64 characters

    @staticmethod
    def generate_magic_link_token() -> str:
        """Generate cryptographically secure magic link token.

        Returns:
            URL-safe base64-encoded 32-byte random token (43 chars).
        """
        return secrets.token_urlsafe(TokenGenerator.MAGIC_LINK_LENGTH)

    @staticmethod
    def generate_sso_state() -> str:
        """Generate OAuth state parameter for CSRF protection.

        Returns:
            URL-safe base64-encoded 16-byte random token (22 chars).
        """
        return secrets.token_urlsafe(TokenGenerator.SSO_STATE_LENGTH)

    @staticmethod
    def generate_verification_token() -> str:
        """Generate email verification token.

        Returns:
            URL-safe base64-encoded 32-byte random token (43 chars).
        """
        return secrets.token_urlsafe(TokenGenerator.VERIFICATION_LENGTH)

    @staticmethod
    def generate_secure_password(length: int = 32) -> str:
        """Generate a cryptographically secure random password.

        Args:
            length: Length in bytes (not characters).

        Returns:
            Random password string (URL-safe base64 encoded).
        """
        return secrets.token_urlsafe(length)

    @staticmethod
    def generate_api_key(prefix: str = "sk_live") -> str:
        """Generate a secure API key with prefix.

        Args:
            prefix: Key prefix (e.g., sk_live, sk_test).

        Returns:
            Prefixed API key (e.g., sk_live_<random>).
        """
        return f"{prefix}_{secrets.token_urlsafe(TokenGenerator.API_KEY_LENGTH)}"


class SSOStateManager:
    """Async SSO state management with Redis.

    Manages OAuth state parameters with:
    - One-time use enforcement
    - 10-minute expiration
    - Provider and redirect metadata storage
    - Audit logging
    """

    # Expiration time (seconds)
    STATE_EXPIRY = 600  # 10 minutes

    def __init__(self, redis: Redis):
        """Initialize SSO state manager.

        Args:
            redis: Async Redis client instance.
        """
        self._redis = redis

    async def create_state(
        self,
        provider: str,
        redirect_uri: str,
        extra_data: dict[str, Any] | None = None,
    ) -> str:
        """Create and store SSO state for OAuth flow.

        Args:
            provider: OAuth provider name (google, github, etc.).
            redirect_uri: Callback URI after OAuth.
            extra_data: Additional metadata to store.

        Returns:
            Generated state token.
        """
        state = TokenGenerator.generate_sso_state()

        # Build state data
        state_data: dict[str, Any] = {
            "provider": provider,
            "redirect_uri": redirect_uri,
        }
        if extra_data:
            state_data["extra_data"] = extra_data

        # Store in Redis with expiration
        key = f"sso_state:{state}"
        await self._redis.setex(
            key,
            self.STATE_EXPIRY,
            json_dumps(state_data, as_str=True),
        )

        logger.debug(
            "Created SSO state",
            extra={
                "operation": "create_sso_state",
                "provider": provider,
                "expiry_seconds": self.STATE_EXPIRY,
            },
        )

        return state

    async def validate_state(self, state: str) -> dict[str, Any] | None:
        """Validate SSO state (one-time use, 10-min expiry).

        Args:
            state: The state parameter from the OAuth callback.

        Returns:
            State metadata dict if valid, None if expired/used/invalid.
        """
        key = f"sso_state:{state}"

        # Get and delete state data atomically to prevent race conditions
        data = await self._redis.eval(_GETDEL_LUA, 1, key)  # type: ignore[misc]

        if data is None:
            logger.debug(
                "SSO state validation failed: not found or expired",
                extra={"operation": "validate_sso_state", "found": False},
            )
            return None

        try:
            state_data = cast(dict[str, Any], json_loads(data))
            logger.debug(
                "SSO state validated successfully",
                extra={
                    "operation": "validate_sso_state",
                    "provider": state_data.get("provider"),
                },
            )
            return state_data
        except ValueError:
            logger.warning(
                "SSO state validation failed: invalid JSON",
                extra={"operation": "validate_sso_state"},
            )
            return None


class MagicLinkManager:
    """Async magic link token management with Redis.

    Manages magic link tokens with:
    - One-time use enforcement
    - 15-minute expiration
    - Email and action metadata storage
    - Audit logging
    """

    # Expiration time (seconds)
    TOKEN_EXPIRY = 900  # 15 minutes

    def __init__(self, redis: Redis):
        """Initialize magic link manager.

        Args:
            redis: Async Redis client instance.
        """
        self._redis = redis

    async def create_token(
        self,
        email: str,
        action: str,
        extra_data: dict[str, Any] | None = None,
    ) -> str:
        """Create and store magic link token.

        Args:
            email: Target email address.
            action: Token action (login, verify, reset).
            extra_data: Additional metadata to store.

        Returns:
            Generated token.
        """
        token = TokenGenerator.generate_magic_link_token()

        # Build token data
        token_data: dict[str, Any] = {
            "email": email,
            "action": action,
        }
        if extra_data:
            token_data["extra_data"] = extra_data

        # Hash token before storing in Redis to prevent plaintext exposure
        hashed_token = hash_token(token)

        # Store in Redis with expiration
        key = f"magic_link:{hashed_token}"
        await self._redis.setex(
            key,
            self.TOKEN_EXPIRY,
            json_dumps(token_data, as_str=True),
        )

        logger.debug(
            "Created magic link token",
            extra={
                "operation": "create_magic_link",
                "action": action,
                "expiry_seconds": self.TOKEN_EXPIRY,
            },
        )

        return token

    async def peek_token(self, token: str) -> dict[str, Any] | None:
        """Peek at a magic link token without consuming it.

        Args:
            token: The magic link token to peek at.

        Returns:
            Token metadata dict if valid, None if expired/used/invalid.
        """
        hashed_token = hash_token(token)
        key = f"magic_link:{hashed_token}"

        data = await self._redis.get(key)

        if data is None:
            return None

        try:
            return cast(dict[str, Any], json_loads(data))
        except ValueError:
            return None

    async def consume_token(self, token: str) -> bool:
        """Consume a magic link token (delete it).

        Args:
            token: The magic link token to consume.

        Returns:
            True if the token was successfully consumed, False if it was already gone.
        """
        hashed_token = hash_token(token)
        key = f"magic_link:{hashed_token}"

        # delete returns the number of keys removed (1 if it existed, 0 otherwise)
        result = await self._redis.delete(key)
        return int(result) > 0 if result is not None else False

    async def validate_token(self, token: str) -> dict[str, Any] | None:
        """Validate magic link token (one-time use, 15-min expiry).

        Args:
            token: The magic link token to validate.

        Returns:
            Token metadata dict if valid, None if expired/used/invalid.
        """
        hashed_token = hash_token(token)
        key = f"magic_link:{hashed_token}"

        # Get and delete token data atomically to prevent race conditions
        data = await self._redis.eval(_GETDEL_LUA, 1, key)  # type: ignore[misc]

        if data is None:
            logger.debug(
                "Magic link validation failed: not found or expired",
                extra={"operation": "validate_magic_link", "found": False},
            )
            return None

        try:
            token_data = cast(dict[str, Any], json_loads(data))
            logger.debug(
                "Magic link validated successfully",
                extra={
                    "operation": "validate_magic_link",
                    "action": token_data.get("action"),
                },
            )
            return token_data
        except ValueError:
            logger.warning(
                "Magic link validation failed: invalid JSON",
                extra={"operation": "validate_magic_link"},
            )
            return None


# Dependency injection factories


def get_sso_state_manager(redis: Redis) -> SSOStateManager:
    """Get SSOStateManager instance for dependency injection.

    Args:
        redis: Async Redis client from dependency.

    Returns:
        SSOStateManager instance.

    Example:
        @router.get("/auth/google")
        async def google_login(
            redis: Redis = Depends(get_redis_client),
            sso_manager: SSOStateManager = Depends(
                lambda r=Depends(get_redis_client): get_sso_state_manager(r)
            )
        ):
            state = await sso_manager.create_state(...)
    """
    return SSOStateManager(redis=redis)


def get_magic_link_manager(redis: Redis) -> MagicLinkManager:
    """Get MagicLinkManager instance for dependency injection.

    Args:
        redis: Async Redis client from dependency.

    Returns:
        MagicLinkManager instance.
    """
    return MagicLinkManager(redis=redis)
