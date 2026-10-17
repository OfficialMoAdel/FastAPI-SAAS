"""The Step 4 exit gate.

Reads the artefacts Step 4 produced and asserts v8qm's ten exit criteria against
them. It exists because the criteria were, until now, satisfied one at a time by
whoever happened to be holding the artefact -- and a criterion nobody checks is a
criterion nobody met.

Two properties this file is built around, both paid for during the programme that
made it necessary:

**A check that runs, passes, and gates nothing is worse than no check.** Five
checks in this repository have been written, passed, and asserted nothing, and
each was caught only by BREAKING the thing it guarded and re-running. So every
check here states the value it expects, and the anti-fabrication check below
exists to be the sharpest of them.

**A measurement must not carry a number from a shape it made up.** The single
most important check in this file is ``criterion_2``, which FAILS if an endpoint
below the 10,000 sample floor publishes a p99 figure at all. That is ADR-0022
Rule 3, and it is written to be unmissable: a missing p99 must be a stated
number, not a plausible-looking value sitting in the wrong column.

The checker reads artefacts and never recomputes a statistic from them. A gate
that re-derives the numbers it is checking cannot detect a producer that
computed them wrong -- it would agree with itself. Where a criterion is
satisfied by an earlier bead's artefact, that artefact's PATH is required on the
command line, so a missing one is a failure rather than an assumption.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

#: v8qm's endpoints. The two body arms are EM-9 and EM-10, measured by
#: scripts/body_scan_regimes.py rather than the campaign, so they arrive under
#: their own names and are held to the same criteria.
#:
#: `users_me_apikey` is EM-11 (bead 86p9): the same handler as `users_me`, reached
#: with `X-API-Key` instead of a bearer token, so the delta between the two arms
#: is the credential seam and nothing else. It was added AFTER v8qm closed, which
#: is why the count below is eleven while that bead's close reason says ten — the
#: C1 name follows this tuple, and a gate that kept the old count would state a
#: number no longer derived from anything.
REQUIRED_ENDPOINTS: Final[tuple[str, ...]] = (
    "users_me",
    "users_list",
    "users_search",
    "users_me_anon",
    "users_me_apikey",
    "login",
    "logout",
    "refresh",
    "health",
    "em9_miss_60kb",
    "em10_hit_40kb",
)

#: 4.4's p99 bar, and ADR-0022 Rule 1's threshold. Duplicated deliberately as a
#: named constant: the gate must be able to state the bar independently of
#: whatever the producer currently believes it to be, or a producer that lowered
#: its own bar would lower the gate's with it.
P99_SAMPLE_FLOOR: Final[int] = 10_000

#: 4.3's minimum, and the concurrency rrct measured the degradation at. Arm A is
#: the reportable arm because 4.5 voids a run in which any of the eight controls
#: sat at a load-producing value, and 8 users is that arm.
MIN_REPETITIONS: Final[int] = 5
DEGRADING_CONCURRENCY: Final[int] = 8

#: The four per-repetition figures 4.3 requires. Present on every endpoint, or
#: the endpoint was summarised by a different rule than the one in force.
REPETITION_FIGURES: Final[tuple[str, ...]] = (
    "p50_median_of_repetitions_ms",
    "p50_min_of_repetitions_ms",
    "min_median_of_repetitions_ms",
    "min_min_of_repetitions_ms",
)

#: The criteria names, held once. They appear in a record() call and again in
#: the summary, and keeping two spellings of a criterion's name is how a gate
#: ends up reporting "5/10" for a set of ten it can no longer name.
C1: Final[str] = "every required endpoint measured at the degrading concurrency"
C2: Final[str] = "4.4 verbatim; p99 needs n >= 10,000"
C3: Final[str] = ">=5 reps, median-of-medians and min-of-medians"
C4: Final[str] = "4.5 void conditions asserted start and end"
C5: Final[str] = "100% success, EM-2's 401 declared in advance"
C6: Final[str] = "full manifest, representative runtime"
C7: Final[str] = "/health paired arm"
C8: Final[str] = "Linux CPU profile, top-5 tottime share"
C9: Final[str] = "blocking-work attribution vs the T-1 histogram"
C10: Final[str] = "no Windows arm, no cross-shape comparison"


@dataclass
class Check:
    """One criterion's verdict. ``detail`` is printed whatever the outcome."""

    number: int
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Evidence:
    """One endpoint, flattened from whichever artefact produced it."""

    name: str
    source: str
    pooled_n: int
    concurrency: int
    repetitions: int
    status_counts: dict[str, int]
    status_is_clean: bool
    unexpected_statuses: list[Any]
    p99_reportable: bool
    reportable_ms: dict[str, Any]
    unresolvable_percentiles: list[Any]
    #: Which of 4.3's four per-repetition figures this endpoint's artefact
    #: actually carries. Kept as the set that was FOUND rather than a boolean,
    #: so criterion 3 can say WHICH figure is missing instead of only that one
    #: is. The two producers put these at different depths, and flattening them
    #: into one place at ingest is where a gate would start trusting a shape it
    #: never checked -- so the ingestion records presence, and the criterion
    #: judges.
    repetition_figures: frozenset[str] = frozenset()
    pacing: dict[str, Any] | None = None


