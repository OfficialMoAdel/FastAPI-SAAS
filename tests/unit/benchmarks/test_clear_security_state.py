"""H-2 verification: a cleanup that leaves a non-zero counter is not a cleanup.

``clear_security_state()`` deletes keys. Asserting that the keys are gone is
NOT a verification — the bead that asked for this test says so explicitly, and
the reason is concrete: a counter that keeps a TTL, or a namespace with a
different prefix, will *look* deleted and still count. So every test here that
clears state then drives the counter back through the real production path and
reads what the engine reports.

Two facts this file pins, both of which were previously wrong:

1. The auth-budget counter IS missed by the cleanup when the operator runs the
   fixed-window engine. Its physical key is
   ``ratelimit:rate_limit:user:<identity>:budget:auth:<window_start>`` — note
   ``ratelimit:`` with no underscore, and a window suffix appended inside the
   Lua script. A ``rate_limit:*`` glob does not match it, because a Redis glob
   is anchored at position 0. Only ``ratelimit:*`` and ``gcra:*`` cover every
   rate-limit counter, because both engines prepend a fixed prefix at the one
   dispatch seam.

2. Consequently the namespaces below are enumerated from the code that
   INCREMENTS a counter, not from the list of delete patterns. Deriving them
   from the patterns is how the original defect happened, and deriving them
   from a key *builder* is how it survived one round of review.
"""

from __future__ import annotations

import ast
import fnmatch
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import redis.asyncio as aioredis

from src.app.core.config import settings
from src.app.core.security.budgets import Identity, SecurityAction, check, probe
from src.app.core.security.rate_policy import build_rate_limit_key
from src.app.core.utils.gcra_rate_limit import GCRARateLimiter
from src.app.shared.exceptions import RateLimitExceededError
from src.benchmarks.security_state import SECURITY_KEY_PATTERNS, clear_security_state

pytestmark = pytest.mark.uses_real_redis

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "src" / "app"

# A documentation-reserved IP (RFC 5737 TEST-NET-3), so a leftover key from a
# failed run is recognisable and cannot collide with a real client.
PROBE_IP = "203.0.113.77"

#: The physical prefix each engine prepends, keyed by the value the operator
#: puts in ``RATE_LIMIT_ALGORITHM``. Derived from source in
#: :func:`_engine_prefixes` — this literal is the cross-check, not the source.
ENGINE_PREFIXES = {"gcra": "gcra:", "fixed_window": "ratelimit:"}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _engine_prefixes() -> dict[str, str]:
    """Every ``<prefix:>`` a rate-limit engine prepends, read from the source.

    Walks every ``check_rate_limit*`` coroutine in ``src/app`` and pulls the
    leading literal out of each f-string assigned to a Redis key name. This is
    the enumeration the bead asked for: it comes from the code that increments
    a counter, so a renamed prefix or a third engine shows up here instead of
    silently escaping the cleanup list.
    """
    found: dict[str, str] = {}
    for path in sorted(APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith("check_rate_limit"):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Assign) or not isinstance(inner.value, ast.JoinedStr):
                    continue
                target = inner.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                values = inner.value.values
                if not values or not isinstance(values[0], ast.Constant) or not isinstance(values[0].value, str):
                    continue
                found[f"{path.relative_to(REPO_ROOT).as_posix()}::{target.id}"] = values[0].value
    return found


def _declared_durable_namespaces() -> dict[str, str]:
    """``key_pattern=`` values from ``DurableLockout`` declarations.

    ``budgets.py`` declares each durable backstop with the pattern the owning
    code increments, and names the refusal it raises. A durable backstop that
    refuses with anything other than 429 is invisible to a rate-limit-shaped
    cleanup, which is why these are enumerated separately from the engines.
    """
    from src.app.core.security.budgets import _MFA_DURABLE_LOCKOUT

    return {_MFA_DURABLE_LOCKOUT.key_pattern: f"{_MFA_DURABLE_LOCKOUT.owner} (refusal: {_MFA_DURABLE_LOCKOUT.refusal})"}


def _covering_pattern(key: str) -> str | None:
    return next((p for p in SECURITY_KEY_PATTERNS if fnmatch.fnmatchcase(key, p)), None)


