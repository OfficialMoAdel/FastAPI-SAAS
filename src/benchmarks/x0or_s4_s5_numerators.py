"""S-4 and S-5: the two GATE B checkbox-2 numerators, measured on Linux.

WHAT THIS IS

Two of the five numerators in ``docs/verification/lane_b_checkbox2_arithmetic.md``
were carried as ``[Unverified - Needs-Linux-measure]``:

  * S-4 - the two-level copy against ``copy.deepcopy`` on the auth payload.
    The old pair (25.7 us -> 1.15 us, saving 24.55 us) was a WINDOWS
    microbenchmark, and the S-4 commit body said so itself.
  * S-5 - the request-id draw cost. The old figure (33 us) named NO HOST at
    all, and a host that is not named is not a Linux host.

This script measures both on the Linux target.

THE INSTRUMENT - re-implemented, and why that matters

The original S-4 and S-5 instruments are NOT in the repository: there is no
S-4 or S-5 microbenchmark under ``src/benchmarks/`` or ``scripts/``, and the
figures they produced came from a hand-rolled Windows script. This script
therefore does NOT claim to reproduce the old instrument. It uses the
repository's canonical protocol - ``src/benchmarks/protocol.py::measure`` -
which is what the M-4 bead recorded as the thing the old measurement SHOULD
have used ("single-shot min-of-N with no warmup instead of
``src/benchmarks/protocol.py::measure``"). The old figures are quoted below
FOR COMPARISON ONLY; they are not re-measured and they are not re-derived.

THE TWO PAIRS

  * S-4: the BEFORE arm is ``copy.deepcopy(payload)`` and the AFTER arm is
    ``_two_level_copy(payload)`` - the production function - on the SAME
    payload, which is asserted to satisfy the shape contract
    (``assert_user_payload_shape``) that makes the two-level copy an
    isomorphism. The payload is named in the artefact.
  * S-5: the BEFORE arm is the unconditional path - THREE independent
    ``uuid4`` draws, one per guard that used to mint its own id - and the
    AFTER arm is ONE draw, which is what the memoisation leaves on the
    unconditional path. The numerator is the difference: the cost of the two
    draws the change removed.

WHAT THIS IS NOT

  * not an endpoint measurement: no request is issued;
  * not a benchmark of the change: the before/after pair IS the numerator's
    own derivation, so it is reported as a pair and never as an improvement
    criterion for anything;
  * the arm comparison is asserted for VACUITY only - the two arms must not
    measure the same number - and NOT for which one is smaller.
"""

from __future__ import annotations

import copy
import json
import platform
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.app.middleware.request_id import resolve_request_id
from src.app.modules.users.cache import _two_level_copy, assert_user_payload_shape
from src.benchmarks.protocol import MicroResult, measure

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "perf-results" / "RESULT-20261020-x0or" / "s4_s5_numerators.json"

ITERATIONS = 2000
WARMUP = 100
REPETITIONS = 5

UNCONTENDED_FLOOR_US = 4090.0

# The auth payload, named: one level deep, with one nested "tier" whose
# values are all scalars. That is the shape assert_user_payload_shape
# admits, and it is the shape the two-level copy is an isomorphism of.
AUTH_PAYLOAD: dict[str, Any] = {
    "id": 1,
    "username": "e2etestuser",
    "email": "e2e_test@example.com",
    "is_active": True,
    "is_superuser": False,
    "tier": {"id": 3, "name": "pro", "level": 2},
}

# The figures the record carried before this measurement, quoted for
# comparison only. They were measured elsewhere, on other hosts, with an
# instrument that is not in the repository.
OLD_FIGURES = {
    "s4": {
        "deepcopy_us": 25.7,
        "two_level_copy_us": 1.15,
        "saving_us": 24.55,
        "host": "Windows (the S-4 commit body said so itself)",
        "tag": "[Unverified - Needs-Linux-measure]",
    },
    "s5": {
        "saving_us": 33.0,
        "host": "no host named at all",
        "tag": "[Unverified - Needs-Linux-measure]",
    },
}