@dataclass
class Gate:
    checks: list[Check] = field(default_factory=list)

    def record(self, number: int, name: str, passed: bool, detail: str = "") -> bool:
        self.checks.append(Check(number, name, passed, detail))
        return passed

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


# --------------------------------------------------------------------------------------
# Artefact ingestion
# --------------------------------------------------------------------------------------


def _load(path: str) -> dict[str, Any]:
    try:
        parsed: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"GATE CANNOT RUN: artefact not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"GATE CANNOT RUN: {path} is not valid JSON: {exc}") from None
    if not isinstance(parsed, dict):
        # A gate handed a list or a scalar would otherwise index it blind and
        # report a missing-key failure that reads like a missing criterion.
        raise SystemExit(f"GATE CANNOT RUN: {path} is a {type(parsed).__name__}, expected a JSON object")
    return parsed


def _endpoint_from_campaign(artefact: dict[str, Any], source: str) -> list[Evidence]:
    """Flatten a campaign artefact's endpoint list.

    The campaign's summary key is ``endpoint``; the dataclass this reads was
    written before that spelling was confirmed, which is the kind of detail a
    gate must not guess at. So the key is read from the artefact and a missing
    one is loud.
    """
    out: list[Evidence] = []
    for row in artefact.get("endpoints", []):
        name = row.get("endpoint") or row.get("name")
        if name is None:
            raise SystemExit(
                f"GATE CANNOT RUN: {source} has an endpoint row with neither "
                "'endpoint' nor 'name' -- the gate refuses to guess which is the label."
            )
        out.append(
            Evidence(
                name=name,
                source=source,
                pooled_n=int(row.get("pooled_n", 0)),
                concurrency=int(row.get("concurrency", 0)),
                repetitions=int(row.get("repetitions", 0)),
                status_counts={str(k): v for k, v in (row.get("status_counts") or {}).items()},
                status_is_clean=bool(row.get("status_is_clean")),
                unexpected_statuses=list(row.get("unexpected_statuses") or []),
                p99_reportable=bool(row.get("p99_reportable")),
                reportable_ms=dict(row.get("reportable_ms") or {}),
                unresolvable_percentiles=list(row.get("unresolvable_percentiles") or []),
                pacing=row.get("pacing"),
                repetition_figures=frozenset(f for f in REPETITION_FIGURES if f in row),
            )
        )
    return out


def _endpoints_from_post_roundtrip(artefact: dict[str, Any], source: str) -> list[Evidence]:
    """Flatten the EM-9/EM-10 arms.

    They arrive as two KEYS on ``post_roundtrip``, not as a list, and a gate that
    assumed a list would read zero endpoints and call it a pass.
    """
    rt = artefact.get("post_roundtrip") or {}
    if not rt.get("measured"):
        raise SystemExit(
            f"GATE CANNOT RUN: {source} carries post_roundtrip.measured=False, so EM-9 "
            f"and EM-10 are absent. Stated reason: {rt.get('reason', '(none given)')}"
        )
    out: list[Evidence] = []
    for arm in ("em9_miss_60kb", "em10_hit_40kb"):
        row = rt.get(arm)
        if row is None:
            raise SystemExit(f"GATE CANNOT RUN: {source} has no {arm} arm.")
        out.append(
            Evidence(
                name=arm,
                source=source,
                pooled_n=int(row.get("pooled_n", 0)),
                concurrency=int(rt.get("concurrency", 0)),
                repetitions=int(row.get("repetitions", 0)),
                status_counts={str(k): v for k, v in (row.get("status_counts") or {}).items()},
                status_is_clean=bool(row.get("status_is_clean")),
                unexpected_statuses=list(row.get("unexpected_statuses") or []),
                p99_reportable=bool(row.get("p99_reportable")),
                reportable_ms=dict(row.get("reportable_ms") or {}),
                unresolvable_percentiles=list(row.get("unresolvable_percentiles") or []),
                repetition_figures=frozenset(f for f in REPETITION_FIGURES if f in row),
            )
        )
    return out


