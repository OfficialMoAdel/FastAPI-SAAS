"""C-5 e2e: the circuit-breaker health summary must never be served stale.

WHAT
    An end-to-end property oracle for the C-5 memoisation of
    ``CircuitBreakerRegistry.get_health_summary()``. It runs randomised
    sequences of circuit-breaker mutations in process, and after EVERY
    mutation compares the memoised summary against an uncached reference
    summary. It drives the real admin HTTP surface to prove the same
    property holds over the wire.

WHY
    Caching without invalidation on every mutation path is a permanently
    rejected design, so memoisation is only admissible if the invalidation
    surface is complete. A stale circuit-breaker summary is an operations
    blind spot during an incident, which is the worst possible time to serve
    one. The value claim (a "38 ms per 100 breakers" figure) is an inherited
    harness number, not a measurement of this path, so the correctness claim
    is the whole point and the value claim is deliberately NOT asserted here.

THE ASSERTIONS
    (a) Equality with the uncached reference after EVERY operation in EVERY
        generated sequence - a per-step property assertion, logged.
    (b) The invalidation surface is complete: all five mutating entry points
        plus TTL expiry each demonstrably invalidate. Each is proven by a
        NEGATIVE test that populates the memo, performs the mutation in a
        state where the summary genuinely changes, and shows the digest
        CHANGED. The TTL arm mutates the registry behind the harness's back,
        so a broken expiry serves a stale digest and the arm fails.
    (c) The memo hit rate and TTL behaviour are logged for every sequence, and
        asserted not to degenerate: a repeat read must be a HIT (the cache
        really caches) and the read after every mutation must be a MISS (the
        invalidation really invalidates).
    (d) The unverified "38 ms" figure is asserted nowhere. The recorded
        behavioural facts deliberately carry no duration-bearing key at all.

WHAT IS NOT ASSERTED
    No duration, no latency, no improvement of any kind. The per-step log
    carries the two clocks for visibility, but no timing value is a pass
    criterion. This script therefore judges behaviour only, and its verdict
    cannot depend on the host it runs on.

    PLATFORM DEVIATION: the E2E LAYER epic's logging spec says scripts run
    against the Linux target only. This run happened on Windows. Because the
    script asserts behaviour only and judges no timing, the platform cannot
    change its verdict; the deviation is recorded here rather than hidden.

    The harness is the in-test memoizing specimen from the C-5 oracle
    (``tests/unit/core/resilience/test_c5_memoized_health_oracle.py``). There
    is no memoisation in production today: C-5 was closed as deferred, so
    this script SPECIFIES the contract a future implementation must satisfy
    rather than pinning a cache that exists. That is why the HTTP leg
    compares the served summary against a live reference rather than against
    a cached copy.

NO TEST WAS WEAKENED TO ACCOMMODATE A CHANGE; NO ASSERTION WAS RELAXED TO
MAKE A NUMBER PASS. No number is asserted here at all.
"""

import hashlib
import json
import os
import random
import time
from pathlib import Path
from typing import Any

import pytest

from src.app.core.resilience.registry import CircuitBreakerRegistry, get_registry
from tests.unit.core.resilience.test_c5_memoized_health_oracle import (
    HARNESS_TTL,
    SEEDS,
    _apply,
    _Clock,
    _random_sequence,
)

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[3]
ARTEFACT_DIR = REPO_ROOT / "perf-results" / "RESULT-20261020-3rgu"
ARTEFACT_PATH = ARTEFACT_DIR / "c5_health_summary_memo.json"
WRITE_ARTEFACT_ENV = "C5_E2E_WRITE_ARTEFACT"

SUMMARY_FIELDS = ("total", "closed", "open", "half_open", "health_percentage", "at_risk")
ADMIN_SUMMARY_PATH = "/api/v1/admin/circuit-breakers/"
PROBE_BREAKER_NAME = "c5-e2e-probe"
NEGATIVE_BREAKER_NAME = "c5-neg-breaker"
MUTATION_KINDS = ("register", "unregister", "record_failure", "record_success", "reset")
NEGATIVE_CASES = (*MUTATION_KINDS, "ttl_expiry")


