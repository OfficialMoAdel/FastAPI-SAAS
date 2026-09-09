"""Shared cache-failure guard (M-10).

Four modules (tenants, users/cache, feature_flags, rate_limits) grew
copy-pasted ``try: redis_op() except Exception: logger.warning(...)`` blocks
around every cache read/write. This contextmanager is the one home for that
policy: swallow infra failures, log once with the caller's operation label
and structured kwargs, and continue so the DB/authority path runs.

Non-goals: NOT for auth-critical caches with their own fail-closed policy
(token blacklist), NOT for cache failures that must invalidate correctness
(tenant invalidation — Q-M8 has its own retry/poison-key handling).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from src.app.core.logger import get_logger

logger = get_logger(__name__)


@contextmanager
def cache_guard(op: str, **log_fields: Any) -> Iterator[None]:
    """Run a cache operation, converting infra failures into a warning.

    Usage (replaces the copy-pasted try/except blocks)::

        with cache_guard("feature_flag_get", key=key):
            cached = await redis.get(cache_key)
            ...

    On exception: logs ``cache_<op>_failed`` with ``op``-specific fields and
    returns cleanly — the caller proceeds to its authority path. The
    exception does NOT propagate.
    """
    try:
        yield
    except Exception as e:  # noqa: BLE001 — the whole point is swallowing infra noise
        logger.warning(f"cache_{op}_failed", error=str(e), **log_fields)
