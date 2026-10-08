"""P-6: the harness-side half of the non-2xx void condition.

The production-side counter is T-4
(:mod:`src.app.core.monitoring.void_conditions`); this is the other half - the
decision, made by the harness, about what may be reported.

WHY THE DECISION CANNOT LIVE ONLY IN THE COUNTER
------------------------------------------------
A void condition discovered at the END of a run is a caveat; a void condition
discovered PER PHASE is a re-run. That difference is the whole reason this module
exists separately from T-4. Unified plan §4.5 requires 100% success status
distribution on every phase, with every non-2xx phase discarded and re-run - not
reported with a caveat attached.

The pack's own evidence for why: auto-ban fired on the benchmark host at
``127.0.0.1``, 3 of 6 phases were invalidated, and the remaining samples were
measuring REJECTION HANDLING rather than the endpoint. Those figures were carried
forward anyway, which is how a rate-limit artefact ends up quoted as a latency one.

WHAT A VOID PHASE DOES TO EVERY OTHER NUMBER
--------------------------------------------
Exclusion, and the direction of the bias matters enough to state. Rejections are
fast. A phase containing them has a latency distribution of rejections, so
including it drags every percentile DOWN and every throughput figure UP - in
precisely the direction that makes a system look better than it is. This is not a
conservatism; it is a signed error, and it favours the false conclusion.

So :func:`admit_phases` returns the phases that may be reported, and
:func:`percentiles_over_admitted` refuses to compute anything over a set that
includes a void phase rather than quietly skipping it. A number that cannot be
computed is better than a number computed from the wrong population.

THE TWO COUNTERS MUST AGREE
---------------------------
T-4 counts server-side; locust classifies client-side. When they disagree, one of
them is wrong and neither can be trusted, so :func:`assert_counters_agree` voids
the run. H-7's missing ``catch_response`` is the precedent: the client-side
classification in this project has been wrong before, and a void condition
validated against the same kind of data that produced the violation validates
nothing.

Gate A's 100% requirement
--------------------------
:func:`assert_gate_a_success` is the aggregate check: every phase admitted, no
void phase outstanding. It exists separately from the per-phase check because a
run can satisfy every per-phase rule and still be missing a phase entirely, and a
missing phase is not a passing phase.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Status classes that count as "served". 3xx is included because a redirect is
#: a served response and a client following it records a 2xx downstream; excluding
#: it would void a phase for behaviour that is not a rejection.
SERVED_CLASSES: frozenset[str] = frozenset({"2xx", "3xx"})

#: The two statuses unified plan §4.5 names verbatim. Recorded separately from the
#: general class check because they are the ones that void a run outright: a 429
#: means the budget was reached (so the rate limiter is being exercised and the
#: run is not measuring the steady state) and a 401 means the request was never
#: authenticated (so it measured the rejection path).
RUN_VOID_STATUSES: frozenset[str] = frozenset({"429", "401"})


class PhaseVoided(RuntimeError):
    """A phase carried a rejection, so its numbers may not be reported."""

    def __init__(self, phase: str, distribution: Mapping[str, int], reasons: list[str]) -> None:
        self.phase = phase
        self.distribution = dict(distribution)
        self.reasons = list(reasons)
        super().__init__(
            f"Phase {phase!r} is VOID: {', '.join(reasons)} in {dict(distribution)}. "
            f"A rejection is fast, so this phase's latency distribution is the "
            f"distribution of rejections - including it would drag every percentile "
            f"down and every throughput figure up. Re-run the phase."
        )


@dataclass(frozen=True, slots=True)
class PhaseVerdict:
    """The admission decision for one phase, with the reason it was reached."""

    phase: str
    admitted: bool
    distribution: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    #: Present only when admitted. A void phase carries no percentiles at all,
    #: rather than percentiles marked invalid, so nothing downstream can quote one.
    percentiles: dict[str, float] | None = None

    def export(self) -> dict[str, Any]:
        """Serialise for the artefact: counts, not a boolean.

        Acceptance criterion (a): the distribution is recorded with counts. A
        boolean cannot be examined after the fact - an artefact that says "void"
        without saying how many 429s and against what total sends the reader back
        to the raw CSVs to find out whether it was one rejection or a third of
        the run.
        """
        return {
            "phase": self.phase,
            "admitted": self.admitted,
            "distribution": dict(self.distribution),
            "reasons": list(self.reasons),
            "percentiles": self.percentiles,
        }


def classify_status(status_code: int) -> str:
    """Bucket a status code into the class the void conditions are stated in."""
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    if status_code >= 500:
        return "5xx"
    return "1xx"


def void_reasons(
    distribution: Mapping[str, int],
    codes: Mapping[str, int] | None = None,
) -> list[str]:
    """Why a phase is void, or an empty list.

    Every offending class is named, not just the first. A run that voided on a 429
    and also carried 503s has two different problems, and reporting only the first
    means the second is discovered on the re-run.

    ``codes`` is the per-status breakdown within the offending classes. It is what
    separates "12 x 4xx" into "12 x 429 (named by plan 4.5)" versus "12 x 401
    (named by plan 4.5)" - unified plan §4.5 names those two codes specifically, and
    a reader told only "4xx" cannot tell whether the phase failed because it was
    throttled (so it was never measuring the steady state) or because requests
    were unauthenticated (so it measured the rejection path). Those are different
    diagnoses and the same class.
    """
    reasons: list[str] = []
    plan_named: dict[str, list[tuple[str, int]]] = {}
    for code, count in (codes or {}).items():
        if not count:
            continue
        klass = classify_status(int(code))
        if klass in SERVED_CLASSES:
            continue
        plan_named.setdefault(klass, []).append((code, count))

    for klass in sorted(distribution):
        count = distribution[klass]
        if not count or klass in SERVED_CLASSES:
            continue
        breakdown = plan_named.get(klass)
        if breakdown:
            detail = ", ".join(f"{n} x {code}" for code, n in sorted(breakdown))
            reasons.append(f"{count} x {klass}: {detail} (named by plan 4.5)")
        else:
            marker = " (named by plan 4.5)" if klass in RUN_VOID_STATUSES else ""
            reasons.append(f"{count} x {klass}{marker}")
    return reasons


def admit_phase(
    phase: str,
    distribution: Mapping[str, int],
    *,
    percentiles: Mapping[str, float] | None = None,
    codes: Mapping[str, int] | None = None,
) -> PhaseVerdict:
    """Decide one phase, and raise :class:`PhaseVoided` if it may not be reported.

    Raising rather than returning a negative verdict is deliberate for the default
    path: a harness that receives a verdict it can forget to check produces the
    same uninterpretable artefact as one that never had the rule. Callers that
    want to aggregate verdicts use :func:`admit_phases`, which collects instead of
    raising, and that function returns the admitted set explicitly.
    """
    reasons = void_reasons(distribution, codes)
    if reasons:
        raise PhaseVoided(phase, distribution, reasons)
    return PhaseVerdict(
        phase=phase,
        admitted=True,
        distribution=dict(distribution),
        reasons=[],
        percentiles=dict(percentiles) if percentiles is not None else None,
    )


def admit_phases(
    distributions: Mapping[str, Mapping[str, int]],
    *,
    percentiles: Mapping[str, Mapping[str, float]] | None = None,
    codes: Mapping[str, Mapping[str, int]] | None = None,
) -> list[PhaseVerdict]:
    """Verdict for every phase. Void phases carry NO percentiles.

    Collected rather than raised, because the artefact needs to show all of them:
    a run that voided two phases and admitted one is a different situation from
    one that voided the first phase it looked at, and reporting only the first
    makes them identical.
    """
    verdicts: list[PhaseVerdict] = []
    for phase in sorted(distributions):
        distribution = distributions[phase]
        reasons = void_reasons(distribution, (codes or {}).get(phase))
        verdicts.append(
            PhaseVerdict(
                phase=phase,
                admitted=not reasons,
                distribution=dict(distribution),
                reasons=reasons,
                percentiles=None if reasons else dict((percentiles or {}).get(phase, {})),
            )
        )
    return verdicts


def admitted_phases(verdicts: Sequence[PhaseVerdict]) -> list[str]:
    return [v.phase for v in verdicts if v.admitted]


def void_phases(verdicts: Sequence[PhaseVerdict]) -> list[str]:
    return [v.phase for v in verdicts if not v.admitted]


def percentiles_over_admitted(verdicts: Sequence[PhaseVerdict]) -> dict[str, float]:
    """Aggregate percentiles across the ADMITTED phases only.

    Refuses rather than skipping a void phase. A caller that wanted the admitted
    set already has it from :func:`admitted_phases`, so passing a verdict list
    containing a void phase is a mistake rather than a request - and answering it
    with a quietly-filtered number would turn that mistake into a plausible
    figure.
    """
    void = void_phases(verdicts)
    if void:
        raise PhaseVoided(
            ",".join(void),
            {},
            [f"cannot aggregate over void phase(s) {void}; re-run them first"],
        )
    merged: dict[str, list[float]] = {}
    for verdict in verdicts:
        for key, value in (verdict.percentiles or {}).items():
            merged.setdefault(key, []).append(value)
    return {key: sum(values) / len(values) for key, values in merged.items()}


def assert_gate_a_success(verdicts: Sequence[PhaseVerdict], *, expected_phases: Sequence[str]) -> None:
    """Gate A requires 100% success across ALL phases - including that none is missing.

    Checked as three separate failures rather than one "not all admitted", because
    a missing phase and a void phase are different defects: the first is a run
    that did not measure what it claimed, the second is a run that measured it and
    the measurement is inadmissible. Reporting both as one boolean loses the
    distinction, and the distinction is what tells an operator whether to re-run
    the phase or the whole run.
    """
    void = void_phases(verdicts)
    if void:
        reasons = [reason for v in verdicts if not v.admitted for reason in v.reasons]
        raise PhaseVoided(
            ",".join(void), {}, [f"phase(s) {void} voided: {reasons}"]
        )
    seen = {v.phase for v in verdicts}
    missing = [phase for phase in expected_phases if phase not in seen]
    if missing:
        raise PhaseVoided(
            ",".join(missing),
            {},
            [f"phase(s) {missing} are absent from the artefact; a phase that was "
             f"not measured is not a phase that passed"],
        )


def assert_counters_agree(
    server: Mapping[str, int],
    client: Mapping[str, int],
    *,
    tolerance: float = 0.02,
) -> dict[str, Any]:
    """T-4's server-side count and locust's client-side count must agree.

    They are independent observers, and that is what makes the comparison worth
    making: agreement means neither is blind, disagreement means one of them is and
    every per-phase figure derived from them is void until it is found. The
    tolerance is a RELATIVE one because the two count over different populations -
    the server sees every request including ones the client retried or failed to
    send - so an absolute allowance would have to be either zero (never satisfied)
    or large enough to hide the miss this exists to catch.

    THE COMPARISON IS PER CLASS, NOT PER TOTAL, and an earlier version compared
    only the totals and was WRONG. The verification bead caught it: an observer
    that classified all 100 rejections as successes and one that counted them
    correctly produce the same total (200) and disagree completely (100 x 4xx
    versus none). Totals alone therefore report agreement for precisely the
    failure this exists to catch - H-7's missing ``catch_response``, where the
    client-side classification is what went wrong. Each class is compared
    separately, so a rejection counted by one observer and missed by the other
    shows up as a disagreement in the 4xx bucket rather than cancelling out in the
    total.
    """
    server_total = sum(server.values())
    client_total = sum(client.values())
    if server_total == 0 and client_total == 0:
        return {"agree": True, "server_total": 0, "client_total": 0, "relative_delta": 0.0}

    classes = sorted(set(server) | set(client))
    larger = max(server_total, client_total) or 1
    disagreements: list[str] = []
    for klass in classes:
        server_count = int(server.get(klass, 0))
        client_count = int(client.get(klass, 0))
        bucket_delta = abs(server_count - client_count)
        if bucket_delta / larger > tolerance:
            disagreements.append(
                f"{klass}: server {server_count} vs client {client_count}"
            )

    total_delta = abs(server_total - client_total) / larger
    if disagreements or total_delta > tolerance:
        raise PhaseVoided(
            "all",
            {},
            [
                f"the server-side counter and the client-side classification "
                f"disagree ({'; '.join(disagreements) or 'total mismatch'}; total "
                f"delta {total_delta:.1%}). One of them is blind - H-7's missing "
                f"catch_response is the precedent, and it is exactly a per-class "
                f"disagreement that a totals-only comparison cancels out - and every "
                f"per-phase figure derived from this run is void until it is "
                f"identified."
            ],
        )
    return {
        "agree": True,
        "server_total": server_total,
        "client_total": client_total,
        "relative_delta": round(total_delta, 6),
        "compared_classes": classes,
    }
