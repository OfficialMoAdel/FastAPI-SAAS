"""The A/B harness must INTERLEAVE the two arms, not measure them in blocks.

WHY THIS FILE EXISTS
--------------------
`scripts/ab_users_me.py` measures two arms -- `4cd00654` (before) and
`7de0ff2a` (after) -- under one instrument, to decide whether a claimed
`/users/me` tail regression is in the app or in the measurement path. The
discriminator is TWO VIEWS OF THE SAME REQUEST: the client-observed latency
and the server-side latency.

The harness originally measured the arms SEQUENTIALLY: the whole of the
before arm's repetitions, then the whole of the after arm's. On one host
with one database pool, that makes every millisecond of host drift between
the two blocks indistinguishable from the difference between the two arms.
The failure is not hypothetical: a separate measurement session on the same
host recorded a monotonic inflation across the repetitions of a single
non-interleaved run (149 us -> 529 us), and the run had to be discarded as
host drift rather than published as a result.

So the arms must ALTERNATE: repetition 1 of before, repetition 1 of after,
repetition 2 of before, repetition 2 of after, and so on. The arms still
never run at the same time -- one host, one database pool, and running them
together would create the very confounder this design exists to measure
rather than create. Alternating, not overlapping.

WHAT IS ASSERTED
----------------
(a) the collect phase alternates between the arms, one repetition each, in
    lockstep;
(b) the warm-up phase of BOTH arms happens before EITHER arm is measured --
    an arm that is measured before the other arm is warm would be measured
    under different conditions, which is the same confound in a smaller
    window;
(c) neither arm's request stream ever overlaps the other's;
(d) each arm still keeps one client and one server sample list per
    repetition (GATE A(9) reads the repetition form, and dropping it would
    leave the gate able to pass on half the evidence);
(e) the report's key set is unchanged, so a new run stays comparable with
    the preserved `qualifying_run.json` / `wyjb_verify.json` / `short_run.json`
    reports of the earlier attempts.

The earlier reports ARE this script's own output -- measured, not assumed:
all three carry exactly the nine top-level keys `build_artefact` emits
today, so the comparison is EQUALITY in both directions, not a subset.
(The per-arm key sets differ between the three preserved reports --
13 keys in `qualifying_run.json`, 14 in `short_run.json`'s before arm, 15
in `wyjb_verify.json` -- because they were produced by three different
versions of the script. The current script's 15 per-arm keys match
`wyjb_verify.json`, the most recent of the three.)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS_PATH = REPO_ROOT / "scripts" / "ab_users_me.py"
PRESERVED_REPORT = REPO_ROOT / "perf-results" / "RESULT-20261015-vpbi" / "qualifying_run.json"

#: The report's top-level keys, pinned. A change here is a change to the
#: artefact contract, and the earlier reports are the comparison base.
REPORT_KEYS = frozenset(
    {
        "artefact",
        "generated_by",
        "question",
        "arms",
        "delta",
        "client_p95_ratio",
        "server_p95_ratio",
        "gate_a_compliance",
        "instrument",
    }
)


def _load_harness() -> Any:
    spec = importlib.util.spec_from_file_location("ab_users_me_harness", HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # The module is REGISTERED before it is executed. `@dataclass` resolves
    # the decorated class's `__module__` through `sys.modules`, and a module
    # that was never registered resolves to None -- which makes the decorator
    # raise `AttributeError: 'NoneType' object has no attribute '__dict__'`
    # before a single test in this file can run.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness() -> Any:
    return _load_harness()


def _args(harness: Any, **overrides: Any) -> argparse.Namespace:
    """The parser's own defaults, so the attribute set matches the real run."""
    argv = [
        "--env",
        str(REPO_ROOT / "src" / ".env"),
        "--before-url",
        "http://127.0.0.1:8021",
        "--after-url",
        "http://127.0.0.1:8022",
        "--before-worktree",
        "/home/ansible/ab/before",
        "--after-worktree",
        "/home/ansible/ab/after",
        "--before-arm-json",
        "/tmp/before_arm.json",
        "--after-arm-json",
        "/tmp/after_arm.json",
        "--out",
        str(REPO_ROOT / "reports" / "ab_users_me_test.json"),
    ]
    for key, value in overrides.items():
        argv.extend(["--" + key.replace("_", "-"), str(value)])
    return harness.build_parser().parse_args(argv)