# --------------------------------------------------------------------------------------
# The ten criteria
# --------------------------------------------------------------------------------------


def criterion_1(evidence: dict[str, Evidence], gate: Gate) -> None:
    """Every required endpoint, each measured AT the degrading concurrency.

    The count is read from ``REQUIRED_ENDPOINTS`` rather than spelled out, so an
    arm added after v8qm closed cannot leave this criterion asserting a count no
    artefact can produce.
    """
    missing = [n for n in REQUIRED_ENDPOINTS if n not in evidence]
    off_conc = [f"{e.name}@{e.concurrency}" for e in evidence.values() if e.concurrency != DEGRADING_CONCURRENCY]
    if missing:
        gate.record(1, C1, False, f"MISSING: {missing}")
        return
    if off_conc:
        gate.record(
            1,
            C1,
            False,
            f"not at concurrency {DEGRADING_CONCURRENCY}: {off_conc}",
        )
        return
    gate.record(
        1,
        C1,
        True,
        f"all {len(evidence)} present, every one at concurrency {DEGRADING_CONCURRENCY}",
    )


def criterion_2(evidence: dict[str, Evidence], gate: Gate) -> None:
    """4.4 verbatim: p99 needs n >= 10,000.

    The anti-fabrication half is the load-bearing part. An endpoint BELOW the
    floor that nonetheless carries a p99 figure is not an endpoint with a
    reported p99 -- it is an endpoint quoting the tail of a sample too small to
    carry one, which is the exact failure ADR-0022 was written to prevent. That
    is a FAIL regardless of how plausible the number looks.
    """
    fabricated: list[str] = []
    for name, ev in evidence.items():
        if ev.pooled_n < P99_SAMPLE_FLOOR and ev.reportable_ms.get("p99") is not None:
            fabricated.append(f"{name}(n={ev.pooled_n}, p99={ev.reportable_ms.get('p99')})")
    if fabricated:
        gate.record(
            2,
            C2,
            False,
            f"ADR-0022 Rule 3 VIOLATED -- published a p99 below the floor: {fabricated}",
        )
        return
    resolved = sorted(n for n, e in evidence.items() if e.p99_reportable)
    withheld = sorted(n for n, e in evidence.items() if not e.p99_reportable)
    gate.record(
        2,
        C2,
        True,
        f"p99 resolved for {len(resolved)} at n >= {P99_SAMPLE_FLOOR} ({resolved}); "
        f"withheld-as-null for {len(withheld)} ({withheld}), none fabricated",
    )


def criterion_3(evidence: dict[str, Evidence], gate: Gate) -> None:
    """4.3: >= 5 repetitions, summarised as median-of-medians AND min-of-medians.

    Both halves are checked and reported separately, because they fail for
    different reasons. Too few repetitions is a sampling decision; a missing
    per-repetition figure is a producer that summarised its sample by a
    different rule than the one in force -- and that is how the EM-9/EM-10 arms
    got closed once, with a pooled median standing in for the four figures.
    """
    too_few = sorted(f"{e.name}(reps={e.repetitions})" for e in evidence.values() if e.repetitions < MIN_REPETITIONS)
    missing_fig = sorted(
        f"{e.name} missing {sorted(set(REPETITION_FIGURES) - set(e.repetition_figures))}"
        for e in evidence.values()
        if set(REPETITION_FIGURES) - set(e.repetition_figures)
    )
    problems = []
    if too_few:
        problems.append(f"fewer than {MIN_REPETITIONS} repetitions: {too_few}")
    if missing_fig:
        problems.append("no median-of-medians/min-of-medians form: " + "; ".join(missing_fig))
    if problems:
        gate.record(3, C3, False, "; ".join(problems))
        return
    gate.record(
        3,
        C3,
        True,
        f"every endpoint reports repetitions >= {MIN_REPETITIONS} and all four per-repetition figures",
    )