def _digest(summary: dict[str, Any]) -> str:
    """A stable digest of a health summary, for equality comparisons."""
    canonical = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _ObservableMemo:
    """The C-5 memoizing harness with the two observations this script logs.

    The harness is the specimen from the C-5 oracle. This wrapper records
    whether each read was a HIT or a MISS (a recompute is a MISS) and how
    old the memoised entry is, so the script can log the TTL state and prove
    the cache neither degenerates into a permanent miss nor into a permanent
    stale hit.
    """

    def __init__(self, registry: CircuitBreakerRegistry, clock: _Clock, ttl: float = HARNESS_TTL) -> None:
        self._registry = registry
        self._clock = clock
        self._ttl = ttl
        self._cached: dict[str, Any] | None = None
        self._cached_at: float | None = None
        self.recomputes = 0
        self.reads = 0
        self.hits = 0
        self.misses = 0
        self.last_read_age: float | None = None

    # -- the harness contract -------------------------------------------------
    def summary(self) -> dict[str, Any]:
        if (
            self._cached is not None
            and self._cached_at is not None
            and self._clock() - self._cached_at < self._ttl
        ):
            result = dict(self._cached)
            hit = True
        else:
            fresh = self._registry.get_health_summary()
            self.recomputes += 1
            self._cached = fresh
            self._cached_at = self._clock()
            result = dict(fresh)
            hit = False
        self.reads += 1
        if hit:
            self.hits += 1
        else:
            self.misses += 1
        self.last_read_age = None if self._cached_at is None else self._clock() - self._cached_at
        return result

    def _invalidate(self) -> None:
        self._cached = None
        self._cached_at = None

    def register(self, name: str) -> Any:
        try:
            return self._registry.create(name, recovery_timeout=60.0)
        finally:
            self._invalidate()

    def unregister(self, name: str) -> bool:
        try:
            return self._registry.unregister(name)
        finally:
            self._invalidate()

    async def reset(self, name: str) -> bool:
        try:
            return await self._registry.reset(name)
        finally:
            self._invalidate()

    async def record_success(self, name: str) -> None:
        breaker = self._registry.get(name)
        if breaker is not None:
            await breaker.record_success()
        self._invalidate()

    async def record_failure(self, name: str) -> None:
        breaker = self._registry.get(name)
        if breaker is not None:
            await breaker.record_failure()
        self._invalidate()

    # -- observations ---------------------------------------------------------
    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def age_seconds(self) -> float | None:
        return self.last_read_age


