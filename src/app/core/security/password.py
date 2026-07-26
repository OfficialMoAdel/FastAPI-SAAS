"""Password hashing and verification utilities.

Uses dedicated thread pools to prevent blocking the event loop during
computationally intensive bcrypt operations.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import bcrypt

from src.app.core.config import settings
from src.app.core.logger import get_logger

_jwt_logger = get_logger(__name__)

_bcrypt_executor: ThreadPoolExecutor | None = None


def _get_bcrypt_executor() -> ThreadPoolExecutor:
    global _bcrypt_executor
    if _bcrypt_executor is None:
        _bcrypt_executor = ThreadPoolExecutor(
            max_workers=settings.BCRYPT_THREAD_POOL_SIZE, thread_name_prefix="bcrypt_auth"
        )
    return _bcrypt_executor


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
