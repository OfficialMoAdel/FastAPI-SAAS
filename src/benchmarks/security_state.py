"""Redis counter namespaces a measured phase can leave behind, and how to drop them.

Split out of ``driver_count_io.py`` so the namespace list is importable
without booting the harness: ``driver_count_io`` calls ``os.chdir`` and forces
``QUERY_PROFILER_ENABLED`` at import time, which a test session must not
inherit. This module has no import-time side effects.

TWO ALLOWED CLEANUP FORMS, and only two.

1. ``clear_security_state()`` — targeted, deletes only the namespaces listed in
   ``SECURITY_KEY_PATTERNS``. Every scan carries a MATCH argument.
2. ``flush_security_state()`` — FLUSHDB on a DEDICATED instance, followed by a
   fixed settle sleep.

Anything else is forbidden as pre-timing cleanup, in particular a keyspace-wide
``SCAN`` with no MATCH. That walks every key on the instance in the window
immediately before a clock starts, evicting and re-warming cache and connection
state in precisely the window the measurement is meant to observe. The
contamination scales with keyspace size, so it grows as the instance is used
more, and it is then read as a regression in whatever endpoint was measured
next. This is the reason the two forms above are pinned by tests rather than
left to reviewer vigilance.

``tests/benchmarks/test_security_state_coverage.py`` asserts the namespace list
stays in step with what the application writes, in both directions.
``tests/unit/benchmarks/test_clear_security_state.py`` writes counters through
the real increment path against a live Redis and reads them back after a
cleanup, and pins the two forms above.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Final

# Every Redis key namespace a measured phase can write, in the form
# ``scan_iter(match=...)`` accepts.
#
# The two ENGINE patterns are the load-bearing pair, and the reason the auth
# budget needs no pattern of its own is that it has no prefix of its own. Both
# engines prepend a fixed string to whatever logical key they are handed, and
# ``rate_policy.consume`` / ``RateLimiter.check_rate_limit_algorithm`` are the
# only dispatchers, so EVERY rate-limit counter in the app lands in one of
# these two namespaces:
#
#   GCRA          ``gcra:<logical>``               gcra_rate_limit.py:141
#   fixed window  ``ratelimit:<logical>:<window>``  rate_limit.py:49 (+ a
#                 window-start suffix the ``rate_limit_fixed_window`` Lua
#                 script appends from the Redis clock)
#
# The logical key for the auth budget is
# ``rate_limit:user:<identity>:budget:auth`` (``budgets.py::_leg_key``), so the
# two physical keys are, as observed against a live Redis:
#
#   gcra:rate_limit:user:203.0.113.77:budget:auth
#   ratelimit:rate_limit:user:203.0.113.77:budget:auth:1791138000
#
# Note ``ratelimit:`` has NO underscore, and note that neither physical key
# starts with ``rate_limit:``. A ``rate_limit:*`` pattern therefore matches
# NEITHER of them: Redis glob patterns anchor at position 0, so a match is a
# prefix test, not a substring test. Do not "simplify" this list by replacing
# ``ratelimit:*`` with ``rate_limit:*``.
#
# ``tests/unit/benchmarks/test_clear_security_state.py`` writes these keys
# through the real increment path against a live Redis and reads the counter
# back after a cleanup; it fails if either engine prefix is missing.
SECURITY_KEY_PATTERNS: tuple[str, ...] = (
    # auto_ban.py: BAN_KEY_PREFIX. NOTE ``security:ban:*`` does NOT also match
    # ``security:ban_count:*`` — the character after ``security:ban`` is ``_``,
    # not ``:`` — so the count namespace needs its own entry.
    "security:ban:*",
    # auto_ban.py: BAN_COUNT_KEY_PREFIX.
    "security:ban_count:*",
    # auto_ban.py: VIOLATIONS_KEY_PREFIX. Also NOT under ``security:ban:*``.
    "security:violations:*",
    # gcra_rate_limit.py:141 — ``f"gcra:{key}"``. Every counter the GCRA engine
    # meters, including every budget leg.
    "gcra:*",
    # rate_limit.py:49 — ``f"ratelimit:{key}"``, plus the ``:<window_start>``
    # suffix the fixed-window Lua script appends. Every counter the fixed-window
    # engine meters, including every budget leg.
    "ratelimit:*",
    # MFAService._check_mfa_lockout: the durable per-account MFA backstop
    # (``mfa_attempts:{user_id}``, incremented by the ``mfa_lockout_check``
    # Lua script). It refuses with 401 rather than 429, so it is invisible to
    # every rate-limit pattern above — and it is the one that actually leaked
    # between phases.
    "mfa_attempts:*",
)
# auto_ban.py's OTHER prefix, ``_BAN_KEY_PREFIX`` (``ban:{ip}``), has NO entry
# here on purpose: that key names the in-process ban L1/negative cache
# (``_ban_negative_cache``), which is never written to Redis. The previous list
# carried a ``ban:*`` pattern for it, which matched nothing and read as
# coverage. If that cache is ever made Redis-backed, add the pattern here and
# a key builder in the coverage test at the same time.

#: The env var that declares a Redis instance disposable enough to FLUSHDB.
#: There is no default: unset means "nobody has said this may be emptied".
DEDICATED_INSTANCE_ENV_VAR: Final[str] = "PERF_REDIS_DEDICATED"

#: Values accepted as an assertion of dedication. Narrow on purpose - a typo
#: must not read as consent to empty a database.
TRUTHY_ENV_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})

#: How long to wait after FLUSHDB before a phase may start timing, in seconds.
#: Fixed, non-zero, and not a tunable: see ``flush_security_state``.
FLUSHDB_SETTLE_SECONDS: Final[float] = 2.0


async def clear_security_state() -> int:
    """Delete every key matching SECURITY_KEY_PATTERNS. Returns the count deleted.

    Call this between measured phases so one cannot poison the next. Read the
    comment on SECURITY_KEY_PATTERNS before adding an entry, and add the
    coverage assertion with it.

    This is the TARGETED cleanup form. Every scan carries a MATCH argument, so
    the walk is bounded by the size of the namespaces being cleared rather than
    by the size of the whole keyspace. A keyspace-wide ``SCAN`` here would walk
    every key in the instance in the window immediately before a clock starts,
    evicting and re-warming cache and connection state in exactly the window the
    measurement is meant to observe.

    The client is built from ``settings.REDIS_CACHE_URL`` — the same value the
    application itself connects with — rather than from the ``REDIS_CACHE_*``
    env vars directly. Reading the settings object at call time (not at import
    time) is what lets the test session's per-worker database-index override
    apply: a harness that assembled its own client from the env vars would
    clean a *different* database from the one the counters were written to,
    and would then report a clean phase over an untouched counter.

    Note the access is on the ``settings`` INSTANCE. ``REDIS_CACHE_URL`` is a
    ``@computed_field @property``, so reading it off the ``RedisCacheSettings``
    class yields the property object rather than a URL — and the test session
    replaces that property wholesale to redirect to an isolated database, so
    instance access is what picks the redirect up.
    """
    import redis.asyncio as aioredis

    from src.app.core.config import settings

    rc = aioredis.from_url(settings.REDIS_CACHE_URL)
    deleted = 0
    try:
        for pattern in SECURITY_KEY_PATTERNS:
            async for key in rc.scan_iter(match=pattern, count=500):
                await rc.delete(key)
                deleted += 1
    finally:
        await rc.aclose()
    return deleted


class NotDedicatedInstance(RuntimeError):
    """FLUSHDB was requested against an instance nobody marked as disposable.

    Carries the variable that would have to be set, so the message names the
    action rather than describing a policy.
    """

    def __init__(self, variable: str) -> None:
        super().__init__(
            f"{variable} is not set to a truthy value, so this instance is not marked "
            f"dedicated. FLUSHDB drops EVERY key in the selected database - session "
            f"stores, rate-limit state, queues and caches alike - so it is refused "
            f"here unless the instance exists to be emptied. Use clear_security_state() "
            f"for targeted cleanup, or set {variable}=true only on a disposable "
            f"measurement instance."
        )
        self.variable = variable


def _is_dedicated() -> bool:
    """Whether the operator has declared this Redis instance disposable.

    There is deliberately no default. An unset variable means "nobody has said
    this instance may be emptied", which is the safe reading: defaulting to
    True would let an unset environment destroy whatever the harness happens to
    be pointed at, and defaulting to False would be indistinguishable from the
    deliberate refusal this function exists to express.
    """
    return os.environ.get(DEDICATED_INSTANCE_ENV_VAR, "").strip().lower() in TRUTHY_ENV_VALUES


async def flush_security_state(
    *,
    clock: Callable[[float], Awaitable[None]] = asyncio.sleep,
    settle: float = FLUSHDB_SETTLE_SECONDS,
) -> None:
    """Empty the whole database, then wait ``settle`` seconds for it to go quiet.

    The second allowed cleanup form, and the more aggressive one: FLUSHDB drops
    every key in the selected database, not only the security namespaces. That
    is what makes it appropriate for a dedicated measurement instance and
    inappropriate everywhere else, which is why it is gated rather than merely
    discouraged.

    ``settle`` is a FIXED CONSTANT, not a parameter the caller tunes to taste
    and not zero. Zero would measure a phase whose caches were emptied
    microseconds earlier, so every read pays a cold miss that the production
    system would not pay; the contamination would then be filed as a
    regression. The sleep exists so the measurement observes a warm system
    again. Pass a fake ``clock`` to observe the ordering without waiting.

    Raises ``NotDedicatedInstance`` before touching Redis when the instance is
    not marked dedicated, so a refusal costs nothing.
    """
    import redis.asyncio as aioredis

    from src.app.core.config import settings

    if not _is_dedicated():
        raise NotDedicatedInstance(DEDICATED_INSTANCE_ENV_VAR)

    rc = aioredis.from_url(settings.REDIS_CACHE_URL)
    try:
        await rc.flushdb()
    finally:
        await rc.aclose()
    # Awaited here, deliberately: this returns only once the instance has gone
    # quiet, so a caller that starts its clock after awaiting this cannot be
    # measuring a database that is still settling.
    await clock(settle)