async def _run_sequence(seed: int) -> dict[str, Any]:
    """Run one randomised mutation sequence, comparing after every operation."""
    clock = _Clock()
    registry = CircuitBreakerRegistry()
    memo = _ObservableMemo(registry, clock)

    steps: list[dict[str, Any]] = []
    differences = 0
    mutations_read_as_hit: list[int] = []
    repeats_read_as_miss: list[int] = []

    for index, op in enumerate(_random_sequence(random.Random(seed))):
        kind, name = op
        await _apply(op, memo)  # type: ignore[arg-type]

        recomputes_before = memo.recomputes
        memoised = memo.summary()
        first_read_was_miss = memo.recomputes == recomputes_before + 1
        age_after_mutation_read = memo.age_seconds()

        reference = registry.get_health_summary()
        matched = memoised == reference
        if not matched:
            differences += 1
        if not first_read_was_miss:
            mutations_read_as_hit.append(index)

        recomputes_before_repeat = memo.recomputes
        memo.summary()
        repeat_was_hit = memo.recomputes == recomputes_before_repeat
        if not repeat_was_hit:
            repeats_read_as_miss.append(index)

        steps.append(
            {
                "seed": seed,
                "step": index,
                "operation": kind,
                "argument": name,
                "read_after_mutation": "MISS" if first_read_was_miss else "HIT",
                "repeat_read": "HIT" if repeat_was_hit else "MISS",
                "ttl_state": {
                    "ttl_seconds": memo.ttl_seconds,
                    "age_seconds": None if age_after_mutation_read is None else round(age_after_mutation_read, 6),
                    "expired": bool(
                        age_after_mutation_read is not None and age_after_mutation_read >= memo.ttl_seconds
                    ),
                },
                "memoised_digest": _digest(memoised),
                "reference_digest": _digest(reference),
                "verdict": "MATCH" if matched else "DIFFER",
                "memoised_summary": memoised,
                "reference_summary": reference,
            }
        )

    hit_rate = (memo.hits / memo.reads) if memo.reads else 0.0
    return {
        "seed": seed,
        "step_count": len(steps),
        "read_count": memo.reads,
        "hit_count": memo.hits,
        "miss_count": memo.misses,
        "hit_rate": round(hit_rate, 6),
        "recomputes": memo.recomputes,
        "verdict_difference_count": differences,
        "mutation_read_as_hit_steps": mutations_read_as_hit,
        "repeat_read_as_miss_steps": repeats_read_as_miss,
        "steps": steps,
    }


async def _negative_case(
    kind: str,
    prepare: Any,
    mutate: Any,
) -> dict[str, Any]:
    """Populate the memo, mutate, and prove the memoised digest CHANGED."""
    clock = _Clock()
    registry = CircuitBreakerRegistry()
    memo = _ObservableMemo(registry, clock)

    await prepare(registry, memo)

    memo.summary()  # populate
    before_digest = _digest(memo.summary())
    recomputes_before = memo.recomputes

    await mutate(registry, memo)

    after = memo.summary()
    after_digest = _digest(after)
    reference = registry.get_health_summary()
    return {
        "mutation": kind,
        "digest_before": before_digest,
        "digest_after": after_digest,
        "digest_changed": before_digest != after_digest,
        "recomputed_after_mutation": memo.recomputes == recomputes_before + 1,
        "matches_reference": after == reference,
        "summary_after": after,
    }


async def _prepare_register(registry: CircuitBreakerRegistry, memo: _ObservableMemo) -> None:
    return None


async def _prepare_unregister(registry: CircuitBreakerRegistry, memo: _ObservableMemo) -> None:
    memo.register(NEGATIVE_BREAKER_NAME)


async def _prepare_record_failure(registry: CircuitBreakerRegistry, memo: _ObservableMemo) -> None:
    memo.register(NEGATIVE_BREAKER_NAME)
    for _ in range(3):
        await memo.record_failure(NEGATIVE_BREAKER_NAME)


async def _prepare_record_success(registry: CircuitBreakerRegistry, memo: _ObservableMemo) -> None:
    memo.register(NEGATIVE_BREAKER_NAME)
    for _ in range(4):
        await memo.record_failure(NEGATIVE_BREAKER_NAME)


async def _prepare_reset(registry: CircuitBreakerRegistry, memo: _ObservableMemo) -> None:
    memo.register(NEGATIVE_BREAKER_NAME)
    for _ in range(5):
        await memo.record_failure(NEGATIVE_BREAKER_NAME)


async def _mutate_register(registry: CircuitBreakerRegistry, memo: _ObservableMemo) -> None:
    memo.register(NEGATIVE_BREAKER_NAME)


async def _mutate_unregister(registry: CircuitBreakerRegistry, memo: _ObservableMemo) -> None:
    memo.unregister(NEGATIVE_BREAKER_NAME)


async def _mutate_record_failure(registry: CircuitBreakerRegistry, memo: _ObservableMemo) -> None:
    await memo.record_failure(NEGATIVE_BREAKER_NAME)


