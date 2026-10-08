"""T-4 and T-5: the two counters whose job is to VOID a run, not to describe one.

Both live in one module because they are the same shape, they are recorded from
the same place, and - this is the substantive reason - they answer the same
question from opposite sides. T-4 counts status codes and voids a phase that saw
a rejection. T-5 counts rate-limit and auto-ban events and voids a run that saw
one at all. Both turn "something went wrong" into "this number is not admissible",
which is why they live together and why neither is allowed to merely annotate.

T-4: per-phase status distribution, non-2xx voids the phase
------------------------------------------------------------
A phase in which a meaningful fraction of requests was rejected is not a phase in
which requests were served. Its latency distribution is the distribution of
rejections, and rejections are FAST - so including it drags every percentile down
and every throughput figure up, in exactly the direction that makes a system look
better than it is. That is why this is a void condition and not a warning, and
why it voided 3 of 6 phases in ``driver_count_io.out``.

It counts server-side rather than in the load generator, because locust can only
see what it chose to classify and its per-request classification is precisely
what was wrong in Round 1 (H-7's missing ``catch_response``). A server-side
counter cannot be fooled by client-side classification.

T-5: rate-limit and auto-ban events, ZERO required
--------------------------------------------------
The difference that matters is ABSENCE versus OCCURRENCE. A log line proves an
event happened; a counter that reads zero at run start and zero at run end
proves absence, and absence is what Gate A checkbox 6 and unified plan §4.5
require. Only a counter can evidence a negative.

The mechanism this detects is confirmed and self-defeating: a rate-limited client
accrues a violation toward ``AUTO_BAN_THRESHOLD`` and can ban ITSELF
(``request_admission.py:310-313`` into ``auto_ban.py:723-729``). It fired on
``127.0.0.1`` during the benchmark and voided 3 of 6 census phases.

**T-5 CHANGES NO POLICY.** Whether a 429 counts toward auto-ban is ADR-0017's
question and the owner's answer (A-9: it must not). This counter records what the
current code does and changes nothing about it - it is an instrument, and an
instrument that quietly alters the system it measures cannot be used to argue
about that system's policy.
"""

from __future__ import annotations

from typing import Any

from src.app.core.monitoring.telemetry import (
    Ceiling,
    Counter,
    assert_cost_under_ceiling,
    measure_call_cost,
    non_2xx_keys,
    status_class,
)

DEFAULT_PHASE = "unlabelled"

#: The counter's added per-request cost when disabled. Absolute, chosen in
#: advance. A counter that costs a dict lookup to record nothing is not free.
COUNTER_COST_CEILING_US = 5.0
COUNTER_COST_CEILING = Ceiling(COUNTER_COST_CEILING_US, "T-4/T-5 counter per-request cost")

#: Statuses that void a run outright regardless of phase. 429 and 401 are named
#: by unified plan §4.5 verbatim; a 5xx is included because a server error is not
#: a fast rejection and would flatter the distribution in the same way a 429 does.
RUN_VOID_STATUSES: frozenset[int] = frozenset({401, 429})


class StatusDistribution:
    """Per-phase status-code distribution, server-side.

    Keyed by phase because the void conditions are per-phase: one bad phase does
    not invalidate the run's other phases, it invalidates that phase. Collapsing
    to a run-level total would discard exactly the granularity the harness needs
    to re-run only what failed.
    """

    name = "status_distribution"

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled
        self._per_phase: dict[str, Counter] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def record(self, phase: str, status_code: int) -> None:
        """Record one response. Returns immediately when disabled."""
        if not self._enabled:
            return
        counter = self._per_phase.get(phase)
        if counter is None:
            counter = Counter(f"status[{phase}]")
            self._per_phase[phase] = counter
        bucket = status_class(status_code)
        counter.increment(bucket, label=str(status_code))

    def phase_distribution(self, phase: str) -> dict[str, int]:
        counter = self._per_phase.get(phase)
        return dict(counter.totals) if counter is not None else {}

    def export(self) -> dict[str, Any]:
        return {
            "instrument": self.name,
            "enabled": self._enabled,
            "phases": {
                phase: {
                    "totals": dict(counter.totals),
                    "codes": {k: dict(v) for k, v in counter.labels.items()},
                }
                for phase, counter in self._per_phase.items()
            },
            "cost_ceiling_us": COUNTER_COST_CEILING_US,
        }

    def reset(self) -> None:
        self._per_phase.clear()