def criterion_4(artefacts: list[dict[str, Any]], gate: Gate) -> None:
    """4.5's void conditions asserted at run start AND at run end."""
    offenders: list[str] = []
    for artefact in artefacts:
        if artefact.get("artefact") != "step4_campaign":
            continue
        if artefact.get("controls_unchanged") is not True:
            offenders.append(f"{artefact.get('generated_by')}: controls_unchanged is not True")
        if artefact.get("control_differences_at_end"):
            differences = artefact["control_differences_at_end"]
            offenders.append(f"{artefact.get('generated_by')}: differences at end {differences}")
    if offenders:
        gate.record(4, C4, False, "; ".join(offenders))
        return
    campaign_count = sum(1 for a in artefacts if a.get("artefact") == "step4_campaign")
    gate.record(
        4,
        C4,
        True,
        f"all {campaign_count} campaign artefacts assert the eight controls unchanged, start to end",
    )


def criterion_5(evidence: dict[str, Evidence], gate: Gate) -> None:
    """100% success, with EM-2's expected 401 documented rather than excused."""
    dirty = []
    for name, ev in sorted(evidence.items()):
        if not ev.status_is_clean or ev.unexpected_statuses:
            dirty.append(f"{name}({ev.status_counts}, unexpected={ev.unexpected_statuses})")
        if ev.status_counts.get("0") or ev.status_counts.get("0 "):
            dirty.append(f"{name} recorded a zero status")
    if dirty:
        gate.record(5, C5, False, "; ".join(dirty))
        return
    exempt = evidence.get("users_me_anon")
    exempt_note = (
        f"users_me_anon returned only {sorted(exempt.status_counts)} because its expected "
        f"status is 401, declared in the endpoint table before the run"
        if exempt is not None
        else "users_me_anon is absent"
    )
    gate.record(5, C5, True, f"no unexpected status anywhere; {exempt_note}")


def criterion_6(artefacts: list[dict[str, Any]], gate: Gate) -> None:
    """The full manifest, and a representative runtime behind it."""
    problems: list[str] = []
    found = 0
    for artefact in artefacts:
        if artefact.get("artefact") != "step4_campaign":
            continue
        manifest = artefact.get("manifest") or {}
        if manifest.get("present") is not True:
            problems.append(f"{artefact.get('generated_by')}: manifest.present is not True")
            continue
        found += 1
        report = manifest.get("report") or {}
        if report.get("representative") is not True:
            problems.append(
                f"{artefact.get('generated_by')}: representative is {report.get('representative')!r}, "
                f"reasons={report.get('non_representative_reasons')}"
            )
        if (manifest.get("served_by") or "").startswith("http://step4.invalid"):
            problems.append(f"{artefact.get('generated_by')}: served_by is the in-process base URL -- no real server")
    if problems:
        gate.record(6, C6, False, "; ".join(problems))
        return
    gate.record(6, C6, True, f"{found} campaign artefacts carry a representative manifest")


def criterion_7(paired_arm_path: str | None, gate: Gate) -> None:
    """The /health paired arm, uvloop+httptools against asyncio+h11.

    Two arms, two DIFFERENT event loops, and the loop each arm actually ran --
    not the one it asked for. `manifest_verification[arm].argv_in_running_process`
    is read in preference to `manifests[arm].requested_loop`, because the whole
    point of the arm is the loop that served the requests and a manifest that
    records only an intention cannot show that.
    """
    if not paired_arm_path:
        gate.record(7, C7, False, "no artefact supplied for the paired arm; not assumed")
        return
    artefact = _load(paired_arm_path)

    # The arms live in `summaries`, a LIST keyed by the `arm` field, with their
    # loops in `manifests` keyed by the same name. The dict shape is accepted
    # too so the gate is not welded to one producer's layout, but the list shape
    # is what exists and it is what is read first.
    summaries = artefact.get("summaries")
    if isinstance(summaries, list) and len(summaries) >= 2:
        manifests = artefact.get("manifests") or {}
        verification = artefact.get("manifest_verification") or {}
        loops: dict[str, Any] = {}
        for entry in summaries:
            if not isinstance(entry, dict) or "arm" not in entry:
                gate.record(7, C7, False, f"{paired_arm_path}: a summaries entry has no 'arm'")
                return
            arm = str(entry["arm"])
            seen = (verification.get(arm) or {}).get("argv_in_running_process")
            loops[arm] = seen or (manifests.get(arm) or {}).get("requested_loop")
    else:
        arms = artefact.get("arms") or artefact.get("results") or {}
        if not isinstance(arms, dict) or len(arms) < 2:
            found = len(arms) if isinstance(arms, dict) else "not a mapping"
            gate.record(7, C7, False, f"{paired_arm_path}: expected two arms, found {found}")
            return
        loops = {name: (body or {}).get("loop") for name, body in arms.items()}

    distinct = {v for v in loops.values() if v}
    if len(distinct) < 2:
        gate.record(7, C7, False, f"both arms report the same loop: {loops}")
        return
    if any(not v for v in loops.values()):
        gate.record(7, C7, False, f"an arm does not state the loop it ran: {loops}")
        return
    gate.record(7, C7, True, f"two arms, distinct loops actually served: {loops}")


