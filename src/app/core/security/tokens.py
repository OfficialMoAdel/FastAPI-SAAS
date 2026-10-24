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
import hmac
import secrets
from functools import lru_cache

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


_TOKEN_PEPPER_DOMAIN = "saas-token-v1"


@lru_cache(maxsize=1)
def _pepper() -> bytes:
    # I2: SECRET_KEY is required config — no silent default. Fail fast if unset.
    #
    # Memoized like its siblings (`jwt_service._get_signing_key`,
    # `jwt_utils._get_parsed_verification_key`, both `@lru_cache(maxsize=1)`).
    # Measured 0.74 us per call and called at least twice per request
    # (hash_token on the probe + the rotation/blacklist write), so this is a
    # pure saving: SECRET_KEY is boot-validated and immutable for the process
    # lifetime, which is exactly the precondition the siblings already rely on.
    # `clear_pepper_cache` exists for the same reason their clear functions do
    # (tests that patch SECRET_KEY, and any future key rotation).
    from src.app.core.config import settings

    key = settings.SECRET_KEY.get_secret_value()
    if not key:
        raise RuntimeError("SECRET_KEY must be set for token hashing")
    return f"{_TOKEN_PEPPER_DOMAIN}:{key}".encode()


def clear_pepper_cache() -> None:
    """Drop the memoized pepper (tests that patch SECRET_KEY; key rotation)."""
    _pepper.cache_clear()


def hash_token(token: str) -> str:
    """HMAC-SHA256 a token with the server pepper for secure storage (I2).

    Peppered so a leaked token table cannot be reversed by rainbow tables
    for low-entropy tokens (numeric codes, short magic links). The domain
    prefix binds this derivation to tokens only — it is never reused for
    another purpose.
    """
    return hmac.new(_pepper(), token.encode("utf-8"), hashlib.sha256).hexdigest()


def legacy_token_digest(token: str) -> str:
    """The pre-I2 digest: bare SHA-256, no pepper.

    Rows written before the pepper change live under this value. Kept as a
    named seam (not inlined per lookup site) so the legacy format has one
    definition and every dual-lookup path stays in sync with it.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_digest_candidates(token: str) -> tuple[str, str]:
    """Digests to try when LOOKING UP a stored token (I2 migration bridge).

    Token tables are keyed on a digest of the token, so a verify path that
    queries only ``hash_token(t)`` cannot see rows written by the previous
    bare-SHA-256 function — every outstanding credential (magic links,
    password resets, email verifications, API keys) would die on deploy.
    Callers query ``WHERE token IN token_digest_candidates(t)`` instead.

    Writes always use ``hash_token`` (the first entry). A hit on the
    SECOND entry means the row is still in the legacy format; callers that
    hold a writable row may re-hash it to migrate the row forward.
    """
    return (hash_token(token), legacy_token_digest(token))


def verify_token_digest(candidate: str, stored: str) -> bool:
    """Constant-time check of a candidate against a stored digest.

    Accepts the current peppered digest AND the pre-I2 bare SHA-256 digest
    so existing rows keep authenticating until rotated. Returns False on
    any mismatch; timing-safe for both paths.
    """
    return any(hmac.compare_digest(d, stored) for d in token_digest_candidates(candidate))


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