def _arm(harness: Any, name: str) -> Any:
    port = 8021 if name == "before" else 8022
    return harness.Arm(name, f"http://127.0.0.1:{port}", f"/tmp/{name}", f"/tmp/{name}.json")


class _Recorder:
    """Stands in for the request driver and records what it was asked to do."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.open: set[str] = set()
        self.overlapped: list[tuple[str, str]] = []

    async def __call__(self, arm: Any, *, count: int, concurrency: int, collect: bool) -> None:
        phase = "collect" if collect else "warm"
        self.calls.append((arm.name, phase))
        if arm.name in self.open:
            self.overlapped.append((arm.name, phase))
        self.open.add(arm.name)
        try:
            if collect:
                for index in range(count):
                    arm.client_ms.append(1.0 + index)
                    arm.server_ms.append(0.5 + index)
                    arm.status_counts[200] = arm.status_counts.get(200, 0) + 1
        finally:
            self.open.discard(arm.name)


def _stub_the_out_of_process_parts(harness: Any, monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> list[str]:
    """Replace everything that touches a database, a network or a file.

    Returns the list of events the stubbed parts record, so the test can
    assert the ORDER of prepare / measure / export.
    """
    events: list[str] = []

    async def fake_seed_accounts(*, arm: str, count: int, tier: str, tenant: str = "") -> list[str]:
        events.append(f"seed:{arm}:{tenant}")
        return [f"ab{arm}{index:04d}" for index in range(count)]

    async def fake_login(arm: Any) -> None:
        events.append(f"login:{arm.name}")
        arm.tokens = {account: f"token-{account}" for account in arm.accounts}

    async def fake_read_arm_export(arm: Any) -> dict[str, Any]:
        events.append(f"export:{arm.name}")
        return {}

    def fake_fold_arm_export(arm: Any, export: dict[str, Any]) -> None:
        events.append(f"fold:{arm.name}")

    def fake_build_artefact(arms: list[Any], args: argparse.Namespace) -> dict[str, Any]:
        events.append("build_artefact")
        return {"artefact": "ab_users_me"}

    monkeypatch.setattr(harness, "seed_accounts", fake_seed_accounts)
    monkeypatch.setattr(harness, "login", fake_login)
    monkeypatch.setattr(harness, "_read_arm_export", fake_read_arm_export)
    monkeypatch.setattr(harness, "_fold_arm_export", fake_fold_arm_export)
    monkeypatch.setattr(harness, "build_artefact", fake_build_artefact)
    monkeypatch.setattr(harness, "_drive_once", recorder)
    return events


class TestTheArmsAreInterleaved:
    async def test_the_collect_phase_alternates_between_the_arms(
        self, harness: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder()
        _stub_the_out_of_process_parts(harness, monkeypatch, recorder)
        args = _args(harness, repetitions=5, requests_per_rep=2, users=2, warmup=1)

        await harness.amain(args)

        collect = [name for name, phase in recorder.calls if phase == "collect"]
        assert collect == ["before", "after"] * 5, (
            "the arms must ALTERNATE one repetition each; a block of five before "
            f"the other arm's five is what makes host drift indistinguishable from "
            f"the arm difference (got {collect})"
        )

    async def test_both_arms_are_warmed_before_either_is_measured(
        self, harness: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder()
        _stub_the_out_of_process_parts(harness, monkeypatch, recorder)
        args = _args(harness, repetitions=2, requests_per_rep=1, users=1, warmup=1)

        await harness.amain(args)

        phases = [(name, phase) for name, phase in recorder.calls]
        warm_positions = [index for index, (_, phase) in enumerate(phases) if phase == "warm"]
        first_collect = next(index for index, (_, phase) in enumerate(phases) if phase == "collect")
        assert len(warm_positions) == 2
        assert max(warm_positions) < first_collect, (
            "both arms must be warm before either is measured; an arm measured "
            "while the other is still cold is measured under different conditions"
        )
        assert [phases[index][0] for index in warm_positions] == ["before", "after"]

    async def test_the_arms_never_overlap(
        self, harness: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder()
        _stub_the_out_of_process_parts(harness, monkeypatch, recorder)
        args = _args(harness, repetitions=3, requests_per_rep=1, users=1, warmup=1)

        await harness.amain(args)

        assert recorder.overlapped == [], (
            "the two arms must never be in flight at the same time: one host, one "
            "database pool, and running them together would CREATE the confounder "
            "this design exists to measure"
        )

    async def test_each_arm_keeps_one_sample_list_per_repetition(
        self, harness: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder()
        _stub_the_out_of_process_parts(harness, monkeypatch, recorder)
        args = _args(harness, repetitions=5, requests_per_rep=2, users=1, warmup=1)

        built: dict[str, Any] = {}
        original = harness.build_artefact

        def capture(arms: list[Any], args_: argparse.Namespace) -> dict[str, Any]:
            built["arms"] = {arm.name: arm for arm in arms}
            return original(arms, args_)

        monkeypatch.setattr(harness, "build_artefact", capture)
        await harness.amain(args)

        for name, arm in built["arms"].items():
            assert len(arm.per_rep_client) == 5, f"{name} lost a repetition's client samples"
            assert len(arm.per_rep_server) == 5, f"{name} lost a repetition's server samples"
            assert sum(len(chunk) for chunk in arm.per_rep_client) == 10
            assert sum(len(chunk) for chunk in arm.per_rep_server) == 10


class TestTheReportShapeIsUnchanged:
    def test_the_report_carries_the_pinned_key_set(self, harness: Any) -> None:
        before = _arm(harness, "before")
        after = _arm(harness, "after")
        args = _args(harness)
        artefact = harness.build_artefact([before, after], args)
        assert set(artefact) == REPORT_KEYS

    def test_every_key_this_script_emits_is_in_the_preserved_report(self) -> None:
        assert PRESERVED_REPORT.is_file(), (
            "the preserved report is the comparison base for a new run; without "
            "it a new run cannot be compared with the earlier attempts"
        )
        preserved = json.loads(PRESERVED_REPORT.read_text(encoding="utf-8"))
        missing = REPORT_KEYS - set(preserved)
        assert not missing, f"the preserved report does not carry {sorted(missing)}"

    def test_the_preserved_report_carries_exactly_the_same_key_set(self) -> None:
        """The preserved reports ARE this script's output -- so the comparison
        is EQUALITY in both directions, not a subset.

        The earlier belief was that the preserved reports had been reduced by
        a reducer that is not in the repository, and that they therefore
        carried extra keys (`verdict`, `detail`, `noise_floor_intra_arm`,
        `noise_floor_run_to_run`, `client_ratio_exceeds_floor`,
        `server_ratio_exceeds_floor`) this script does not emit. Measured
        against the files, that is false: all three preserved reports carry
        exactly the nine keys `build_artefact` emits. A new run can therefore
        be compared with them field for field, and a divergence in either
        direction is a change to the artefact contract.
        """
        preserved = json.loads(PRESERVED_REPORT.read_text(encoding="utf-8"))
        assert set(preserved) == REPORT_KEYS, (
            "the preserved report's top-level key set is the comparison base for "
            f"a new run and it changed (got {sorted(preserved)})"
        )

    def test_every_preserved_report_carries_the_same_top_level_keys(self) -> None:
        """All three earlier attempts used the same report contract."""
        directory = PRESERVED_REPORT.parent
        for name in ("qualifying_run.json", "wyjb_verify.json", "short_run.json"):
            path = directory / name
            assert path.is_file(), f"{name} is missing from the preserved evidence"
            report = json.loads(path.read_text(encoding="utf-8"))
            assert set(report) == REPORT_KEYS, (
                f"{name} carries a different top-level key set than the script "
                f"emits today (got {sorted(report)})"
            )


def test_the_harness_still_exposes_the_fairness_controls(harness: Any) -> None:
    """The interleaving must not have cost the harness any of its guards.

    The guards are the difference between a figure and an artefact: a run
    through a limiter is not a figure about the 28 commits, and a run with
    only one view of the request cannot say which view is lying.
    """
    assert harness.P99_SAMPLE_FLOOR == 10_000
    assert harness.MIN_REPETITIONS == 5
    assert harness.NOISE_FLOOR_INTRA_ARM == 2.0
    assert harness.NOISE_FLOOR_RUN_TO_RUN == 7.1
    assert harness.SERVER_MS_HEADER == "x-ab-server-ms"
    assert harness.ARM_EXPORT_WAIT_SECONDS == 30.0
    assert callable(harness.build_artefact)
    assert callable(harness._classify)


class TestTheFixtureIsRecorded:
    """The tenant and the tier are FIXTURE choices, and they are part of the figure.

    A figure taken in an environment that the artefact does not describe is a
    figure whose context has to be reconstructed from the machine it ran on.
    The environment under the earlier attempts changed underneath that
    evidence -- the `system` tenant is gone, the seeded accounts are gone,
    and the tier rows are now named `Free`/`Pro` where they were
    `free`/`pro` -- so the fixture is now named explicitly and recorded.
    """

    def test_the_tenant_is_a_parameter_whose_default_is_the_historic_slug(self, harness: Any) -> None:
        assert harness.PROBE_TENANT == "system", (
            "the historic default tenant slug changed; if it moved on purpose, "
            "say so in the artefact instead of silently re-pointing the fixture"
        )
        args = _args(harness)
        assert args.tenant == "system"

    def test_the_tenant_is_a_parameter_and_not_a_constant(self, harness: Any) -> None:
        args = _args(harness, tenant="test-tenant-e2e")
        assert args.tenant == "test-tenant-e2e"

    async def test_both_arms_are_given_the_same_tenant(
        self, harness: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder()
        _stub_the_out_of_process_parts(harness, monkeypatch, recorder)
        args = _args(harness, tenant="test-tenant-e2e", repetitions=1, requests_per_rep=1, users=1, warmup=1)

        built: dict[str, Any] = {}
        original = harness.build_artefact

        def capture(arms: list[Any], args_: argparse.Namespace) -> dict[str, Any]:
            built["arms"] = {arm.name: arm for arm in arms}
            return original(arms, args_)

        monkeypatch.setattr(harness, "build_artefact", capture)
        await harness.amain(args)

        tenants = {name: arm.tenant for name, arm in built["arms"].items()}
        assert set(tenants.values()) == {"test-tenant-e2e"}, (
            "both arms must address the SAME tenant: the question is whether one "
            f"commit serves the request faster than the other, not whether two "
            f"different tenants do (got {tenants})"
        )

    def test_the_tenant_is_recorded_per_arm_in_the_artefact(self, harness: Any) -> None:
        before = _arm(harness, "before")
        after = _arm(harness, "after")
        before.tenant = "test-tenant-e2e"
        after.tenant = "test-tenant-e2e"
        args = _args(harness)
        artefact = harness.build_artefact([before, after], args)
        for name, arm in (("before", before), ("after", after)):
            assert artefact["arms"][name]["tenant"] == "test-tenant-e2e", (
                "the tenant a figure was taken in is part of the figure; an "
                "artefact that omits it has to be reconstructed from the machine"
            )

    def test_the_tier_lookup_is_case_insensitive_like_the_application(self, harness: Any) -> None:
        """The app lowercases the tier name; the fixture must agree.

        `rate_policy.py` resolves the per-tier budget with
        `str(tier_name).lower()`, and `resolve_entitlement` lowercases too, so
        a tier row named `Pro` and one named `pro` are the same tier to the
        application. A fixture that looked the tier up case-sensitively would
        refuse to seed against the database that exists.
        """
        source = HARNESS_PATH.read_text(encoding="utf-8")
        assert "lower(name) = lower($1)" in source, (
            "the tier lookup is case-sensitive while the application resolves "
            "the tier case-insensitively"
        )


class TestTheDeadSequentialPathIsGone:
    def test_the_harness_no_longer_defines_the_block_measurement_helper(self, harness: Any) -> None:
        """`run_arm` measured one arm's whole block and is replaced.

        It is kept out of the module on purpose: a helper that measures one
        arm end to end is exactly the shape that produced the earlier
        non-interleaved runs, and leaving it in the file invites the next
        reader to call it.
        """
        assert not hasattr(harness, "run_arm"), (
            "the block-measurement helper is back; the arms must be driven by "
            "the interleaved path"
        )