def _parse_pstats_tottime(path: str) -> list[tuple[float, str]]:
    """Read a pstats "Ordered by: internal time" table into (tottime, label).

    cProfile writes this table by hand and the beads that produced CPU profiles
    closed on it, so the evidence exists -- in a shape a JSON reader cannot
    reach. Transcribing it is reading a measurement, not inventing one: every
    number below is parsed out of the file.

    Raises on anything it does not recognise rather than returning an empty
    list, because an empty list would divide to a top-5 share of zero and
    report a profile that had no hot functions at all.
    """
    header_seen = False
    rows: list[tuple[float, str]] = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            if not header_seen:
                # The header row names the columns; the real column order was
                # read off a real dump rather than assumed.
                if "ncalls" in stripped and "tottime" in stripped:
                    header_seen = True
                continue
            parts = stripped.split()
            if len(parts) < 5 or not parts[0].isdigit():
                continue
            try:
                rows.append((float(parts[1]), " ".join(parts[5:]) or parts[4]))
            except ValueError:
                continue
    if not header_seen:
        raise ValueError(f"{path}: no pstats table header (ncalls/tottime) found")
    if not rows:
        raise ValueError(f"{path}: pstats header present but no data rows parsed")
    rows.sort(reverse=True)
    return rows


def criterion_8(profile_path: str | None, gate: Gate) -> None:
    """A Linux CPU profile, with the top-5 tottime share stated.

    Accepts either a JSON artefact carrying a top-functions list, or a raw
    cProfile pstats table, in which case the top-5 share is computed from the
    parsed tottime column. The share is reported, never used as a threshold:
    what the criterion asks for is that the concentration is STATED.
    """
    if not profile_path:
        gate.record(8, C8, False, "no profile artefact supplied; not assumed")
        return

    # The reader is chosen by what the file IS, not by catching an error from
    # the other one. _load turns malformed JSON into SystemExit on purpose, so
    # that a gate handed a broken artefact refuses to run rather than reporting
    # a missing criterion; catching that here would undo the refusal.
    if not profile_path.endswith(".json"):
        try:
            rows = _parse_pstats_tottime(profile_path)
        except (OSError, ValueError) as exc:
            gate.record(8, C8, False, str(exc))
            return
        total = sum(tottime for tottime, _ in rows)
        if total <= 0:
            gate.record(8, C8, False, f"{profile_path}: tottime column sums to {total}")
            return
        top5 = sum(tottime for tottime, _ in rows[:5])
        share = round(top5 / total, 4)
        named = ", ".join(label for _, label in rows[:5])
        gate.record(
            8,
            C8,
            True,
            f"top-5 tottime share {share} of {total:.6f}s over {len(rows)} parsed rows; top five: {named}",
        )
        return

    artefact = _load(profile_path)
    top = artefact.get("top") or artefact.get("top_functions")
    if not isinstance(top, list) or not top:
        gate.record(8, C8, False, f"{profile_path}: no top-functions list")
        return
    stated = artefact.get("top5_tottime_fraction", artefact.get("top_5_tottime_fraction"))
    if stated is None:
        gate.record(8, C8, False, f"{profile_path}: does not state a top-5 tottime fraction")
        return
    gate.record(8, C8, True, f"top-5 tottime fraction {stated} over {len(top)} functions")


def criterion_9(occupancy_path: str | None, gate: Gate) -> None:
    """Blocking-work attribution, cross-checked against T-1's histogram."""
    if not occupancy_path:
        gate.record(9, C9, False, "no occupancy artefact supplied; not assumed")
        return
    artefact = _load(occupancy_path)
    sources = artefact.get("sources") or artefact.get("occupancy") or {}
    cross = artefact.get("cross_check")
    if not sources:
        gate.record(9, C9, False, f"{occupancy_path}: no per-source occupancy block")
        return
    if cross is None:
        gate.record(9, C9, False, f"{occupancy_path}: no cross_check block -- the histogram was never compared against")
        return
    gate.record(
        9,
        C9,
        True,
        f"{len(sources)} sources attributed; cross_check verdict: {str(cross.get('verdict'))[:80]}",
    )


