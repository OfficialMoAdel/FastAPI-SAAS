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
    from src.app.core.security.pending_token_store import PendingTokenStore

    @router.get("/auth/google")
    async def google_login(
        sso_manager: PendingTokenStore = Depends(get_sso_state_manager)
    ):
        state = await sso_manager.create_token(
            {"provider": "google", "redirect_uri": str(request.url)}
        )
        ...
"""

from __future__ import annotations

import hashlib
import secrets

from redis.asyncio import Redis

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


from src.app.core.security.pending_token_store import PendingTokenStore

# Dependency injection factories

def get_sso_state_manager(redis: Redis) -> PendingTokenStore:
    """Get PendingTokenStore instance for SSO state dependency injection.

    Args:
        redis: Async Redis client from dependency.

    Returns:
        PendingTokenStore instance configured for SSO state.
    """
    return PendingTokenStore(
        redis=redis,
        prefix="sso_state",
        ttl_seconds=600,  # 10 minutes
        use_hash=False
    )


def get_magic_link_manager(redis: Redis) -> PendingTokenStore:
    """Get PendingTokenStore instance for Magic Link dependency injection.

    Args:
        redis: Async Redis client from dependency.

    Returns:
        PendingTokenStore instance configured for magic links.
    """
    return PendingTokenStore(
        redis=redis,
        prefix="magic_link",
        ttl_seconds=900,  # 15 minutes
        use_hash=True
    )
