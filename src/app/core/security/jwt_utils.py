"""JWT claim extraction utilities for request-scoped caching.

This module provides utilities to decode JWT tokens once per request and cache
the decoded payload in request.state. This eliminates redundant JWT decoding
in the request lifecycle (e.g., RateLimitMiddleware + auth dependency).

Performance Impact:
    - JWT decode: ~70μs per call
    - With caching: Only 1 decode per request instead of 2-3
    - Estimated savings: 70-140μs per authenticated request

Usage:
    # In middleware or early request processing:
    payload = await extract_jwt_claims_to_state(request)
    if payload:
        user_id = payload.get("sub")

    # Later in the same request (e.g., auth dependency):
    cached = get_cached_jwt_payload(request)
    if cached:
        # Use cached payload, skip decode
        ...

See Also:
    - src/app/core/security/__init__.py: Main JWT verification with DB checks
    - src/app/middleware/rate_limit_middleware.py: Uses cached payload
    - src/app/modules/auth/dependencies.py: Uses cached payload
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any, Union

import jwt
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.ed448 import Ed448PublicKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from jwt.exceptions import PyJWTError

from src.app.core.config import settings
from src.app.core.logger import get_logger

# Type alias for PyJWT-compatible public key types
JWTPublicKey = Union[RSAPublicKey, EllipticCurvePublicKey, Ed25519PublicKey, Ed448PublicKey]

if TYPE_CHECKING:
    from fastapi import Request

logger = get_logger(__name__)

# JWT_AUDIENCE must be explicitly configured in production.
# Falling back to a generic default in production is a misconfiguration risk.
_raw_audience = settings.JWT_AUDIENCE
if not _raw_audience:
    _env = getattr(settings, "ENVIRONMENT", "development")
    if str(_env).lower() in ("production", "staging"):
        raise RuntimeError(
            "JWT_AUDIENCE is not configured. "
            "Set the JWT_AUDIENCE environment variable before deploying to production/staging. "
            "Leaving this unset allows tokens issued by any service to be accepted."
        )
    # Non-production: use a harmless default and warn loudly
    logger.warning(
        "JWT_AUDIENCE is not set. Defaulting to 'fastapi-saas-api'. "
        "Set JWT_AUDIENCE explicitly before deploying to production."
    )
    _raw_audience = "fastapi-saas-api"
JWT_AUDIENCE: str = _raw_audience
REQUEST_STATE_KEY: str = "jwt_payload"


def _get_algorithm() -> str:
    """Get the JWT algorithm from settings.

    Returns:
        Algorithm string: 'RS256' for asymmetric or 'HS256' for symmetric.
    """
    return "RS256" if settings.JWT_PRIVATE_KEY else "HS256"


def _get_verification_key() -> str:
    """Get the raw key string used for JWT signature verification.

    Returns:
        Public key PEM string for RS256, or SECRET_KEY for HS256.

    Note:
        For RS256, prefer _get_parsed_verification_key() which returns
        a pre-parsed key object for better performance.
    """
    if settings.JWT_PRIVATE_KEY:
        # RS256: Use public key for verification
        return settings.JWT_PUBLIC_KEY or ""
    # HS256: Use symmetric secret
    return settings.SECRET_KEY.get_secret_value()


@lru_cache(maxsize=1)
def _get_parsed_verification_key() -> Union[JWTPublicKey, str]:
    """Get a pre-parsed key for JWT verification (cached singleton).

    For RS256 deployments, this pre-parses the PEM public key into a
    cryptography key object. PyJWT can use this directly, avoiding
    repeated PEM parsing on every jwt.decode() call.

    For HS256 deployments, returns the secret key string directly
    (no parsing overhead for symmetric keys).

    Returns:
        - JWTPublicKey: Pre-parsed public key object for RS256
        - str: Secret key string for HS256

    Performance Impact:
        - RS256: Saves ~10-20μs per decode (PEM parsing cached)
        - HS256: No change (already optimal)

    Security Notes:
        - Only PUBLIC key is cached (safe)
        - Private key never cached
        - lru_cache(maxsize=1) = singleton per process
    """
    if settings.JWT_PRIVATE_KEY:
        # RS256: Pre-parse public key
        key_pem = settings.JWT_PUBLIC_KEY or ""
        if key_pem:
            parsed_key = load_pem_public_key(key_pem.encode())
            # Cast to JWTPublicKey for type safety (PyJWT supports these types)
            if isinstance(parsed_key, RSAPublicKey | EllipticCurvePublicKey | Ed25519PublicKey | Ed448PublicKey):
                return parsed_key
            # Unsupported key type - fall back to string (will let PyJWT handle it)
            return key_pem
        # Fallback: return empty string (will fail validation, as expected)
        return ""
    # HS256: Return symmetric secret directly (no parsing needed)
    return settings.SECRET_KEY.get_secret_value()


def clear_jwt_key_cache() -> None:
    """Clear the cached JWT verification key.

    Useful for testing or when keys are rotated at runtime.
    In production, key rotation typically requires process restart.
    """
    _get_parsed_verification_key.cache_clear()


def extract_token_from_header(authorization: str | None) -> str | None:
    """Extract Bearer token from Authorization header.

    Args:
        authorization: The Authorization header value.

    Returns:
        The token string if valid Bearer format, None otherwise.
    """
    if not authorization:
        return None

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None

    return parts[1]


async def decode_jwt_token(token: str) -> dict[str, Any] | None:
    """Decode and verify a JWT token without database checks.

    This performs cryptographic verification only:
    - Signature validation
    - Expiration check
    - Audience validation

    It does NOT check:
    - Token blacklist (requires Redis)
    - User existence (requires database)
    - Token type validation

    Performance Note:
        Uses pre-parsed key for RS256 to avoid repeated PEM parsing.
        Saves ~10-20μs per decode for asymmetric key deployments.

    Args:
        token: The JWT token string.

    Returns:
        Decoded payload dict if valid, None if invalid/expired.
    """
    try:
        import asyncio

        alg = _get_algorithm()
        # Use pre-parsed key for better performance (especially RS256)
        if alg == "RS256":
            payload: dict[str, Any] = await asyncio.to_thread(
                jwt.decode,
                token,
                _get_parsed_verification_key(),
                algorithms=[alg],
                audience=JWT_AUDIENCE,
            )
        else:
            payload = jwt.decode(
                token,
                _get_parsed_verification_key(),
                algorithms=[alg],
                audience=JWT_AUDIENCE,
            )
        return payload
    except PyJWTError as e:
        logger.debug("JWT decode failed: %s", e)
        return None


async def extract_jwt_claims_to_state(request: Request) -> dict[str, Any] | None:
    """Extract and cache JWT claims in request.state.

    This function:
    1. Checks if payload is already cached (returns immediately if so)
    2. Extracts Bearer token from Authorization header
    3. Decodes and verifies the JWT signature
    4. Caches the payload in request.state.jwt_payload

    Args:
        request: The FastAPI request object.

    Returns:
        The decoded JWT payload dict, or None if:
        - No Authorization header
        - Invalid Bearer format
        - Invalid/expired token
        - Signature verification failed

    Example:
        payload = await extract_jwt_claims_to_state(request)
        if payload:
            user_id = payload.get("sub")
            tenant_id = payload.get("tenant_id")
    """
    # Check cache first
    cached: dict[str, Any] | None = getattr(request.state, REQUEST_STATE_KEY, None)
    if cached is not None:
        return cached

    # Extract token from header
    authorization = request.headers.get("Authorization")
    token = extract_token_from_header(authorization)

    if not token:
        return None

    # Decode and verify
    payload = await decode_jwt_token(token)

    if payload:
        # Cache in request state for reuse
        request.state.jwt_payload = payload
        logger.debug(
            "JWT claims cached in request.state: sub=%s, tenant_id=%s",
            payload.get("sub"),
            payload.get("tenant_id"),
        )

    return payload


def get_cached_jwt_payload(request: Request) -> dict[str, Any] | None:
    """Get cached JWT payload from request.state.

    Use this to check if JWT was already decoded earlier in the request.

    Args:
        request: The FastAPI request object.

    Returns:
        Cached payload dict, or None if not yet decoded.
    """
    return getattr(request.state, REQUEST_STATE_KEY, None)


def has_cached_jwt_payload(request: Request) -> bool:
    """Check if JWT payload is cached in request.state.

    Args:
        request: The FastAPI request object.

    Returns:
        True if payload is cached, False otherwise.
    """
    return getattr(request.state, REQUEST_STATE_KEY, None) is not None