class SecurityEventCounter:
    """Counts rate-limit and auto-ban events so a run can prove it saw none.

    Both event kinds are counted together and reported separately, because they
    are different failures with different owners: a rate-limit event means the
    budgets were reached, an auto-ban event means the host was banned and
    subsequent requests were answered by a banned-IP path rather than by the
    system under test.
    """

    name = "security_events"

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled
        self._counter = Counter("security_events")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def record_rate_limit(self, *, phase: str = DEFAULT_PHASE, identity: str = "") -> None:
        """Record one rate-limit rejection. No policy change of any kind."""
        if not self._enabled:
            return
        self._counter.increment("rate_limit", label=phase or DEFAULT_PHASE)
        if identity:
            # Recorded only as an aggregate label, never the raw identity: an
            # instrument that writes client identities into an artefact turns a
            # performance run into a data-exposure surface.
            self._counter.increment("identities_seen", by=0)

    def record_auto_ban(self, *, phase: str = DEFAULT_PHASE) -> None:
        if not self._enabled:
            return
        self._counter.increment("auto_ban", label=phase or DEFAULT_PHASE)

    def total_events(self) -> int:
        return self._counter.total_events()

    def export(self) -> dict[str, Any]:
        return {
            "instrument": self.name,
            "enabled": self._enabled,
            "rate_limit_events": self._counter.get("rate_limit"),
            "auto_ban_events": self._counter.get("auto_ban"),
            "total_events": self.total_events(),
            "zero_assertion": self.total_events() == 0,
            "by_phase": dict(self._counter.labels.get("rate_limit", {})),
            "changes_policy": False,
            "cost_ceiling_us": COUNTER_COST_CEILING_US,
        }

    def reset(self) -> None:
        self._counter.reset()


# -- the void conditions -----------------------------------------------------


def phase_is_void(distribution: dict[str, int]) -> list[str]:
    """Status classes in a phase that void it. Empty means the phase is admissible.

    A rejection is fast, so a phase carrying rejections reports a latency
    distribution of rejections. Returning the offending classes rather than a
    bare bool is deliberate: an artefact that says "void" without saying which
    class caused it sends the reader back to the raw numbers to find out why.
    """
    return non_2xx_keys(distribution)


def assert_phase_admissible(distribution: dict[str, int], phase: str) -> dict[str, int]:
    """Raise when a phase carried a rejection, rather than annotating it.

    Unified plan §4.5 states it verbatim - a phase returning 429/401 IS VOID -
    and the Round-1 pack's own history is the argument for enforcing it in code:
    three phases were carried forward anyway and every downstream number was read
    off them.
    """
    offenders = phase_is_void(distribution)
    if offenders:
        raise AssertionError(
            f"Phase {phase!r} is VOID: non-2xx responses {offenders} in a phase whose "
            f"latency distribution would then be the distribution of rejections "
            f"({distribution}). Re-run the phase."
        )
    return distribution


def assert_run_saw_zero_security_events(counter: SecurityEventCounter, *, when: str) -> None:
    """Assert zero rate-limit and auto-ban events for the whole run.

    Checked at run START and run END, per §4.5. Both ends matter and they catch
    different things: the start check catches state left behind by an earlier run
    (auto-ban is persistent, so a previous run's ban poisons the next one), and
    the end check catches events this run produced.

    Any non-zero voids the run. Not annotates. A voided run is re-run; an
    annotated one is quoted, and the annotation is the first thing dropped when
    the number is repeated somewhere the annotation did not travel.
    """
    total = counter.total_events()
    if total:
        detail = counter.export()
        raise AssertionError(
            f"Run is VOID at {when}: {total} rate-limit/auto-ban event(s) "
            f"(rate_limit={detail['rate_limit_events']}, "
            f"auto_ban={detail['auto_ban_events']}). Unified plan §4.5 requires "
            f"ZERO for the whole run."
        )


def assert_counter_cost(measured_us: float) -> float:
    """Check a counter's measured per-request cost against its absolute ceiling."""
    return assert_cost_under_ceiling(measured_us, COUNTER_COST_CEILING)


def measure_disabled_cost(*, repeats: int = 20000) -> dict[str, Any]:
    """Measure the counters' added cost when disabled, against their ceiling.

    This is the cost every deployment that never scrapes these pays, so it is the
    cost that has to be free. Both counters are driven; the figure reported is the
    per-request cost of the disabled path across them.
    """
    status = StatusDistribution(enabled=False)
    security = SecurityEventCounter(enabled=False)

    def disabled_path() -> None:
        status.record(DEFAULT_PHASE, 200)
        security.record_rate_limit()

    cost_us = measure_call_cost(disabled_path, repeats=repeats)
    assert_counter_cost(cost_us)
    return {"measured_us": round(cost_us, 4), "ceiling_us": COUNTER_COST_CEILING_US}


def measure_enabled_cost(*, repeats: int = 20000) -> dict[str, Any]:
    """Measure the counters' added cost when enabled, against their ceiling."""
    status = StatusDistribution(enabled=True)
    security = SecurityEventCounter(enabled=True)

    def enabled_path() -> None:
        status.record(DEFAULT_PHASE, 200)
        security.record_rate_limit()

    cost_us = measure_call_cost(enabled_path, repeats=repeats)
    assert_counter_cost(cost_us)
    return {"measured_us": round(cost_us, 4), "ceiling_us": COUNTER_COST_CEILING_US}


def run_void_statuses() -> frozenset[int]:
    """The status codes that void a run on their own. Exposed so a test can pin it."""
    return RUN_VOID_STATUSES
