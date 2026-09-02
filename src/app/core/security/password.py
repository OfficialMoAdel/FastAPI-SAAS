"""Password hashing and verification utilities.

Uses dedicated thread pools to prevent blocking the event loop during
computationally intensive bcrypt operations.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor

import bcrypt

from src.app.core.config import settings
from src.app.core.logger import get_logger

_jwt_logger = get_logger(__name__)

_bcrypt_executor: ThreadPoolExecutor | None = None

# bcrypt silently truncates secrets beyond 72 bytes (A7 FIX)
_BCRYPT_MAX_SECRET_BYTES = 72


def _get_bcrypt_executor() -> ThreadPoolExecutor:
    global _bcrypt_executor
    if _bcrypt_executor is None:
        _bcrypt_executor = ThreadPoolExecutor(
            max_workers=settings.crypt.BCRYPT_THREAD_POOL_SIZE, thread_name_prefix="bcrypt_auth"
        )
    return _bcrypt_executor


def _normalize_secret(password: str) -> bytes:
    """Return the bcrypt secret bytes for a password (A7 FIX).

    bcrypt silently truncates secrets longer than 72 bytes, discarding the
    tail entropy. Passwords over the limit are pre-hashed with SHA-256 and
    base64-encoded (44 ASCII bytes), which bcrypt accepts without truncation.
    The same normalization is applied by both get_password_hash() and
    verify_password(), so existing hashes for passwords <= 72 bytes remain
    valid.
    """
    encoded = password.encode("utf-8")
    if len(encoded) <= _BCRYPT_MAX_SECRET_BYTES:
        return encoded
    return base64.b64encode(hashlib.sha256(encoded).digest())


def needs_rehash(hashed_password: str) -> bool:
    """Check whether a stored hash uses the configured bcrypt cost (A7 FIX).

    Parses the cost factor from a modular-crypt-format bcrypt hash
    (``$2b$<rounds>$...``) and compares it against ``settings.crypt.BCRYPT_ROUNDS``.
    Callers can use this to transparently rehash on successful login when the
    configured cost has been raised.

    Returns False for malformed hashes (nothing to upgrade from safely).
    """
    try:
        rounds = int(hashed_password.split("$")[2])
    except (IndexError, ValueError):
        return False
    return rounds != int(settings.crypt.BCRYPT_ROUNDS)


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password asynchronously using dedicated thread pool.

    Uses the bcrypt thread pool to prevent blocking the event loop
    during password verification. This is critical under high concurrency
    as bcrypt operations take ~50-100ms.

    SECURITY FIX: Added exception handling for malformed hashes to prevent crashes.
    A7 FIX: >72-byte passwords are SHA-256 pre-hashed instead of silently truncated.
    """
    try:
        loop = asyncio.get_running_loop()
        result: bool = await loop.run_in_executor(
            _get_bcrypt_executor(), bcrypt.checkpw, _normalize_secret(plain_password), hashed_password.encode()
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

    A7 FIX: >72-byte passwords are SHA-256 pre-hashed instead of silently truncated.
    """
    loop = asyncio.get_running_loop()
    hashed: bytes = await loop.run_in_executor(
        _get_bcrypt_executor(),
        bcrypt.hashpw,
        _normalize_secret(password),
        bcrypt.gensalt(rounds=settings.crypt.BCRYPT_ROUNDS),
    )
    return hashed.decode()