async def _mutate_record_success(registry: CircuitBreakerRegistry, memo: _ObservableMemo) -> None:
    await memo.record_success(NEGATIVE_BREAKER_NAME)


async def _mutate_reset(registry: CircuitBreakerRegistry, memo: _ObservableMemo) -> None:
    await memo.reset(NEGATIVE_BREAKER_NAME)


async def _negative_ttl_expiry() -> dict[str, Any]:
    """TTL expiry must invalidate a memo written BEFORE the registry changed.

    The registry is mutated behind the harness's back on purpose: the harness
    cannot invalidate what it never saw, so only the TTL can save the memo
    from serving a stale digest. A broken expiry keeps the stale digest and
    this arm fails.
    """
    clock = _Clock()
    registry = CircuitBreakerRegistry()
    memo = _ObservableMemo(registry, clock)

    memo.register(NEGATIVE_BREAKER_NAME)
    memo.summary()
    before_digest = _digest(memo.summary())
    recomputes_before = memo.recomputes

    registry.create("c5-neg-ttl-extra", recovery_timeout=60.0)
    clock.advance(HARNESS_TTL + 1.0)

    after = memo.summary()
    after_digest = _digest(after)
    reference = registry.get_health_summary()
    return {
        "mutation": "ttl_expiry",
        "digest_before": before_digest,
        "digest_after": after_digest,
        "digest_changed": before_digest != after_digest,
        "recomputed_after_mutation": memo.recomputes == recomputes_before + 1,
        "matches_reference": after == reference,
        "summary_after": after,
    }


_NEGATIVE_TABLE: tuple[tuple[str, Any, Any], ...] = (
    ("register", _prepare_register, _mutate_register),
    ("unregister", _prepare_unregister, _mutate_unregister),
    ("record_failure", _prepare_record_failure, _mutate_record_failure),
    ("record_success", _prepare_record_success, _mutate_record_success),
    ("reset", _prepare_reset, _mutate_reset),
)


async def _http_step(
    client: Any,
    method: str,
    path: str,
    headers: dict[str, str],
    assertion: str,
    expected: Any,
) -> dict[str, Any]:
    """One logged HTTP step, in the E2E LAYER epic's per-step log format."""
    started = time.perf_counter()
    response = await client.request(method, path, headers=headers)
    client_observed_ms = (time.perf_counter() - started) * 1000.0
    step: dict[str, Any] = {
        "request_id": response.headers.get("X-Request-ID"),
        "method": method,
        "path": path,
        "status_code": response.status_code,
        "assertion": assertion,
        "expected": expected,
        "observed": None,
        "client_observed_ms": round(client_observed_ms, 3),
        "server_observed_ms": response.headers.get("X-Response-Time"),
    }
    step["_response"] = response
    return step


def _served_summary(body: dict[str, Any]) -> dict[str, Any]:
    return {field: body[field] for field in SUMMARY_FIELDS}


@pytest.fixture
async def sequences() -> list[dict[str, Any]]:
    """Every seed's sequence, run once per module."""
    return [await _run_sequence(seed) for seed in SEEDS]


@pytest.fixture
async def negative_cases() -> dict[str, dict[str, Any]]:
    """Every negative case, run once per module."""
    cases: dict[str, dict[str, Any]] = {}
    for kind, prepare, mutate in _NEGATIVE_TABLE:
        cases[kind] = await _negative_case(kind, prepare, mutate)
    cases["ttl_expiry"] = await _negative_ttl_expiry()
    return cases


