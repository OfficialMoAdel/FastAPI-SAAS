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

import logging
from typing import TYPE_CHECKING, Any

import jwt
from jwt.exceptions import PyJWTError

from src.app.core.config import settings

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

# Constants matching main security module
JWT_AUDIENCE: str = "fastapi-saas-api"
REQUEST_STATE_KEY: str = "jwt_payload"


def _get_algorithm() -> str:
    """Get the JWT algorithm from settings.

    Returns:
        Algorithm string: 'RS256' for asymmetric or 'HS256' for symmetric.
    """
    return "RS256" if settings.JWT_PRIVATE_KEY else "HS256"


def _get_verification_key() -> str:
    """Get the key used for JWT signature verification.

    Returns:
        Public key for RS256, or SECRET_KEY for HS256.
    """
    if settings.JWT_PRIVATE_KEY:
        # RS256: Use public key for verification
        return settings.JWT_PUBLIC_KEY or ""
    # HS256: Use symmetric secret
    return settings.SECRET_KEY.get_secret_value()


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


def decode_jwt_token(token: str) -> dict[str, Any] | None:
    """Decode and verify a JWT token without database checks.

    This performs cryptographic verification only:
    - Signature validation
    - Expiration check
    - Audience validation

    It does NOT check:
    - Token blacklist (requires Redis)
    - User existence (requires database)
    - Token type validation

    Args:
        token: The JWT token string.

    Returns:
        Decoded payload dict if valid, None if invalid/expired.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            _get_verification_key(),
            algorithms=[_get_algorithm()],
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
    payload = decode_jwt_token(token)

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
    return hasattr(request.state, REQUEST_STATE_KEY) and request.state.jwt_payload is not None
