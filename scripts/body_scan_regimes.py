"""Body-scan cost in both regimes: prefilter MISS (EM-9) and prefilter HIT (EM-10).

These two beads must run together. EM-9 asks for the CHEAP regime (60 KB benign,
prefilter misses) and EM-10 for the EXPENSIVE one (40 KB benign-with-literal,
prefilter hits), each at 4.4 sizes, each asserting its prefilter property in the
artefact, each reporting per-KB cost and the STEP4-L8 scan cost, and EM-10
additionally the RATIO between them WITH ITS SPREAD. All on the same commit, same
host, same window - which is why this is one probe rather than two.

WHY THE RATIO IS THE FINDING
----------------------------
The two bodies differ in SIZE but not in intent - both are benign, neither is an
attack the endpoint should refuse. The only difference is whether a literal the
Aho-Corasick prefilter (P1-7) can find happens to be present. So:

    40 KB + literal  -> prefilter hits  -> FULL REGEX runs
    60 KB + no literal -> prefilter misses -> regex SKIPPED

Reporting either alone describes "the body scan costs X". Reporting the ratio
describes the REGIME BOUNDARY, which is the thing an engineer actually needs: the
same endpoint costs ~24x more on a body that contains the substring "select" than
on a larger body that does not. A single average would sit between two regimes and
mean neither.

THE ATTACK LITERAL IS BENIGN BY CONSTRUCTION
--------------------------------------------
The hit-regime body carries an SQL-shaped string inside otherwise ordinary text
so the prefilter admits it. Nothing in it is a real attack, and the endpoint is
expected to SERVE the request - the property asserted in the artefact is the
PREFILTER'S, not a detection. A run that conflated "prefilter hit" with "attack
detected" would report a 400 where the endpoint correctly serves a 200.

WHAT EM-9 AND EM-10 ACTUALLY ASKED FOR
--------------------------------------
Both beads name a POST carrying a body. The scan-cost arm below answers the
SCAN question directly and it is the figure to quote for the scan itself, but it
never leaves the process: it calls ``_check_for_patterns`` on a bytes object, so
it measures no serialisation, no database write and no response. That is why
``--posts`` exists. The round-trip arm drives real
``POST /api/v1/posts/by-user/{username}`` requests through the whole stack and is
the artefact EM-9/EM-10 asked for; it runs in the SAME window, on the SAME host,
at the SAME commit, so the two arms stay comparable.

The round-trip is NOT a substitute for the scan arm and the scan arm is not a
substitute for the round-trip. A round-trip figure bundles the scan with the
serialisation of a 60 KB body, the insert and the re-serialisation of the
response; a scan figure bundles none of them. Quote both, labelled.

The RATIO is reported on both arms, and the two ratios are NOT expected to
agree -- measuring them showed they cannot, and that is the result rather than
a defect. An additive constant leaves a DIFFERENCE invariant; it does not
leave a ratio invariant, because (a + c) / (b + c) equals a / b only when
c is zero. Once the non-scan work is two orders of magnitude larger than the
scan, the round-trip ratio collapses toward 1 and the scan ratio is the only
one still measuring the thing under test. So the round-trip establishes that
the bodies are accepted end to end and prices the endpoint; the SCAN ratio is
where the prefilter difference lives, and quoting the round-trip ratio as the
finding would hide a ~14x effect behind a ~2.5x one.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import statistics
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
import os
import sys

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("ENVIRONMENT", "local")

# Imported rather than re-declared: two copies of the p99 floor is exactly the
# drift that produced a probe measuring a different limiter than it claimed.
# ``scripts.step4_campaign`` holds NO src.app import at module scope (the
# settings singleton is built at first src.app import, before load_dotenv can
# run), so this import is safe at module scope. ``seed_accounts`` reads settings
# INSIDE the function, at call time, after the env file is loaded.
from scripts.step4_campaign import P99_SAMPLE_FLOOR, PROBE_TENANT, seed_accounts
from src.benchmarks.percentiles import (
    PERCENTILES,
    nearest_rank,
    unresolvable_quantiles,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: Fixed scan-cost ceiling, chosen BEFORE measurement. Absolute, never relaxed.
SCAN_CEILING_US = 5_000.0

#: The route EM-9/EM-10 name: an authenticated POST that carries the body into
#: the attack-detection middleware and then writes it. ``_require_post_owner``
#: (posts/router.py:30-33) means each account must POST to its OWN username, so
#: the request set is spread across accounts, never aimed at one identity.
POSTS_PATH = "/api/v1/posts/by-user/{username}"

#: What the round-trip is allowed to spend OUTSIDE the body scan. The scan is
#: already gated by SCAN_CEILING_US; this allowance covers JSON serialisation of
#: the envelope, the insert and the re-serialisation of the response, which
#: carries the full text back out (PostRead embeds ``text``). It is anchored on
#: EM-1's own serial figure for the cheapest authenticated DB round-trip in the
#: programme's record - /users/me at 2.80 ms - so the total below is not a
#: number fitted to a run.
NON_SCAN_ALLOWANCE_US = 2_500.0

#: scan + everything the round-trip adds. Derived in the open so a reader can
#: take it apart, and declared before any POST is issued.
ROUNDTRIP_CEILING_US = SCAN_CEILING_US + NON_SCAN_ALLOWANCE_US

#: perf_counter() returns SECONDS. The protocol bug that made every ceiling in
#: this programme pass vacuously (bead FastAPI_saas-1py8) was a missing
#: conversion at exactly this point, so the factor is named and used once.
US_PER_SECOND = 1_000_000.0

#: Microseconds per millisecond. The ceiling is declared in microseconds and the
#: per-repetition figures are reported in milliseconds, so something has to
#: cross between the two. Named so the conversion is written ONCE and its
#: INVERSE cannot be typed at a call site by accident -- which is exactly how
#: this function came to publish 192780.51 "ms" for a 192 ms measurement.
US_PER_MILLISECOND = 1_000.0

#: Bounded capture of non-expected statuses. A 403 among 800 POSTs is a finding;
#: 800 captured bodies is an unreadable artefact.
UNEXPECTED_BODY_LIMIT = 3

MISS_BODY_KB = 60
HIT_BODY_KB = 40

#: Title is 2-30 chars (PostBase). Fixed so only the BODY varies between arms.
POST_TITLE = "step4 body scan"

#: An ordinary-looking sentence carrying an SQL-shaped substring. Benign: the
#: endpoint should serve it. The point is that the PREFILTER admits it.
_LITERAL_SENTENCE = (
    b"Please summarise the quarterly 'select all rows' report for the accounts "
    b"team before Friday. The dashboard shows a union of active subscriptions."
)


def best_of(fn: Any, *, repeats: int) -> tuple[float, list[float]]:
    gc.collect()
    samples: list[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1e6)
    return min(samples), samples


#: Filler for the MISS body, chosen because it contains NONE of the prefilter's
#: literals. The obvious choice - lorem ipsum - does NOT: the literal set includes
#: b"or" (TWO bytes), which appears inside "dolor" and "consectetur". A first
#: attempt used lorem ipsum for the 60 KB "cheap" body, the prefilter admitted it,
#: and both regimes ran the full regex - producing a per-KB ratio of 1.04 and the
#: false impression that the regime boundary does not exist.
#:
#: The literals avoided are: b"or", b";", b"<", b"&&", b"||", b"--". The assertion
#: in the artefact is the authority on whether this body is genuinely a miss; the
#: comment is only a note on why these words were chosen.
_MISS_FILLER = b"the quick wiggly daffy numbats puffy kiwis waltzing "


def build_bodies() -> tuple[bytes, bytes]:
    """The two bodies, size-accurate to the kilobyte the beads name."""
    miss_target = MISS_BODY_KB * 1024
    filler = _MISS_FILLER
    miss = (filler * (miss_target // len(filler) + 1))[:miss_target]

    hit_target = HIT_BODY_KB * 1024
    body = _LITERAL_SENTENCE
    body = body + b" " + filler * (hit_target // len(filler) + 2)
    hit = body[:hit_target]
    return miss, hit


def build_wire_bodies() -> tuple[bytes, bytes]:
    """The two bodies as they actually cross the wire, inside the JSON envelope.

    The attack-detection middleware scans the REQUEST BYTES, not the model field,
    so the prefilter property that matters is the envelope's. Verified before use:
    the envelope is 41 bytes larger than the filler and both properties hold on
    it unchanged - and both still validate against PostCreate.
    """
    miss, hit = build_bodies()
    return (
        json.dumps({"title": POST_TITLE, "text": miss.decode()}).encode(),
        json.dumps({"title": POST_TITLE, "text": hit.decode()}).encode(),
    )


def _both_ratios(scan_cost: dict[str, Any], roundtrip: dict[str, Any]) -> dict[str, Any]:
    """Hit-over-miss for both arms, with the dilution between them stated.

    The two ratios are deliberately NOT reconciled, and this is the function
    that says so. An additive constant leaves a DIFFERENCE invariant but not a
    RATIO: ``(a + c) / (b + c)`` equals ``a / b`` only when ``c`` is zero. So
    once the non-scan work (serialisation, insert, response re-serialisation,
    all at concurrency 8) is two orders of magnitude above the scan, the
    round-trip ratio collapses toward 1 and stops carrying the prefilter
    difference at all.

    Reporting both makes that dilution a number in the artefact rather than a
    surprise for whoever reads one arm and skips the other. The earlier version
    of this file asserted the opposite -- that a ratio is invariant under an
    additive constant -- which is false, and had it stood it would have made
    the ~2.5x round-trip ratio look like the headline and buried the ~14x scan
    ratio that is the actual measurement.

    Returns ``None`` for whichever ratio its arm did not produce, so a scan-only
    run reports a real scan ratio and no invented round-trip one.
    """
    def ratio(hit_us: Any, miss_us: Any) -> float | None:
        if not isinstance(hit_us, int | float) or not isinstance(miss_us, int | float):
            return None
        return round(hit_us / miss_us, 2) if miss_us else None

    scan_ratio = ratio(scan_cost.get("hit_40kb", {}).get("best_us"), scan_cost.get("miss_60kb", {}).get("best_us"))
    rt_ratio = ratio(
        roundtrip.get("em10_hit_40kb", {}).get("best_us"),
        roundtrip.get("em9_miss_60kb", {}).get("best_us"),
    )
    out: dict[str, Any] = {
        "scan_ratio_hit_over_miss": scan_ratio,
        "roundtrip_ratio_hit_over_miss": rt_ratio,
        "note": (
            "HIT over MISS on the each arm's own best_us. An additive constant "
            "leaves a DIFFERENCE invariant but not a RATIO, so the round-trip "
            "ratio is the scan ratio diluted by the non-scan work. The scan "
            "ratio is the one measuring the prefilter difference; the "
            "round-trip ratio is quoted so the dilution is a number here rather "
            "than a surprise for a reader who sees one arm and not the other."
        ),
    }
    if scan_ratio is not None and rt_ratio is not None:
        out["dilution_factor"] = round(rt_ratio / scan_ratio, 3) if scan_ratio else None
        out["dilution_note"] = (
            "roundtrip_ratio / scan_ratio, below 1 because the round-trip "
            "denominator carries the non-scan floor."
        )
    return out


def _summary_block(batches: list[list[float]], *, ceiling_us: float) -> dict[str, Any]:
    """The ADR-0022 shape: what resolved, what did not, stated as a number.

    Two DIFFERENT conditions live here and conflating them is how a probe ends
    up publishing a tail it never measured:

    * ``unresolvable_percentiles`` is `unresolvable_quantiles`'s own meaning - a
      quantile whose rank lands on the last sample. It is a property of the
      sample, and at n=800 neither p95 nor p99 trips it.
    * ``p99_reportable`` is 4.4's bar: pooled n >= P99_SAMPLE_FLOOR (10,000).

    A probe that published the p99 figure below that floor would be quoting a
    number from 800 requests as if it described the tail of 10,000. So the figure
    is WITHHELD here, with the reason beside it, and the tail claim for this
    endpoint rests on the RATIO between the two arms instead - which no sample
    size can move, because both arms are drawn from the same window.

    ``batches`` is one list per repetition, in MICROSECONDS, because that is
    the form ``drive`` collects and the form the ceiling is declared in. The
    per-repetition figures below are computed from the batches for 4.3's sake --
    median-of-medians and min-of-medians, not a single pooled median -- and
    every ``_ms`` key is converted on the way out. It was NOT before: this
    function used to publish microseconds under a ``reportable_ms`` name, so
    ``p50: 192780.51`` read as 192 seconds when the measurement was 192 ms. The
    same class of defect as protocol.py reporting seconds into fields named in
    microseconds: a unit that disagrees with its own key, found by checking
    rather than by looking.
    """
    samples = [value for batch in batches for value in batch]
    pooled_n = len(samples)
    unresolvable = unresolvable_quantiles(pooled_n, PERCENTILES)

    reportable: dict[str, Any] = {}
    for quantile in PERCENTILES:
        label = f"p{int(quantile * 100)}"
        if label in unresolvable or (quantile == 0.99 and pooled_n < P99_SAMPLE_FLOOR):
            reportable[label] = None
        else:
            reportable[label] = round(nearest_rank(samples, quantile) / US_PER_MILLISECOND, 3)

    batch_p50s = [statistics.median(batch) for batch in batches]
    batch_mins = [min(batch) for batch in batches]
    best = min(samples)
    return {
        "pooled_n": pooled_n,
        "repetitions": len(batches),
        # 4.3's four figures, on the same names PhaseResult.summary uses, so a
        # reader comparing the two artefact families is not comparing shapes.
        "p50_median_of_repetitions_ms": round(statistics.median(batch_p50s) / US_PER_MILLISECOND, 3),
        "p50_min_of_repetitions_ms": round(min(batch_p50s) / US_PER_MILLISECOND, 3),
        "min_median_of_repetitions_ms": round(statistics.median(batch_mins) / US_PER_MILLISECOND, 3),
        "min_min_of_repetitions_ms": round(min(batch_mins) / US_PER_MILLISECOND, 3),
        "best_us": round(best, 2),
        "median_us": round(statistics.median(samples), 2),
        "reportable_ms": reportable,
        "unresolvable_percentiles": sorted(unresolvable),
        "p99_reportable": pooled_n >= P99_SAMPLE_FLOOR,
        "p99_sample_floor": P99_SAMPLE_FLOOR,
        "p99_note": (
            f"ADR-0022 Rule 2. p99 requires pooled n >= {P99_SAMPLE_FLOOR}; this "
            f"run reaches {pooled_n}, so the p99 figure is published as null "
            "rather than computed from a sample too small to carry it. Rule 3 "
            "still applies: the endpoint IS measured and it DOES gate - this is "
            "a reporting rule, not an exemption. The boundary between the two "
            "regimes is read from the RATIO, which neither an unresolvable tail "
            "nor a sample size can move."
        ),
        "ceiling_us": ceiling_us,
        "under_ceiling": best < ceiling_us,
    }


async def _db_connect() -> Any:
    """One connection helper for every direct-DB need in this arm.

    ``settings.postgres.*`` and never the flat delegation, and an explicit
    unwrap because POSTGRES_PASSWORD is a SecretStr | None -- interpolating the
    DSN directly sends the literal '**********' and fails authentication. Keyword
    arguments, never a DSN string, so URL-encoding can never bite.
    """
    import asyncpg

    from src.app.core.config import settings

    postgres = settings.postgres
    password = postgres.POSTGRES_PASSWORD
    if password is None:
        raise RuntimeError("POSTGRES_PASSWORD is unset; this arm cannot reach the database")
    return await asyncpg.connect(
        host=postgres.POSTGRES_SERVER,
        port=postgres.POSTGRES_PORT,
        user=postgres.POSTGRES_USER,
        password=password.get_secret_value(),
        database=postgres.POSTGRES_DB,
    )


async def ensure_tier(tier: str) -> int:
    """Make sure the declared tier row exists, idempotently.

    ``scripts/step4_campaign.seed_accounts`` seeds the RBAC role but REQUIRES
    the tier to pre-exist -- it raises "tier ... does not exist; seed it first".
    After the schema rebuild that raised table is empty, so this arm would
    refuse to start on a database the campaign is otherwise perfectly happy
    with.

    ``tier`` does carry ``uq_tier_name``, so ``on conflict (name) do nothing
    returning id`` would also work. Select-then-insert is used instead because
    it returns the id in both the already-existed and the just-created case
    from one code path, and because a bare INSERT on a name-unique table would
    raise on the second run rather than being idempotent.
    """
    connection = await _db_connect()
    try:
        tier_id = await connection.fetchval("select id from tier where name = $1", tier)
        if tier_id is None:
            tier_id = await connection.fetchval(
                "insert into tier (name) values ($1) returning id", tier
            )
        return int(tier_id)
    finally:
        await connection.close()


async def post_roundtrip(
    *,
    env: str,
    accounts: int,
    tier: str,
    requests_per_repetition: int,
    repetitions: int,
    concurrency: int,
) -> dict[str, Any]:
    """EM-9/EM-10 as real POSTs: the whole stack, both regimes, one window.

    Two things this deliberately does NOT do. It does not mint a token (a minted
    sid has no session record, so the auth path answers "Session has been
    revoked" - measured, bead FastAPI_saas-nymp's campaign), and it does not keep
    its own login: ``seed_accounts`` is the campaign's own seeder, so the
    credential path is the one already measured end to end.

    Seeding is SETUP and is timed separately. Every figure below comes from after
    the last login, so the (5, 60) IP-keyed auth budget spent preparing the pool
    never contaminates a measurement.
    """
    # Redundant with the load in main(), and kept so this arm is correct when
    # called as a library rather than through the CLI. Harmless: the second load
    # of the same file with override=True produces the same environment.
    load_dotenv(env, override=True)

    import httpx

    from src.app.core.setup import close_redis_cache_pool, create_redis_cache_pool
    from src.app.main import app
    from src.benchmarks.security_state import clear_security_state

    # Before seed_accounts: it looks the tier up and refuses to start without it.
    tier_id = await ensure_tier(tier)

    seeded, rbac = await seed_accounts(accounts, tier, app)
    if not seeded:
        raise RuntimeError("post_roundtrip seeded no accounts; nothing to measure")

    # seed_accounts runs the app's lifespan and shuts it down on return, which
    # closes the Redis pool (composition.py's shutdown hook). Re-open it or the
    # measured phase talks to a blind app - and a purge that reports success
    # while the app has no client is the exact shape that cost a day earlier.
    await create_redis_cache_pool()

    try:
        # 4.5 wants a targeted purge per phase, not once per run: seeding spends
        # limiter budget from this host's single IP.
        cleared = await clear_security_state()
        await asyncio.sleep(2.0)

        miss_wire, hit_wire = build_wire_bodies()
        transport = httpx.ASGITransport(app=app)
        semaphore = asyncio.Semaphore(concurrency)

        async def drive(wire_body: bytes, label: str) -> dict[str, Any]:
            batches: list[list[float]] = []
            status_counts: dict[int, int] = {}
            unexpected: list[dict[str, Any]] = []

            async def one(account: Any) -> float:
                headers = {
                    "User-Agent": USER_AGENT,
                    "X-Tenant-ID": PROBE_TENANT,
                    "Authorization": f"Bearer {account.access_token}",
                    "Content-Type": "application/json",
                }
                started = time.perf_counter()
                # One client per request, closed in a finally: the campaign's
                # proven shape. Reusing one client across concurrent requests
                # would measure the connection, not the endpoint.
                client = httpx.AsyncClient(
                    transport=transport,
                    base_url="http://step4.invalid",
                    timeout=300.0,
                )
                try:
                    response = await client.post(
                        POSTS_PATH.format(username=account.username),
                        content=wire_body,
                        headers=headers,
                    )
                finally:
                    await client.aclose()
                elapsed_us = (time.perf_counter() - started) * US_PER_SECOND
                status_counts[response.status_code] = (
                    status_counts.get(response.status_code, 0) + 1
                )
                if response.status_code != 201 and len(unexpected) < UNEXPECTED_BODY_LIMIT:
                    unexpected.append(
                        {
                            "endpoint": label,
                            "account": account.username,
                            "status_code": response.status_code,
                            "body": response.text[:1200],
                        }
                    )
                return elapsed_us

            for _ in range(repetitions):
                batch: list[float] = []

                async def run_one(index: int) -> None:
                    async with semaphore:
                        # Round-robin across the pool. Tier pro is (1000, 3600)
                        # per USER per path, so 800 + 800 from a single account
                        # would hit the wall mid-phase and 429 the tail; 8
                        # accounts put 200 on each. The path token is constant
                        # (sanitize_path collapses the username to {}), so there
                        # is no per-username bucket to hide in.
                        batch.append(await one(seeded[index % len(seeded)]))

                await asyncio.gather(*[run_one(i) for i in range(requests_per_repetition)])
                batches.append(batch)

            return {
                "batches": batches,
                "status_counts": status_counts,
                "unexpected_bodies": unexpected,
            }

        # Both regimes in the same window, same host, same commit - the property
        # EM-9/EM-10 both require and the reason this is one probe.
        miss_run = await drive(miss_wire, "em9_miss_60kb")
        hit_run = await drive(hit_wire, "em10_hit_40kb")
    finally:
        await close_redis_cache_pool()

    connection = await _db_connect()
    try:
        # ``returning 1`` then count, NOT ``fetchval`` on the bare DELETE:
        # asyncpg returns NULL for a statement with no RETURNING clause, so
        # ``fetchval`` reported ``null`` every time and the artefact claimed a
        # count it never had.
        deleted_rows = await connection.fetch(
            "delete from post where created_by_user_id = any($1::int[]) returning 1",
            [a.user_id for a in seeded],
        )
        deleted = len(deleted_rows)
    finally:
        await connection.close()

    return {
        "measured": True,
        "route": f"POST {POSTS_PATH}",
        "route_why": (
            "The route the user approved for EM-9/EM-10. PostBase.text caps at "
            "63,206 chars so both the 60 KB and the 40 KB body fit. "
            "_require_post_owner (posts/router.py:30-33) means each account POSTs "
            "to its OWN username - no account can be used to write as another, "
            "which is also why the request set is spread rather than concentrated."
        ),
        "wire_sizes": {
            "em9_miss_60kb": len(miss_wire),
            "em10_hit_40kb": len(hit_wire),
            "note": (
                "The JSON envelope adds 41 bytes over the filler. The middleware "
                "scans the REQUEST BYTES, so every prefilter property and every "
                "per-KB figure below is against the WIRE size, not the text size."
            ),
        },
        "concurrency": concurrency,
        "repetitions": repetitions,
        "requests_per_repetition": requests_per_repetition,
        "accounts": accounts,
        "tier": tier,
        "tier_id": tier_id,
        "tier_note": (
            "A DECLARED CONFIGURATION, exactly like the RBAC role. The tier "
            "decides which TIER_RATE_LIMITS entry applies to these accounts: pro "
            "is (1000, 3600) per user per path, which is why 1000 requests per "
            "arm can be driven at all - the free tier's (10, 3600) would 429 "
            "the tail. The row is created if absent because "
            "step4_campaign.seed_accounts seeds the ROLE but requires the TIER "
            "to pre-exist."
        ),
        "security_state_cleared_before_phase": cleared,
        "ceiling_us": ROUNDTRIP_CEILING_US,
        "ceiling_derivation": {
            "scan_ceiling_us": SCAN_CEILING_US,
            "non_scan_allowance_us": NON_SCAN_ALLOWANCE_US,
            "formula": "SCAN_CEILING_US + NON_SCAN_ALLOWANCE_US",
            "note": (
                "Declared before any POST was issued. The scan half is the "
                "existing absolute ceiling; the allowance is anchored on EM-1's "
                "2.80 ms serial figure for the cheapest authenticated DB "
                "round-trip in the programme's record, so it is not fitted to "
                "this run. It is a coarse guard on 'did the non-scan part "
                "explode', not the finding. Measured, it did: both arms "
                "breached it, because the non-scan work at concurrency 8 is "
                "roughly two orders of magnitude above the 2.80 ms serial "
                "figure this allowance was anchored on. That breach is a "
                "correct report of the endpoint, not a bad ceiling - and it is "
                "why the ceiling cannot stand in for the scan measurement."
            ),
        },
        "seed_info": rbac,
        "em9_miss_60kb": {
            **_summary_block(miss_run["batches"], ceiling_us=ROUNDTRIP_CEILING_US),
            "status_counts": {str(k): v for k, v in miss_run["status_counts"].items()},
            "status_is_clean": set(miss_run["status_counts"]) == {201},
            "unexpected_statuses": sorted(
                set(miss_run["status_counts"]) - {201}
            ),
            "unexpected_bodies": miss_run["unexpected_bodies"],
        },
        "em10_hit_40kb": {
            **_summary_block(hit_run["batches"], ceiling_us=ROUNDTRIP_CEILING_US),
            "status_counts": {str(k): v for k, v in hit_run["status_counts"].items()},
            "status_is_clean": set(hit_run["status_counts"]) == {201},
            "unexpected_statuses": sorted(set(hit_run["status_counts"]) - {201}),
            "unexpected_bodies": hit_run["unexpected_bodies"],
        },
        "cleanup": {
            "post_rows_deleted": deleted,
            "note": (
                "The bodies are ~77 MB of rows at the default sizes. They are "
                "harness output, not application data, so they are removed at "
                "teardown and the count is recorded."
            ),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    """The CLI, as a function so a test can assert the defaults really are these.

    A check that re-declares the arguments drifts from the ones the operator
    actually gets, and then it passes while guarding nothing - the exact failure
    this programme has already paid for twice.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/body_scan_regimes.json")
    parser.add_argument("--repeats", type=int, default=800)
    # --- the round-trip arm (EM-9/EM-10 as real POSTs) ---------------------
    parser.add_argument(
        "--posts",
        action="store_true",
        help=(
            "Also drive POST /api/v1/posts/by-user/{username} with both bodies. "
            "Without it only the scan-cost arm runs and the artefact states the "
            "round-trip was not measured - a recorded gap, not a silent one."
        ),
    )
    parser.add_argument("--env", default="/home/ansible/.l8_env")
    parser.add_argument("--accounts", type=int, default=8)
    parser.add_argument("--tier", default="pro")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--posts-reps", type=int, default=5)
    parser.add_argument("--posts-iterations", type=int, default=200)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    # The env file must be loaded BEFORE the first src.app import anywhere in
    # this process, and the scan arm below imports src.app to reach the
    # middleware. The settings singleton is constructed at that FIRST import, so
    # a load that happens later changes nothing and every settings.postgres.*
    # read afterwards silently points at whatever os.environ happened to hold.
    # Measured, not theorised: with this load after the scan arm, the round-trip
    # arm's tier insert landed in a DIFFERENT database and failed on a column
    # default that exists in the intended one. load_dotenv returns False and
    # changes nothing when the file is absent, so the scan arm still runs with
    # no --env at all.
    load_dotenv(args.env, override=True)

    from src.app.middleware.attack_detection import (
        AttackDetectionMiddleware,
        _attack_prefilter_hit,
    )

    miss_body, hit_body = build_bodies()
    miss_wire, hit_wire = build_wire_bodies()
    mw = AttackDetectionMiddleware(app=None)
    attacks: list[dict] = []

    # --- ASSERT THE PREFILTER PROPERTIES BEFORE ANY TIMING ----------------
    # Twice: on the TEXT (which the scan arm calls) and on the WIRE bytes (which
    # the middleware actually receives). The two differ by the 41-byte JSON
    # envelope, so asserting only the text would leave the round-trip arm's
    # central property unverified.
    miss_hits = _attack_prefilter_hit(miss_body)
    hit_hits = _attack_prefilter_hit(hit_body)
    miss_wire_hits = _attack_prefilter_hit(miss_wire)
    hit_wire_hits = _attack_prefilter_hit(hit_wire)
    properties = {
        "miss_body": {
            "kb": MISS_BODY_KB,
            "bytes": len(miss_body),
            "prefilter_hit": miss_hits,
            "asserted_miss": miss_hits is False,
            "statement": (
                "60 KB of ordinary text carries no literal the Aho-Corasick "
                "prefilter can find, so the P1-7 skip FIRES and the full regex "
                "never runs. Asserted here, before any timing."
            ),
        },
        "hit_body": {
            "kb": HIT_BODY_KB,
            "bytes": len(hit_body),
            "prefilter_hit": hit_hits,
            "asserted_hit": hit_hits is True,
            "statement": (
                "40 KB of ordinary text containing a SQL-shaped substring. The "
                "prefilter ADMITS it, so the full regex RUNS. Asserted here, "
                "before any timing. The substring is benign and the endpoint is "
                "expected to serve the request - the property asserted is the "
                "PREFILTER'S, not a detection."
            ),
        },
        "wire_bytes": {
            "miss_bytes": len(miss_wire),
            "hit_bytes": len(hit_wire),
            "miss_prefilter_hit": miss_wire_hits,
            "hit_prefilter_hit": hit_wire_hits,
            "both_asserted": (miss_wire_hits is False) and (hit_wire_hits is True),
            "statement": (
                "The SAME two properties asserted on the bytes the request "
                "actually carries - the JSON envelope, 41 bytes larger than the "
                "filler. The middleware scans request bytes, so this is the "
                "assertion the round-trip arm depends on. It is re-asserted "
                "rather than assumed because a body that changes class between "
                "the text and the wire would silently measure the wrong regime."
            ),
        },
        "both_asserted": (miss_hits is False) and (hit_hits is True),
    }

    # --- STEP4-L8 SCAN COST, both regimes ---------------------------------
    def scan(payload: bytes) -> None:
        mw._check_for_patterns(payload, "body", attacks)  # noqa: SLF001

    best_miss, s_miss = best_of(lambda: scan(miss_body), repeats=args.repeats)
    best_hit, s_hit = best_of(lambda: scan(hit_body), repeats=args.repeats)

    scan_cost = {
        "ceiling_us": SCAN_CEILING_US,
        "ceiling_basis": "absolute, chosen BEFORE measurement, never relaxed",
        "miss_60kb": {
            "best_us": round(best_miss, 3),
            "median_us": round(statistics.median(s_miss), 3),
            "us_per_kb": round(best_miss / MISS_BODY_KB, 3),
            "under_ceiling": best_miss < SCAN_CEILING_US,
        },
        "hit_40kb": {
            "best_us": round(best_hit, 3),
            "median_us": round(statistics.median(s_hit), 3),
            "us_per_kb": round(best_hit / HIT_BODY_KB, 3),
            "under_ceiling": best_hit < SCAN_CEILING_US,
        },
        "ratio_best": round(best_hit / best_miss, 2) if best_miss else None,
        "ratio_note": (
            "HIT/MISS at DIFFERENT body sizes. The hit body is SMALLER, so this "
            "ratio UNDERSTATES the per-byte penalty: the hit regime costs more "
            "while scanning 33% fewer bytes. Per-KB figures are reported beside "
            "it so the per-byte story is not hidden by the size difference."
        ),
        "per_kb_ratio": (
            round((best_hit / HIT_BODY_KB) / (best_miss / MISS_BODY_KB), 2)
            if best_miss
            else None
        ),
        "per_kb_ratio_note": (
            "COST PER KILOBYTE, which removes the size confound entirely. This "
            "is the figure that describes the regime boundary; the raw ratio "
            "above is kept for completeness and is the more conservative one."
        ),
    }

    # Built outside the artefact literal and annotated: the two branches return
    # different dict shapes and mypy would otherwise widen the join to
    # ``dict[str, object]``, making every field below unindexable.
    post_roundtrip_result: dict[str, Any] = (
        asyncio.run(
            post_roundtrip(
                env=args.env,
                accounts=args.accounts,
                tier=args.tier,
                requests_per_repetition=args.posts_iterations,
                repetitions=args.posts_reps,
                concurrency=args.concurrency,
            )
        )
        if args.posts
        else {
            "measured": False,
            "reason": (
                "--posts was not passed, so only the scan-cost arm ran. "
                "EM-9 and EM-10 asked for a POST carrying the body; the "
                "scan arm answers the scan question but never leaves the "
                "process, so it is NOT the artefact those beads asked for. "
                "This gap is recorded rather than left silent."
            ),
            "route": f"POST {POSTS_PATH}",
        }
    )

    result = {
        "schema": "perf-body-scan-regimes/2",
        "run": {
            "same_commit_host_window_for_both_regimes": True,
            "why": (
                "EM-9 and EM-10 both require the same commit, host and window. "
                "Two separate runs would differ by exactly the run-to-run "
                "variance 4.4 records - 7.1x on this target - and the ratio "
                "between the regimes would be uninterpretable."
            ),
        },
        "prefilter_properties": properties,
        "scan_cost": scan_cost,
        "post_roundtrip": post_roundtrip_result,
        "ratios": _both_ratios(scan_cost, post_roundtrip_result),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"prefilter MISS asserted: {properties['miss_body']['asserted_miss']} "
          f"({len(miss_body)} bytes, hit={miss_hits})")
    print(f"prefilter HIT  asserted: {properties['hit_body']['asserted_hit']} "
          f"({len(hit_body)} bytes, hit={hit_hits})")
    print(f"prefilter WIRE asserted: {properties['wire_bytes']['both_asserted']} "
          f"({len(miss_wire)}/{len(hit_wire)} bytes)")
    print(f"scan MISS 60KB: {best_miss:>9.2f} us  ({best_miss/MISS_BODY_KB:.2f} us/KB)")
    print(f"scan HIT  40KB: {best_hit:>9.2f} us  ({best_hit/HIT_BODY_KB:.2f} us/KB)")
    print(f"ratio raw {scan_cost['ratio_best']}x | per-KB ratio "
          f"{scan_cost['per_kb_ratio']}x")
    if args.posts:
        rt = post_roundtrip_result
        for arm in ("em9_miss_60kb", "em10_hit_40kb"):
            block = rt[arm]
            print(
                f"POST {arm}: n={block['pooled_n']} best={block['best_us']}us "
                f"median={block['median_us']}us clean={block['status_is_clean']} "
                f"counts={block['status_counts']} unres={block['unresolvable_percentiles']}"
            )
        miss_best = rt["em9_miss_60kb"]["best_us"]
        hit_best = rt["em10_hit_40kb"]["best_us"]
        print(
            f"POST ratio raw {round(hit_best / miss_best, 2) if miss_best else None}x "
            f"| ceiling {rt['ceiling_us']}us (NOT additive-constant immune: the "
            f"scan ratio is the finding, this one is diluted -- see 'ratios')"
        )
        print(f"POST cleanup: deleted {rt['cleanup']['post_rows_deleted']} rows")
    else:
        print("POST round-trip: NOT MEASURED (--posts not passed)")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