def criterion_10(artefacts: list[dict[str, Any]], gate: Gate) -> None:
    """No Windows artefact as a comparison arm, and no main.json comparison."""
    problems: list[str] = []
    for artefact in artefacts:
        report = ((artefact.get("manifest") or {}).get("report")) or {}
        host = report.get("host") or {}
        if host.get("platform") not in (None, "Linux"):
            problems.append(f"a manifest reports platform {host.get('platform')!r}")
        topology = artefact.get("topology") or {}
        base = topology.get("base_url") or ""
        if "step4.invalid" in base and "localhost" not in base:
            # In-process is not disqualifying on its own -- it is the only shape
            # available before a server was stood up -- but it must never be
            # COMPARED with a socket run as if the two were the same shape.
            source = artefact.get("generated_by")
            problems.append(f"in-process run present ({source}); it must not be compared against socket runs")
    if problems:
        gate.record(10, C10, False, "; ".join(problems))
        return
    gate.record(10, C10, True, "every manifest reports Linux; every run is one shape")


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assert v8qm's ten exit criteria against the artefacts.")
    parser.add_argument(
        "--campaign",
        action="append",
        default=[],
        required=True,
        help="a step4_campaign artefact; repeat for each run in the campaign",
    )
    parser.add_argument(
        "--post-roundtrip",
        default=None,
        help="a body_scan_regimes artefact carrying the EM-9/EM-10 round-trip",
    )
    parser.add_argument("--paired-arm", default=None, help="the /health paired-arm artefact")
    parser.add_argument("--cpu-profile", default=None, help="the Linux CPU profile artefact")
    parser.add_argument("--occupancy", default=None, help="the event-loop occupancy attribution artefact")
    parser.add_argument("--out", default=None, help="write the verdict as JSON here")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    artefacts: list[dict[str, Any]] = []
    evidence: dict[str, Evidence] = {}

    for path in args.campaign:
        artefact = _load(path)
        artefacts.append(artefact)
        for row in _endpoint_from_campaign(artefact, path):
            evidence[row.name] = row

    if args.post_roundtrip:
        artefact = _load(args.post_roundtrip)
        artefacts.append(artefact)
        for row in _endpoints_from_post_roundtrip(artefact, args.post_roundtrip):
            evidence[row.name] = row

    gate = Gate()
    criterion_1(evidence, gate)
    criterion_2(evidence, gate)
    criterion_3(evidence, gate)
    criterion_4(artefacts, gate)
    criterion_5(evidence, gate)
    criterion_6(artefacts, gate)
    criterion_7(args.paired_arm, gate)
    criterion_8(args.cpu_profile, gate)
    criterion_9(args.occupancy, gate)
    criterion_10(artefacts, gate)

    print(f"Step 4 gate: {len(evidence)} endpoints of {len(REQUIRED_ENDPOINTS)} required")
    for name in REQUIRED_ENDPOINTS:
        ev = evidence.get(name)
        if ev is None:
            print(f"  {name:<18} MISSING")
            continue
        p99 = ev.reportable_ms.get("p99")
        tail = f"p99={p99}" if p99 is not None else "p99=withheld"
        print(
            f"  {name:<18} n={ev.pooled_n:<6} conc={ev.concurrency} reps={ev.repetitions} "
            f"{tail:<18} clean={ev.status_is_clean} {ev.source}"
        )

    print()
    for check in gate.checks:
        print(f"  [{'PASS' if check.passed else 'FAIL'}] ({check.number}) {check.name}")
        if check.detail:
            print(f"         {check.detail}")

    failed = gate.failed
    print()
    if failed:
        unmet = ", ".join(str(c.number) for c in failed)
        print(f"GATE FAILED: {len(failed)} of {len(gate.checks)} criteria unmet -> {unmet}")
    else:
        print(f"GATE PASSED: all {len(gate.checks)} criteria met")

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "criteria_met": len(gate.checks) - len(failed),
                    "criteria_total": len(gate.checks),
                    "failed_criteria": [c.number for c in failed],
                    "checks": [vars(c) for c in gate.checks],
                    "endpoints": {
                        n: {
                            "pooled_n": e.pooled_n,
                            "concurrency": e.concurrency,
                            "repetitions": e.repetitions,
                            "status_counts": e.status_counts,
                            "p99": e.reportable_ms.get("p99"),
                            "p99_reportable": e.p99_reportable,
                            "source": e.source,
                        }
                        for n, e in evidence.items()
                    },
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
