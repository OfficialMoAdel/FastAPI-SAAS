"""Every Redis counter a perf phase can write must be clearable between phases.

`src/benchmarks/security_state.py::clear_security_state` drops
`SECURITY_KEY_PATTERNS` so one measured phase cannot poison the next. That
list is only trustworthy if it stays in step with the namespaces the
application actually writes, and a hand-maintained glob list drifts silently:
a new counter appears, the harness keeps running, and the contamination shows
up weeks later as an unexplained latency cliff.

So this asserts the two directions that matter:

1. COVERAGE — every namespace built by real application code is matched by at
   least one pattern. A new counter added without a pattern fails here.
2. REACHABILITY — every pattern is matched by at least one real key, so the
   list cannot accumulate dead entries that look like coverage.

The keys are produced by the production builders (`budgets._leg_key`, the
`auto_ban` prefix constants, the MFA lockout key), never by string literals
written here, so this cannot drift from the code it is checking.

LOGICAL vs PHYSICAL. A budget leg produces a *logical* key
(`rate_limit:user:<identity>:budget:auth`) and each engine prepends its own
prefix, so what Redis actually holds is one of two *physical* keys. Nothing
ever writes the logical key on its own. This distinction is load-bearing: a
pattern list built around the logical key looks complete while missing every
fixed-window counter in the app, because the fixed-window engine prepends
`ratelimit:` — no underscore. Only the physical forms are enumerated below.

No Redis required: `fnmatch` reproduces the glob semantics `SCAN MATCH` uses.
The prefixes themselves are re-derived from the incrementing source by
`tests/unit/benchmarks/test_clear_security_state.py::test_engine_prefixes_are_read_from_the_incrementing_code`,
which fails if either engine stops using the prefix assumed here.
"""

from __future__ import annotations

import fnmatch

import pytest

from src.app.core.security.auto_ban import (
    BAN_COUNT_KEY_PREFIX,
    BAN_KEY_PREFIX,
    VIOLATIONS_KEY_PREFIX,
    _ban_cache_key,
)
from src.app.core.security.budgets import BUDGETS, _leg_key
from src.benchmarks.security_state import SECURITY_KEY_PATTERNS

#: gcra_rate_limit.py:141 — ``f"gcra:{key}"``, no suffix.
GCRA_PREFIX = "gcra:"
#: rate_limit.py:49 — ``f"ratelimit:{key}"``. No underscore, unlike the logical
#: key's ``rate_limit:``. The Lua script then appends ``:{window_start}`` read
#: from the Redis clock, so the suffix is represented by a fixed representative
#: rather than omitted.
FIXED_WINDOW_PREFIX = "ratelimit:"
WINDOW_SUFFIX = ":1791138000"


def _budget_keys() -> set[str]:
    """One PHYSICAL key per budget leg, in each limiter engine's form.

    Under GCRA the physical key is `gcra:` + the logical key
    (gcra_rate_limit.py); under the fixed window it is `ratelimit:` + the
    logical key + a window-start suffix (rate_limit.py). A pattern set that
    knows only one engine's form silently misses every counter the other one
    meters — which is exactly what happened: an earlier version of this list
    carried the logical `rate_limit:*` namespace, matched neither engine, and
    was reported as covering the auth budget.
    """
    keys: set[str] = set()
    for budget_def in BUDGETS.values():
        for leg in budget_def.legs:
            logical = _leg_key(leg, leg.kind.render("1.2.3.4"))
            keys.add(GCRA_PREFIX + logical)
            keys.add(FIXED_WINDOW_PREFIX + logical + WINDOW_SUFFIX)
    return keys


def _logical_budget_keys() -> set[str]:
    """The logical keys, which are NOT Redis keys and must not be treated as such."""
    return {_leg_key(leg, leg.kind.render("1.2.3.4")) for budget_def in BUDGETS.values() for leg in budget_def.legs}


def _mfa_lockout_keys() -> set[str]:
    """The durable per-account MFA backstop (MFAService._check_mfa_lockout)."""
    return {f"mfa_attempts:{user_id}" for user_id in (1, 2)}


def _auto_ban_keys() -> set[str]:
    """The Redis-backed auto_ban counters.

    ``_BAN_KEY_PREFIX`` (``ban:{ip}``) is deliberately absent: it names the
    in-process ban L1/negative cache, never a Redis key, so it is neither a
    pattern nor a key here.
    """
    ip = "1.2.3.4"
    return {
        BAN_KEY_PREFIX.format(ip=ip),
        BAN_COUNT_KEY_PREFIX.format(ip=ip),
        VIOLATIONS_KEY_PREFIX.format(ip=ip),
    }


def _all_known_keys() -> dict[str, set[str]]:
    return {
        "security budget legs": _budget_keys(),
        "auto_ban counters": _auto_ban_keys(),
        "mfa durable lockout": _mfa_lockout_keys(),
    }