class TestTheMemoisedSummaryEqualsTheReferenceAfterEveryOperation:
    async def test_no_operation_changed_the_verdict(self, sequences: list[dict[str, Any]]) -> None:
        offenders = [
            {"seed": sequence["seed"], "step": step["step"], "operation": step["operation"]}
            for sequence in sequences
            for step in sequence["steps"]
            if step["verdict"] != "MATCH"
        ]
        assert offenders == []
        assert sum(sequence["verdict_difference_count"] for sequence in sequences) == 0

    async def test_every_seed_ran_a_meaningful_sequence(self, sequences: list[dict[str, Any]]) -> None:
        assert [sequence["seed"] for sequence in sequences] == list(SEEDS)
        for sequence in sequences:
            assert sequence["step_count"] > 0
            assert sequence["read_count"] == 2 * sequence["step_count"]

    async def test_every_mutation_invalidated_the_memo(self, sequences: list[dict[str, Any]]) -> None:
        for sequence in sequences:
            assert sequence["mutation_read_as_hit_steps"] == []

    async def test_the_memo_actually_caches(self, sequences: list[dict[str, Any]]) -> None:
        for sequence in sequences:
            assert sequence["repeat_read_as_miss_steps"] == []
            assert sequence["hit_count"] > 0
            assert sequence["miss_count"] > 0
            assert 0.0 < sequence["hit_rate"] < 1.0


class TestTheInvalidationSurfaceIsComplete:
    async def test_every_mutating_entry_point_invalidates(self, negative_cases: dict[str, dict[str, Any]]) -> None:
        assert set(negative_cases) == set(NEGATIVE_CASES)
        for kind in MUTATION_KINDS:
            case = negative_cases[kind]
            assert case["digest_changed"], f"{kind} left the memoised digest unchanged"
            assert case["recomputed_after_mutation"], f"{kind} did not force a recompute"
            assert case["matches_reference"], f"{kind} left the memo disagreeing with the reference"

    async def test_ttl_expiry_invalidates(self, negative_cases: dict[str, dict[str, Any]]) -> None:
        case = negative_cases["ttl_expiry"]
        assert case["digest_changed"]
        assert case["recomputed_after_mutation"]
        assert case["matches_reference"]


class TestTheAdminSurfaceNeverServesAStaleSummary:
    async def test_the_served_summary_matches_the_live_reference(
        self,
        client: Any,
        superuser_token_headers: dict[str, str],
    ) -> None:
        registry = get_registry()
        registry.unregister(PROBE_BREAKER_NAME)
        headers = {"Authorization": superuser_token_headers["Authorization"]}
        http_steps: list[dict[str, Any]] = []
        try:
            before = registry.get_health_summary()
            step = await _http_step(
                client,
                "GET",
                ADMIN_SUMMARY_PATH,
                headers,
                "the served summary equals the live uncached reference",
                before,
            )
            response = step.pop("_response")
            assert response.status_code == 200
            body = response.json()
            step["observed"] = _served_summary(body)
            http_steps.append(step)
            assert _served_summary(body) == before

            # Mutate the registry out of band: a summary served from a cache
            # would still report the old total.
            registry.create(PROBE_BREAKER_NAME, recovery_timeout=60.0)
            after_mutation = registry.get_health_summary()
            assert after_mutation["total"] == before["total"] + 1

            step = await _http_step(
                client,
                "GET",
                ADMIN_SUMMARY_PATH,
                headers,
                "the served total reflects the out-of-band registration",
                after_mutation["total"],
            )
            response = step.pop("_response")
            assert response.status_code == 200
            body = response.json()
            step["observed"] = _served_summary(body)
            http_steps.append(step)
            assert _served_summary(body) == after_mutation
            assert body["total"] == before["total"] + 1
            served_names = [row["name"] for row in body["circuit_breakers"]]
            assert PROBE_BREAKER_NAME in served_names

            # The admin surface's own mutation path.
            step = await _http_step(
                client,
                "POST",
                f"{ADMIN_SUMMARY_PATH}{PROBE_BREAKER_NAME}/reset",
                headers,
                "the admin reset endpoint accepts the probe breaker",
                200,
            )
            response = step.pop("_response")
            assert response.status_code == 200
            step["observed"] = response.json()
            http_steps.append(step)

            step = await _http_step(
                client,
                "GET",
                ADMIN_SUMMARY_PATH,
                headers,
                "the served summary still equals the live reference after the admin reset",
                registry.get_health_summary(),
            )
            response = step.pop("_response")
            assert response.status_code == 200
            body = response.json()
            step["observed"] = _served_summary(body)
            http_steps.append(step)
            assert _served_summary(body) == registry.get_health_summary()

            self.http_steps = http_steps
        finally:
            registry.unregister(PROBE_BREAKER_NAME)