def _result(result: MicroResult) -> dict[str, Any]:
    return {
        "best_us": result.best_us,
        "batch_mins_us": list(result.batch_mins_us),
        "batch_medians_us": list(result.batch_medians_us),
        "iterations": result.iterations,
        "repetitions": result.repetitions,
        "warmup": result.warmup,
        "spread_ratio": result.spread_ratio,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    # The payload must satisfy the shape contract, or the two-level copy is
    # not an isomorphism and the comparison would be meaningless.
    assert_user_payload_shape(AUTH_PAYLOAD)
    assert _two_level_copy(AUTH_PAYLOAD) == AUTH_PAYLOAD
    assert copy.deepcopy(AUTH_PAYLOAD) == AUTH_PAYLOAD

    s4_before = measure(
        lambda: copy.deepcopy(AUTH_PAYLOAD),
        name="s4_deepcopy",
        iterations=ITERATIONS,
        warmup=WARMUP,
        repetitions=REPETITIONS,
    )
    s4_after = measure(
        lambda: _two_level_copy(AUTH_PAYLOAD),
        name="s4_two_level_copy",
        iterations=ITERATIONS,
        warmup=WARMUP,
        repetitions=REPETITIONS,
    )

    def _three_draws() -> tuple[str, str, str]:
        return (resolve_request_id(None), resolve_request_id(None), resolve_request_id(None))

    def _one_draw() -> str:
        return resolve_request_id(None)

    s5_before = measure(
        _three_draws,
        name="s5_three_draws",
        iterations=ITERATIONS,
        warmup=WARMUP,
        repetitions=REPETITIONS,
    )
    s5_after = measure(
        _one_draw,
        name="s5_one_draw",
        iterations=ITERATIONS,
        warmup=WARMUP,
        repetitions=REPETITIONS,
    )

    s4_saving = s4_before.best_us - s4_after.best_us
    s5_saving = s5_before.best_us - s5_after.best_us

    # VACUITY only: the two arms must not be measuring the same number.
    # Which one is smaller is NOT asserted - the measurement reports it.
    assert s4_before.best_us != s4_after.best_us, "the S-4 arms measured the same number"
    assert s5_before.best_us != s5_after.best_us, "the S-5 arms measured the same number"

    artefact: dict[str, Any] = {
        "schema": "perf-s4-s5-numerators/1",
        "bead": "FastAPI_saas-x0or",
        "item": "S-4 and S-5",
        "title": "the S-4 two-level copy and the S-5 request-id draw cost, measured on Linux",
        "recorded_at": datetime.now(UTC).isoformat(),
        "method": (
            "Both numerators are measured with the repository's canonical protocol "
            "(src/benchmarks/protocol.py::measure: warm up, collect, min-of-N inside a batch, then the median "
            "of the per-batch minima). The ORIGINAL S-4 and S-5 instruments are not in the repository, so this "
            "does not reproduce them: the old figures are quoted for comparison only and are neither "
            "re-measured nor re-derived. Each item is reported as a BEFORE/AFTER PAIR."
        ),
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "in_repo": True,
            "harness": "src/benchmarks/x0or_s4_s5_numerators.py",
        },
        "protocol": {
            "source": "src/benchmarks/protocol.py::measure",
            "iterations": ITERATIONS,
            "warmup": WARMUP,
            "repetitions": REPETITIONS,
            "estimator": "min-of-N inside a batch, then the median of the per-batch minima",
            "the_original_instruments_are_not_committed": (
                "There is no S-4 or S-5 microbenchmark in src/benchmarks/ or scripts/. The old figures came "
                "from a hand-rolled Windows script, and the M-4 bead's own record lists as a defect it closed: "
                "'single-shot min-of-N with no warmup instead of src/benchmarks/protocol.py::measure'. This "
                "script uses the canonical protocol and compares against the OLD published figures, not "
                "against a re-run of the old instrument."
            ),
        },
        "s4_two_level_copy": {
            "what": "copy.deepcopy against the production _two_level_copy, on the same auth payload",
            "payload": {
                "description": (
                    "one level deep, with one nested 'tier' whose values are all scalars - the shape "
                    "assert_user_payload_shape admits, and the shape the two-level copy is an isomorphism of"
                ),
                "keys": sorted(AUTH_PAYLOAD),
                "tier_keys": sorted(AUTH_PAYLOAD["tier"]),
                "satisfies_the_shape_assertion": True,
            },
            "before_deepcopy": _result(s4_before),
            "after_two_level_copy": _result(s4_after),
            "numerator_us": s4_saving,
            "numerator_is": "the difference between the two arms on this payload",
            "fraction_of_the_4090_us_uncontended_floor_percent": round(s4_saving / UNCONTENDED_FLOOR_US * 100, 4),
            "the_old_figure_for_comparison_only": OLD_FIGURES["s4"],
        },
        "s5_request_id_draw": {
            "what": (
                "the unconditional path before the change - THREE independent uuid4 draws, one per guard that "
                "used to mint its own id - against the ONE draw the memoisation leaves on that path"
            ),
            "before_three_draws": _result(s5_before),
            "after_one_draw": _result(s5_after),
            "numerator_us": s5_saving,
            "numerator_is": "the cost of the two draws the change removed",
            "fraction_of_the_4090_us_uncontended_floor_percent": round(s5_saving / UNCONTENDED_FLOOR_US * 100, 4),
            "the_old_figure_for_comparison_only": OLD_FIGURES["s5"],
        },
        "denominator": {
            "uncontended_floor_us": UNCONTENDED_FLOOR_US,
            "uncontended_floor_source": (
                "GATE B's named floor; the committed corroboration is "
                "perf-results/RESULT-20261012-sweepB/sweepA_8_stats.csv (min 4.09577600294142 ms at "
                "concurrency 8)"
            ),
            "a_warm_p50_is_not_substituted_for_it": True,
        },
        "what_is_not_asserted": {
            "no_new_less_old": (
                "No improvement is claimed and no arm is asserted to be the smaller one: the arms are compared "
                "for VACUITY only (they must not measure the same number). The before/after pair IS the "
                "numerator's own derivation, so it is reported as a pair."
            ),
            "the_old_figures_are_not_re_measured": (
                "The old S-4 and S-5 figures are quoted for comparison only. They were measured on other "
                "hosts with an instrument that is not in the repository, so they are neither re-measured nor "
                "re-derived, and the difference between the old and the new figures is a difference between "
                "INSTRUMENTS as well as between hosts."
            ),
            "not_an_endpoint_measurement": "no request is issued and the app's lifespan is not run",
            "the_payload_is_named": (
                "The S-4 figure is the cost on the payload named above; a different payload shape is a "
                "different figure."
            ),
            "main_json_stub_never_compared": "tests/benchmarks/baselines/main.json is a stub and is never compared",
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artefact, indent=2) + "\n", encoding="utf-8")
    s4_fraction = artefact['s4_two_level_copy']['fraction_of_the_4090_us_uncontended_floor_percent']
    s5_fraction = artefact['s5_request_id_draw']['fraction_of_the_4090_us_uncontended_floor_percent']
    print(f"WROTE {output}")
    print(f"S-4 deepcopy        {s4_before.best_us} us")
    print(f"S-4 two_level_copy  {s4_after.best_us} us")
    print(f"S-4 numerator       {s4_saving} us ({s4_fraction} % of the floor)")
    print(f"S-5 three draws     {s5_before.best_us} us")
    print(f"S-5 one draw        {s5_after.best_us} us")
    print(f"S-5 numerator       {s5_saving} us ({s5_fraction} % of the floor)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
