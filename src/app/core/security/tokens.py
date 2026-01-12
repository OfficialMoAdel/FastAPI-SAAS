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

import json
import logging
import secrets
from typing import Any, cast

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


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
        state_data = {
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
            json.dumps(state_data),
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

        # Get state data
        data = await self._redis.get(key)

        if data is None:
            logger.debug(
                "SSO state validation failed: not found or expired",
                extra={"operation": "validate_sso_state", "found": False},
            )
            return None

        # Delete immediately (one-time use)
        await self._redis.delete(key)

        try:
            state_data = cast(dict[str, Any], json.loads(data))
            logger.debug(
                "SSO state validated successfully",
                extra={
                    "operation": "validate_sso_state",
                    "provider": state_data.get("provider"),
                },
            )
            return state_data
        except json.JSONDecodeError:
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
        token_data = {
            "email": email,
            "action": action,
        }
        if extra_data:
            token_data["extra_data"] = extra_data

        # Store in Redis with expiration
        key = f"magic_link:{token}"
        await self._redis.setex(
            key,
            self.TOKEN_EXPIRY,
            json.dumps(token_data),
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

    async def validate_token(self, token: str) -> dict[str, Any] | None:
        """Validate magic link token (one-time use, 15-min expiry).

        Args:
            token: The magic link token to validate.

        Returns:
            Token metadata dict if valid, None if expired/used/invalid.
        """
        key = f"magic_link:{token}"

        # Get token data
        data = await self._redis.get(key)

        if data is None:
            logger.debug(
                "Magic link validation failed: not found or expired",
                extra={"operation": "validate_magic_link", "found": False},
            )
            return None

        # Delete immediately (one-time use)
        await self._redis.delete(key)

        try:
            token_data = cast(dict[str, Any], json.loads(data))
            logger.debug(
                "Magic link validated successfully",
                extra={
                    "operation": "validate_magic_link",
                    "action": token_data.get("action"),
                },
            )
            return token_data
        except json.JSONDecodeError:
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
