"""S-2: the cost of ONE transaction unwind on the duplicate logout path.

WHAT THIS IS

S-2 removed a redundant outer ROLLBACK from the duplicate-token branch of
``src/app/core/security/jwt_service.py::blacklist_tokens`` - the logout path.
The removed line unwound the CALLER's whole transaction to reach a no-op that
a repeat logout is by contract, so it cost a ROLLBACK round trip to Postgres.
The cost of that unwind was never measured anywhere in this programme; this
script measures it, on the Linux target, and records it as an artefact.

THE ARM - the duplicate path, reproduced in the database

The state the removed line ran in is reproduced exactly, not sketched:

  * a row for the probe token already exists (the table's own unique index on
    ``token`` is what makes the repeat a duplicate);
  * the staged insert is made inside a SAVEPOINT - ``begin_nested()`` - which
    is what ``blacklist_tokens`` does, and the flush raises the unique
    violation;
  * the savepoint rolls ITSELF back on the IntegrityError, so the session's
    outer transaction is open and holds NOTHING;
  * the unwind is then timed: ``await db.rollback()``.

That is the exact state, and the exact call, that the removed line made.

The exception is classified with the PRODUCTION predicate
(``_is_duplicate_blacklist_violation``), so this arm can only ever be the
duplicate branch - a measurement that reached some other branch would fail
rather than report a number for it.

TWO MORE ARMS, for scale

  * an unwind WITH real work to undo (a flushed insert), so the duplicate
    arm's cost can be read against what an unwind costs when it has
    something to undo;
  * an unwind with NOTHING to undo and no transaction open - the LOCAL floor
    of the call, which is what tells the reader whether the duplicate arm's
    figure is above or below what the instrument can resolve.

WHAT THIS IS NOT

  * NOT an endpoint measurement: no HTTP request is issued and the app's
    lifespan is not run;
  * NOT a before/after benchmark: no comparison of two arms' durations is
    made, and no number here is a claim about an improvement;
  * NOT the cost of a logout. It is the cost of ONE unwind.
"""

from __future__ import annotations

import asyncio
import json
import platform
import socket
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "perf-results" / "RESULT-20261020-gryp" / "s2_transaction_unwind_cost.json"

REPETITIONS = 20
PROBE_PREFIX = "gryp-unwind-probe-"

# The denominators this figure is quoted against, from the committed record.
UNCONTENDED_FLOOR_US = 4090.0
EM6_LOGOUT_P50_US = 12670.7


async def _probe_row(session: Any) -> tuple[str, Any] | None:
    """An existing (token, tenant_id) pair, or None when the table is empty.

    Using an EXISTING row is what makes the repeat a duplicate without
    writing anything: the unique index on ``token`` is the duplicate's
    cause, and no probe row has to be created at all.
    """
    result = await session.execute(text("select token, tenant_id from token_blacklist limit 1"))
    row = result.first()
    if row is None:
        return None
    return str(row[0]), row[1]


async def _tenant_id(session: Any) -> Any:
    result = await session.execute(text("select id from tenants limit 1"))
    row = result.first()
    if row is None:
        raise RuntimeError("no tenant row exists: the probe row cannot be created")
    return row[0]


async def _arm_duplicate_unwind(factory: Any, token: str, tenant_id: Any) -> dict[str, Any]:
    """One unwind on the duplicate path - the shape S-2 removed.

    The savepoint's own rollback is NOT timed; only the outer unwind is, and
    the savepoint's rollback happens before the clock starts.

    The insert is issued as raw SQL on purpose: the ORM model carries a
    foreign key to ``tenants``, and resolving that FK needs the whole model
    registry imported, which this script does not do and does not need.
    """
    from src.app.core.security.jwt_service import _is_duplicate_blacklist_violation

    session = factory()
    classified_as_duplicate = False
    integrity_error: str | None = None
    try:
        try:
            async with session.begin_nested():
                await session.execute(
                    text(
                        "insert into token_blacklist (token, expires_at, tenant_id) "
                        "values (:token, cast(:expires as timestamptz), cast(:tenant as uuid))"
                    ),
                    {
                        "token": token,
                        "expires": datetime.now(UTC) + timedelta(days=1),
                        "tenant": str(tenant_id),
                    },
                )
                await session.flush()
        except IntegrityError as exc:
            integrity_error = type(exc).__name__
            classified_as_duplicate = _is_duplicate_blacklist_violation(exc)
        started = time.perf_counter_ns()
        await session.rollback()
        elapsed_us = (time.perf_counter_ns() - started) / 1000.0
    finally:
        await session.close()
    return {
        "elapsed_us": elapsed_us,
        "classified_as_the_production_duplicate": classified_as_duplicate,
        "integrity_error": integrity_error,
    }