def _client() -> aioredis.Redis:
    # Instance access: REDIS_CACHE_URL is a @computed_field @property, so
    # reading it off the class yields the property object. The test session
    # replaces that property to redirect at an isolated database, and this is
    # the access that picks that up.
    client: aioredis.Redis = aioredis.from_url(settings.REDIS_CACHE_URL, decode_responses=True)
    return client


@pytest.fixture
async def engine(request):
    """Pin ``RATE_LIMIT_ALGORITHM`` for one test and restore the real setting.

    Both engines are tested even though the deployed default is ``gcra``,
    because the cleanup has to hold for whichever one an operator selects.
    """
    algorithm = request.param
    original = settings.rate_limit.RATE_LIMIT_ALGORITHM
    settings.rate_limit.RATE_LIMIT_ALGORITHM = algorithm
    client = _client()
    yield algorithm, client
    settings.rate_limit.RATE_LIMIT_ALGORITHM = original
    await client.aclose()


async def _auth_budget_calls(client, algorithm: str, attempts: int) -> int:
    """Drive ``attempts`` real auth-budget charges; return how many were allowed.

    Goes through ``check()`` — the production refusal site — so the counter is
    written by the same code an HTTP request would run.
    """
    import src.app.core.utils.gcra_rate_limit as gcra_mod

    gcra_mod.gcra_rate_limiter = GCRARateLimiter(client)
    allowed = 0
    for _ in range(attempts):
        try:
            await check(SecurityAction.AUTH, Identity(ip=PROBE_IP))
        except RateLimitExceededError:
            break
        allowed += 1
    return allowed


async def _auth_physical_keys(client: aioredis.Redis, algorithm: str) -> AsyncIterator[str]:
    """The physical keys the real path just wrote, found by scanning.

    Derived by observation rather than assembled from a string format, so the
    test cannot pass by agreeing with its own mistake about the key shape.
    """
    prefix = ENGINE_PREFIXES[algorithm]
    async for key in client.scan_iter(match=f"{prefix}*{PROBE_IP}*", count=1000):
        yield key if isinstance(key, str) else key.decode()


# --------------------------------------------------------------------------
# (b) the counter goes to ZERO, read back through the engine
# --------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINE_PREFIXES.keys(), indirect=True)
async def test_auth_budget_counter_is_read_back_as_zero_after_cleanup(engine):
    algorithm, client = engine
    limit, _period = settings.rate_limit.SECURITY_BUDGETS["auth"]

    try:
        # Exhaust the budget through the production path.
        allowed_before = await _auth_budget_calls(client, algorithm, limit + 1)
        assert allowed_before == limit, f"expected exactly {limit} allowed before the cap, got {allowed_before}"

        # The counter is now genuinely non-zero: the next charge is refused and
        # the engine reports no headroom.
        with pytest.raises(RateLimitExceededError):
            await check(SecurityAction.AUTH, Identity(ip=PROBE_IP))
        exhausted = await probe(SecurityAction.AUTH, Identity(ip=PROBE_IP))
        assert exhausted.is_limited is True
        assert exhausted.remaining == 0

        keys_before = [k async for k in _auth_physical_keys(client, algorithm)]
        assert keys_before, "the exhausted budget wrote no physical key; the probe above proves nothing"

        # ---- the cleanup under test ----
        deleted = await clear_security_state()

        # Read the counter BACK, twice, because either alone is insufficient:
        #   1. the physical key is absent
        keys_after = [k async for k in _auth_physical_keys(client, algorithm)]
        assert keys_after == [], f"cleanup left {keys_after}"
        #   2. the engine agrees it is at zero — a TTL-retaining or
        #      differently-prefixed counter survives (1) and fails (2).
        assert deleted >= len(keys_before), "cleanup reported fewer deletions than there were keys to remove"
        restored = await probe(SecurityAction.AUTH, Identity(ip=PROBE_IP))
        assert restored.is_limited is False
        assert restored.remaining is not None, "an un-limited verdict must report remaining headroom, not None"
        assert restored.remaining > 0

        # And the budget is FULL again, not merely open: the same number of
        # charges is allowed as before the cleanup.
        #
        # ``probe()`` is not a pure observation — it meters the action exactly
        # as ``check()`` does, so the read-back above spent one charge. Clear
        # again so the count below starts from a pristine zero rather than from
        # limit-1, which would otherwise make this assertion off by one and
        # hide a genuinely partial restore.
        await clear_security_state()
        allowed_after = await _auth_budget_calls(client, algorithm, limit + 1)
        assert allowed_after == limit, f"budget was not restored to {limit}, got {allowed_after}"
    finally:
        async for key in _auth_physical_keys(client, algorithm):
            await client.delete(key)
        await client.delete(f"mfa_attempts:{PROBE_IP}")