def _covering_pattern(key: str) -> str | None:
    for pattern in SECURITY_KEY_PATTERNS:
        if fnmatch.fnmatchcase(key, pattern):
            return pattern
    return None


@pytest.mark.parametrize("namespace", sorted(_all_known_keys()))
def test_every_known_namespace_is_covered(namespace: str) -> None:
    uncovered = sorted(k for k in _all_known_keys()[namespace] if _covering_pattern(k) is None)
    assert not uncovered, (
        f"{namespace} keys not matched by SECURITY_KEY_PATTERNS: {uncovered}. "
        f"A phase that writes them leaves state behind for the next phase."
    )


def test_every_engine_form_of_a_budget_key_is_covered() -> None:
    """Both physical forms of the auth budget, under both engines.

    The original bead claim was that the auth budget was an uncovered
    namespace, and an early revision of this file "refuted" it by observing
    that ``_leg_key`` yields ``rate_limit:user:<identity>:budget:auth``, which
    a ``rate_limit:*`` pattern matches. That reasoning was wrong in a way worth
    pinning: the logical key is never written to Redis. Each engine prepends a
    prefix, so both of these — and only these — are what a phase leaves behind:

        gcra:rate_limit:user:<identity>:budget:auth
        ratelimit:rate_limit:user:<identity>:budget:auth:<window_start>

    ``rate_limit:*`` matches NEITHER, because a Redis glob anchors at position
    0. Asserting the covering pattern of each physical form is what stops that
    false refutation from being re-derived a second time.
    """
    auth = BUDGETS[next(a for a in BUDGETS if a.value == "auth")]
    logical = _leg_key(auth.legs[0], auth.legs[0].kind.render("1.2.3.4"))
    assert logical.startswith("rate_limit:")
    assert _covering_pattern(GCRA_PREFIX + logical) == "gcra:*"
    assert _covering_pattern(FIXED_WINDOW_PREFIX + logical + WINDOW_SUFFIX) == "ratelimit:*"
    assert "rate_limit:*" not in SECURITY_KEY_PATTERNS, (
        "the logical prefix matches no physical counter; keeping it in the list "
        "reads as coverage while covering nothing"
    )


def test_logical_budget_keys_are_not_treated_as_redis_keys() -> None:
    """No logical budget key may be covered by any pattern.

    If one ever were, this file would be asserting coverage of a key the
    application never writes — the exact confusion the physical-key split
    above exists to prevent.
    """
    for logical in sorted(_logical_budget_keys()):
        assert _covering_pattern(logical) is None or logical in _budget_keys(), (
            f"{logical} is not a physical key but is covered by "
            f"{_covering_pattern(logical)!r}; the pattern list is crediting a namespace nothing writes"
        )


def test_no_dead_patterns() -> None:
    """Every pattern matches a key the application can really produce."""
    real = set().union(*_all_known_keys().values())
    dead = sorted(p for p in SECURITY_KEY_PATTERNS if not any(fnmatch.fnmatchcase(k, p) for k in real))
    assert not dead, f"SECURITY_KEY_PATTERNS entries that match no real key: {dead}"


def test_ban_count_is_not_covered_by_the_ban_pattern() -> None:
    """`security:ban:*` must not be mistaken for covering `security:ban_count:*`.

    This is the reason the count namespace needs its own entry: the character
    after `security:ban` is `_`, not `:`, so the ban glob does not reach it.
    Removing the `ban_count` pattern would otherwise pass unnoticed until a
    ban count leaked across phases.
    """
    assert not fnmatch.fnmatchcase(BAN_COUNT_KEY_PREFIX.format(ip="1.2.3.4"), "security:ban:*")
    assert _covering_pattern(BAN_COUNT_KEY_PREFIX.format(ip="1.2.3.4")) == "security:ban_count:*"


def test_mfa_lockout_is_covered() -> None:
    """The lockout refuses with 401, so no other namespace reveals it exists."""
    assert _covering_pattern("mfa_attempts:1") == "mfa_attempts:*"


def test_l1_ban_cache_key_has_no_redis_pattern() -> None:
    """`ban:{ip}` is the in-process L1/negative cache, not a Redis key.

    The original list carried a `ban:*` pattern for it. It matched nothing, so
    it bought nothing while reading as coverage. Asserting its absence stops
    the pattern coming back; if the cache is ever made Redis-backed this fails
    and the correct fix is to add the pattern plus a key builder above, not to
    weaken the assertion.
    """
    l1_key = _ban_cache_key("1.2.3.4")
    assert l1_key == "ban:1.2.3.4"
    assert not any(fnmatch.fnmatchcase(l1_key, p) for p in SECURITY_KEY_PATTERNS)
    assert "ban:*" not in SECURITY_KEY_PATTERNS
