"""Composite Auth-Prefetch helper (P1-1 Optimization).

Executes token blacklist verification, session validity verification, and
RBAC permission bitmask pre-fetching in a single Redis round-trip (Lua EVALSHA).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.rbac.dependencies import _get_bitmask_cache_key
from src.app.core.security.session_manager import session_manager
from src.app.core.security.token_blacklist_cache import (
    BLACKLIST_KEY,
    NEGATIVE_CACHE_KEY,
    NEGATIVE_CACHE_TTL,
    _circuit_breaker,
    _extract_blacklist_member,
    _get_redis_client,
    _token_cache_key,
)
from src.app.core.utils.lua_manager import get_lua_manager

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)


async def execute_composite_auth_prefetch(
    token: str,
    jti: str | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
    tenant_id: int | UUID | None = None,
    check_bitmask: bool = True,
    redis_client: Redis | None = None,
) -> tuple[bool | None, bool | None, int | None]:
    """Execute composite auth prefetch in single Redis round-trip (1 RTT).

    Args:
        token: JWT access token string
        jti: JWT token ID claim (if verified/available)
        user_id: Authenticated user ID (if resolved)
        session_id: Session ID claim (sid or jti)
        tenant_id: Tenant UUID or ID (if resolved)
        check_bitmask: Whether to prefetch the RBAC permission bitmask
        redis_client: Optional explicit Redis client instance

    Returns:
        tuple[is_blacklisted, session_valid, bitmask]:
            - is_blacklisted: True (revoked), False (valid), None (Redis unavailable/errored/skipped)
            - session_valid: True (active), False (revoked/expired), None (skipped/unavailable/errored)
            - bitmask: int bitmask if cached in Redis, None if cache miss or unavailable
    """
    if redis_client is None:
        redis_client = _get_redis_client()

    if redis_client is None or not _circuit_breaker.is_closed:
        return None, None, None

    negative_key = f"{NEGATIVE_CACHE_KEY}{_token_cache_key(token, jti)}"
    blacklist_key = BLACKLIST_KEY
    session_hash_key = f"{session_manager.SESSION_HASH_PREFIX}{user_id}" if user_id is not None else ""
    bitmask_key = (
        _get_bitmask_cache_key(user_id, tenant_id)
        if (check_bitmask and user_id is not None and tenant_id is not None)
        else ""
    )

    member = _extract_blacklist_member(token, jti)
    now = int(time.time())
    neg_ttl = getattr(settings.r1_unified, "TOKEN_NEGATIVE_CACHE_TTL", NEGATIVE_CACHE_TTL)

    keys = [negative_key, blacklist_key, session_hash_key, bitmask_key]
    args = [
        member,
        str(now),
        str(neg_ttl),
        str(session_id or ""),
        "1" if bitmask_key else "0",
    ]

    try:
        raw_result = await get_lua_manager().execute(
            redis_client,
            "composite_auth_prefetch",
            keys=keys,
            args=args,
        )

        # Parse Lua return: {err (0/1), is_blacklisted (0/1/-1), session_valid (0/1/-1), bitmask (str/false)}
        if not isinstance(raw_result, list | tuple) or len(raw_result) < 4:
            return None, None, None

        err_flag = raw_result[0]
        if err_flag != 0:
            logger.debug("Composite auth prefetch returned error flag from Lua; using fallback paths")
            return None, None, None

        await _circuit_breaker.record_success()

        is_blacklisted: bool | None = None
        session_valid: bool | None = None
        bitmask: int | None = None

        # 1. Blacklist check (0=clean, 1=blacklisted, -1=skipped)
        raw_blacklisted = raw_result[1]
        if raw_blacklisted in (0, 1, "0", "1"):
            is_blacklisted = bool(int(raw_blacklisted))

        # 2. Session validity (1=valid, 0=revoked/missing, -1=skipped)
        raw_session = raw_result[2]
        if raw_session in (0, 1, "0", "1"):
            session_valid = bool(int(raw_session))

        # 3. Bitmask (cached integer string, or false/nil if not cached)
        raw_bitmask = raw_result[3]
        if raw_bitmask not in (False, None, b"", ""):
            try:
                bitmask_str = raw_bitmask.decode() if isinstance(raw_bitmask, bytes) else str(raw_bitmask)
                bitmask = int(bitmask_str)
            except (ValueError, TypeError):
                bitmask = None

        return is_blacklisted, session_valid, bitmask

    except Exception as e:
        await _circuit_breaker.record_failure(e)
        logger.warning("Composite auth prefetch failed, falling back to sequential checks", error=str(e))
        return None, None, None