@pytest.mark.parametrize("engine", ENGINE_PREFIXES.keys(), indirect=True)
async def test_cleanup_reports_a_nonzero_deletion_count_while_counters_exist(engine):
    """``clear_security_state`` must not report success on an untouched Redis.

    Guards the return value, which is what a caller uses to decide whether a
    phase needed cleaning at all.
    """
    algorithm, client = engine
    logical = build_rate_limit_key("budget:auth", user_id=PROBE_IP)
    physical = f"{ENGINE_PREFIXES[algorithm]}{logical}"
    await client.set(physical, "1")
    try:
        deleted = await clear_security_state()
        assert deleted >= 1
        assert await client.exists(physical) == 0
    finally:
        await client.delete(physical)


# --------------------------------------------------------------------------
# (c) the namespace list is derived from incrementing code, and stays derived
# --------------------------------------------------------------------------


def test_engine_prefixes_are_read_from_the_incrementing_code():
    """Pin the enumeration itself, so a silent rename cannot slip through."""
    found = _engine_prefixes()
    assert found, "found no check_rate_limit* key assignment; the scanner is broken"
    assert set(found.values()) == set(ENGINE_PREFIXES.values()), (
        f"the engines prepend {sorted(set(found.values()))}, "
        f"but this test expects {sorted(set(ENGINE_PREFIXES.values()))}. "
        f"A new engine or a renamed prefix appeared: {found}"
    )


@pytest.mark.parametrize("prefix", sorted(set(ENGINE_PREFIXES.values())))
def test_every_engine_prefix_has_a_cleanup_pattern(prefix):
    """The required pattern is DERIVED from the source prefix, not hardcoded."""
    assert prefix in set(_engine_prefixes().values()), f"{prefix} is not produced by any engine any more"
    assert f"{prefix}*" in SECURITY_KEY_PATTERNS, (
        f"the engine writes {prefix}... and SECURITY_KEY_PATTERNS has no {prefix}* entry, "
        f"so every counter it meters survives a cleanup"
    )


def test_durable_backstop_namespaces_have_cleanup_patterns():
    """A backstop that refuses with 401 is invisible to a rate-limit-shaped list."""
    for pattern, owner in _declared_durable_namespaces().items():
        namespace = pattern.split("{", 1)[0]
        assert _covering_pattern(f"{namespace}anything") == f"{namespace}*", (
            f"{owner} increments {namespace}... but no pattern covers it"
        )


def test_logical_rate_limit_keys_are_not_mistaken_for_physical_ones():
    """``rate_limit:`` is the LOGICAL prefix; nothing physical starts with it.

    This is the exact false belief that let the original defect through: the
    logical key built by ``build_rate_limit_key`` looks like a namespace, and
    ``rate_limit:*`` reads like it covers the budget. It does not, under either
    engine.
    """
    logical = build_rate_limit_key("budget:auth", user_id=PROBE_IP)
    assert logical.startswith("rate_limit:")
    for algorithm, prefix in ENGINE_PREFIXES.items():
        physical = f"{prefix}{logical}"
        assert physical.startswith(prefix)
        assert _covering_pattern(physical) == f"{prefix}*", f"{algorithm} physical key is uncovered"
        assert _covering_pattern(logical) is None or logical != physical
    assert "rate_limit:*" not in SECURITY_KEY_PATTERNS, (
        "rate_limit:* matches no physical counter; it is the logical prefix and it "
        "reads as coverage while covering nothing"
    )


def test_no_pattern_is_dead():
    """Every pattern must match something the application can actually write."""
    concrete = [
        "security:ban:203.0.113.1",
        "security:ban_count:203.0.113.1",
        "security:violations:203.0.113.1",
        "gcra:rate_limit:user:203.0.113.1:budget:auth",
        "ratelimit:rate_limit:user:203.0.113.1:budget:auth:1791138000",
        "mfa_attempts:1",
    ]
    for pattern in SECURITY_KEY_PATTERNS:
        assert any(fnmatch.fnmatchcase(k, pattern) for k in concrete), f"{pattern} matches nothing the app writes"