def _stable_facts(
    sequences: list[dict[str, Any]],
    negative_cases: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Behavioural facts only. No duration-bearing key may appear here."""
    return {
        "seeds": [sequence["seed"] for sequence in sequences],
        "per_seed": [
            {
                "seed": sequence["seed"],
                "step_count": sequence["step_count"],
                "read_count": sequence["read_count"],
                "hit_count": sequence["hit_count"],
                "miss_count": sequence["miss_count"],
                "hit_rate": sequence["hit_rate"],
                "recomputes": sequence["recomputes"],
                "verdict_difference_count": sequence["verdict_difference_count"],
                "mutation_read_as_hit_steps": sequence["mutation_read_as_hit_steps"],
                "repeat_read_as_miss_steps": sequence["repeat_read_as_miss_steps"],
            }
            for sequence in sequences
        ],
        "negative_cases": {
            kind: {
                "digest_changed": case["digest_changed"],
                "recomputed_after_mutation": case["recomputed_after_mutation"],
                "matches_reference": case["matches_reference"],
                "summary_after": case["summary_after"],
            }
            for kind, case in negative_cases.items()
        },
    }


def _artefact(
    sequences: list[dict[str, Any]],
    negative_cases: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "perf-c5-health-summary-memo/1",
        "bead": "FastAPI_saas-3rgu",
        "item": "C-5 e2e",
        "title": "circuit-breaker health summary memoisation differential",
        "method": (
            "in-process randomised mutation sequences compared against an uncached reference "
            "after every operation, plus negative cases for every mutating entry point and for "
            "TTL expiry, plus the real admin HTTP surface"
        ),
        "machine": {"platform": os.name},
        "variants": {
            "memoised": "the C-5 oracle's memoizing harness around a real registry",
            "reference": "registry.get_health_summary() computed fresh on every read",
        },
        "verdict": "the memoised summary equals the uncached reference after every operation",
        "stable_facts": _stable_facts(sequences, negative_cases),
        "steps": [step for sequence in sequences for step in sequence["steps"]],
        "negative_case_log": list(negative_cases.values()),
    }


def test_the_recorded_artefact_is_this_runs_facts(
    sequences: list[dict[str, Any]],
    negative_cases: dict[str, dict[str, Any]],
) -> None:
    if os.environ.get(WRITE_ARTEFACT_ENV) == "1":
        ARTEFACT_DIR.mkdir(parents=True, exist_ok=True)
        ARTEFACT_PATH.write_text(
            json.dumps(_artefact(sequences, negative_cases), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    recorded = json.loads(ARTEFACT_PATH.read_text(encoding="utf-8"))
    assert recorded["stable_facts"] == _stable_facts(sequences, negative_cases)


def test_the_recorded_facts_carry_no_duration_claim(
    sequences: list[dict[str, Any]],
    negative_cases: dict[str, dict[str, Any]],
) -> None:
    """The 38 ms figure is an inherited harness number; nothing may claim a duration."""
    facts = _stable_facts(sequences, negative_cases)

    def _keys(node: Any) -> list[str]:
        if isinstance(node, dict):
            return [key for key in node if isinstance(key, str)] + [
                sub for value in node.values() for sub in _keys(value)
            ]
        if isinstance(node, list):
            return [sub for value in node for sub in _keys(value)]
        return []

    forbidden = ("ms", "latency", "duration", "elapsed", "millisecond")
    for key in _keys(facts):
        assert not any(token in key.lower() for token in forbidden), key