async def _arm_unwind_with_work(factory: Any, tenant_id: Any) -> dict[str, Any]:
    """One unwind that has a flushed insert to undo."""
    token = f"{PROBE_PREFIX}work-{time.time_ns()}"
    session = factory()
    try:
        await session.execute(
            text(
                "insert into token_blacklist (token, expires_at, tenant_id) "
                "values (:token, cast(:expires as timestamptz), cast(:tenant as uuid))"
            ),
            {
                "token": token,
                "expires": datetime.now(UTC) + timedelta(days=1),
                "tenant": str(tenant_id),
            },
        )
        await session.flush()
        started = time.perf_counter_ns()
        await session.rollback()
        elapsed_us = (time.perf_counter_ns() - started) / 1000.0
    finally:
        await session.close()
    return {"elapsed_us": elapsed_us}


async def _arm_local_floor(factory: Any) -> dict[str, Any]:
    """The local floor of the call: an unwind with no transaction open."""
    session = factory()
    try:
        started = time.perf_counter_ns()
        await session.rollback()
        elapsed_us = (time.perf_counter_ns() - started) / 1000.0
    finally:
        await session.close()
    return {"elapsed_us": elapsed_us}


def _stats(samples: list[float]) -> dict[str, Any]:
    ordered = sorted(samples)
    return {
        "min_us": round(ordered[0], 3),
        "median_us": round(ordered[len(ordered) // 2], 3),
        "max_us": round(ordered[-1], 3),
        "samples_us": [round(value, 3) for value in samples],
    }


async def _main_async(args: Any) -> int:
    from src.app.core.db.database import _get_local_session

    factory = _get_local_session()
    session = factory()
    try:
        probe = await _probe_row(session)
        if probe is None:
            tenant_id = await _tenant_id(session)
            token = f"{PROBE_PREFIX}seed-{time.time_ns()}"
            await session.execute(
                text(
                    "insert into token_blacklist (token, expires_at, tenant_id) "
                    "values (:token, cast(:expires as timestamptz), cast(:tenant as uuid))"
                ),
                {
                    "token": token,
                    "expires": datetime.now(UTC) + timedelta(days=1),
                    "tenant": str(tenant_id),
                },
            )
            await session.commit()
            probe = (token, tenant_id)
    finally:
        await session.close()
    token, tenant_id = probe

    duplicate: list[dict[str, Any]] = []
    with_work: list[dict[str, Any]] = []
    floor: list[dict[str, Any]] = []
    for _ in range(args.repetitions):
        duplicate.append(await _arm_duplicate_unwind(factory, token, tenant_id))
        with_work.append(await _arm_unwind_with_work(factory, tenant_id))
        floor.append(await _arm_local_floor(factory))

    duplicate_samples = [entry["elapsed_us"] for entry in duplicate]
    work_samples = [entry["elapsed_us"] for entry in with_work]
    floor_samples = [entry["elapsed_us"] for entry in floor]
    duplicate_stats = _stats(duplicate_samples)
    floor_stats = _stats(floor_samples)

    classified = all(entry["classified_as_the_production_duplicate"] for entry in duplicate)
    assert classified, "the duplicate arm must reach the production duplicate branch in every repetition"
    assert all(entry["integrity_error"] == "IntegrityError" for entry in duplicate), (
        "the duplicate arm must raise an IntegrityError"
    )

    numerator_us = duplicate_stats["min_us"]
    artefact: dict[str, Any] = {
        "schema": "perf-s2-transaction-unwind-cost/1",
        "bead": "FastAPI_saas-gryp",
        "item": "S-2",
        "title": "the cost of ONE transaction unwind on the duplicate logout path",
        "recorded_at": datetime.now(UTC).isoformat(),
        "method": (
            "The duplicate path is reproduced in the database: the staged insert is made inside a SAVEPOINT "
            "(begin_nested), the flush raises the unique violation, the savepoint rolls ITSELF back, and only "
            "then is the outer unwind timed. The exception is classified with the production predicate "
            "(_is_duplicate_blacklist_violation), so the arm can only be the duplicate branch. The savepoint's "
            "own rollback is NOT inside the timed region: the clock starts after it. No HTTP request is issued "
            "and the app's lifespan is not run."
        ),
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "in_repo": True,
            "harness": "src/benchmarks/gryp_transaction_unwind_cost.py",
        },
        "protocol": {
            "repetitions": args.repetitions,
            "statistic": "min-of-N with the median and the max reported beside it",
            "clock": "time.perf_counter_ns()",
            "criterion": "report only; this is not a new < old comparison and no improvement is claimed",
        },
        "arms": {
            "duplicate_path_unwind": {
                "what": (
                    "the outer unwind on the duplicate logout path - the exact call and the exact state the "
                    "redundant ROLLBACK S-2 removed ran in"
                ),
                **duplicate_stats,
                "classified_as_the_production_duplicate_in_every_repetition": classified,
            },
            "unwind_with_a_flushed_insert_to_undo": {
                "what": "one unwind that has real work to undo, for scale",
                **_stats(work_samples),
            },
            "local_floor": {
                "what": "one unwind with no transaction open - the local floor of the call",
                **floor_stats,
            },
        },
        "the_figure": {
            "numerator_us": numerator_us,
            "numerator_is": "the min-of-N cost of ONE transaction unwind on the duplicate logout path",
            "fraction_of_the_4090_us_uncontended_floor_percent": round(numerator_us / UNCONTENDED_FLOOR_US * 100, 4),
            "fraction_of_em6s_12670_7_us_logout_p50_percent": round(numerator_us / EM6_LOGOUT_P50_US * 100, 4),
            "denominators": {
                "uncontended_floor_us": UNCONTENDED_FLOOR_US,
                "uncontended_floor_source": "GATE B's named floor; the committed corroboration is "
                "perf-results/RESULT-20261012-sweepB/sweepA_8_stats.csv (min 4.09577600294142 ms at concurrency 8)",
                "em6_logout_p50_us": EM6_LOGOUT_P50_US,
                "em6_logout_p50_source": (
                    "EM-6 (FastAPI_saas-ky66), perf-results/RESULT-20261013-logout/logout_step.json"
                ),
            },
            "endpoint_the_saving_lands_on": (
                "POST /api/v1/account/logout, on the REPEAT (duplicate-token) path only. A normal logout never "
                "took this branch, so the saving lands on a repeat logout and on nothing else. It does not land "
                "on GET /users/me or on any other request path."
            ),
            "is_the_figure_above_the_local_floor": numerator_us > floor_stats["min_us"],
            "local_floor_min_us": floor_stats["min_us"],
        },
        "what_is_not_asserted": {
            "no_new_less_old": (
                "No comparison of a before arm against an after arm is made. There is no before arm here: the "
                "line S-2 removed is gone from the tree, so what this measures is the cost of the unwind as it "
                "would have been executed, not a measured saving."
            ),
            "no_endpoint_measurement": "no HTTP request is issued and the lifespan is not run",
            "not_the_cost_of_a_logout": "it is the cost of ONE unwind, on one branch",
            "the_denominator_is_not_re_measured": (
                "EM-6's logout p50 and the 4.09 ms floor are quoted from the committed record; neither is "
                "re-measured here, and the floor is not substituted with a warm p50"
            ),
            "main_json_stub_never_compared": "tests/benchmarks/baselines/main.json is a stub and is never compared",
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artefact, indent=2) + "\n", encoding="utf-8")
    print(f"WROTE {output}")
    print(f"duplicate-path unwind: min {numerator_us} us, median {duplicate_stats['median_us']} us")
    print(f"unwind with work:      min {_stats(work_samples)['min_us']} us")
    print(f"local floor:           min {floor_stats['min_us']} us")
    floor_fraction = artefact["the_figure"]["fraction_of_the_4090_us_uncontended_floor_percent"]
    p50_fraction = artefact["the_figure"]["fraction_of_em6s_12670_7_us_logout_p50_percent"]
    print(f"fraction of the 4090 us floor: {floor_fraction} %")
    print(f"fraction of EM-6's logout p50: {p50_fraction} %")
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--repetitions", type=int, default=REPETITIONS)
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
