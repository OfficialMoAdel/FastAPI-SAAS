"""P-22: a DISCRIMINATING A/B for the claimed `/users/me` regression.

    2026-09-22 -> 2026-10-02    p50 8 -> 16 ms (2.1x)    p95 19 -> 740 ms (39x)

What this is, and what it deliberately is not
---------------------------------------------
It is NOT a reproduction of either stored baseline. The plan disqualifies both on
their own terms (relaxed budgets, co-located generator, no warm-up exclusion,
auto-ban contamination) and states verbatim "There is no valid
production-representative baseline". Comparing this run's numbers to 8/16/19/740
would be quoting a number from a shape the programme has already rejected, so it
is not done anywhere in this file. The only figure this harness reports is the
DELTA between two arms measured under one instrument.

The discriminator: TWO VIEWS OF THE SAME REQUEST
------------------------------------------------
The 39x is a TAIL claim, and the plan's own mechanism hypothesis (line 75) is
event-loop lag. A server-side timer measures only time inside the app; the load
generator's clock measures the socket, the generator, and the host. So:

  * client-side p95 ratio large, server-side p95 ratio small
        -> the tail is client/generator-side. The regression is NOT in the app.
  * both large
        -> the server really did get slower.
  * neither large
        -> NULL RESULT, which the plan calls valid and valuable.

GATE A(4) asks for the load generator on a SEPARATE host. That is UNSATISFIABLE
in this environment (perf-app and perf-load are both TCP-unreachable; only one
host exists) and it is recorded in the artefact rather than papered over. The
two-view split is what makes the missing host survivable: a co-located
generator's CPU starvation is visible as a growing client-minus-server gap, so
the confounder is measured rather than assumed away.

Why this harness imports NOTHING from either arm
-------------------------------------------------
Measured: neither commit contains loop_lag.py, request_timer.py or
percentiles.py -- the T-series postdates the whole A/B window. So the
server-side timer and the lag sampler live in the serving script, outside both
checkouts (see ab/instrument.py). GATE A forbids comparing across a change to
the measuring instrument; owning the instrument outside both trees is the only
way to GUARANTEE that, and it is why this file drives URLs rather than importing
an app. The canonical percentile code IS reused, imported from
src.benchmarks.percentiles, which has no src.app dependency.

Two fairness controls that are load-bearing, not hygiene
--------------------------------------------------------
1. FRESH USERNAMES PER ARM. The user cache is keyed by identity, not by code
   version, so without this arm B would read `UserAuthInfo` rows that arm A
   wrote -- a different shape, from a different code version. Fresh identities
   make both arms cold identically, by construction, and this is why the harness
   needs no access to the user cache.
2. NO RATE-LIMIT PURGE AROUND THE MEASURED REQUESTS, and the reason is arithmetic
   rather than convenience: at tier pro the budget is (1000, 3600) per user per
   path, and 10000 requests over 64 accounts is 156 per account, so neither arm
   can starve the other. GATE A(6) is then satisfied by ASSERTING zero 429/403
   from the observed status counts, which proves no limiter fired, rather than by
   trusting that a purge worked.
3. THE SEED LOGINS DO NEED A PURGE, and this is separate from (1) and (2). A login
   carries no access token, so it is keyed ANONYMOUS on the client IP and it
   draws on `DEFAULT_ANONYMOUS_LIMIT` (10, 3600) as well as
   `SECURITY_BUDGETS['auth']` (5, 60) -- measured on this host by pacing and
   dumping the limiter keys, not assumed. 64 accounts per arm is 128 logins
   against a 10-per-HOUR ceiling, which is ~12.8 hours of waiting; pacing cannot
   help and the budget is per hour, not per minute. So the canonical
   `clear_security_state()` runs between seed logins, the same mechanism
   `scripts/step4_campaign.py` already uses for exactly this. This does NOT
   reintroduce the cross-arm leak that (1) exists to prevent: that leak is in
   the USER CACHE (`user:*`), and `SECURITY_KEY_PATTERNS` deliberately does not
   match it. The two are orthogonal key families. An earlier version of this
     file claimed the harness "needs no Redis access at all"; that was true of the
     measured requests and false of the logins, and the login is what fails first.
4. THE TENANT IS A FIXTURE, AND IT IS RECORDED. `--tenant` names the tenant
   slug the requests address and the seeded accounts belong to; both arms MUST
   use the same one, because the question is whether one commit serves the
   request faster than the other, not whether two different tenants do. The
   value is recorded per arm in the artefact, so the environment a figure was
   taken in is part of the figure.

   Why it is a parameter rather than a constant: the tenant that existed when
   the earlier attempts ran (slug `system`) is no longer in the database. The
   environment changed underneath that evidence -- the `system` tenant is gone,
   the seeded accounts are gone, and the tier rows are now named `Free`/`Pro`
   where they were `free`/`pro` -- so a new run is NOT a reproduction of the
   earlier attempts and is not offered as one. It is a new measurement, in the
   environment that exists now, of the same question.


How the arms are driven: INTERLEAVED
----------------------------------
The arms are never in flight at the same time -- one host, 4 CPUs, one database
pool, and running them together would CREATE the confounder this design exists
to measure rather than create. But they are also not measured in blocks. A
block -- the whole of the before arm's repetitions, then the whole of the
after arm's -- makes every millisecond of host drift between the two blocks
indistinguishable from the difference between the two arms.

The failure is not hypothetical. A separate measurement session on this same
host recorded a monotonic inflation across the repetitions of a single
non-interleaved run (149 us -> 529 us), and that run had to be discarded as
host drift rather than published as a result.

So the arms ALTERNATE: repetition 1 of before, repetition 1 of after,
repetition 2 of before, and so on. Host drift now moves both arms together
instead of moving one arm relative to the other. Both arms are also PREPARED
(seed, log in, warm) before EITHER is measured, because an arm measured while
the other is still cold is measured under different conditions -- the same
confound as measuring in blocks, in a smaller window.

The report's key set is unchanged by this, so a new run stays comparable with
the preserved reports of the earlier attempts in
`perf-results/RESULT-20261015-vpbi/`.

How the loop-lag quadrant is folded in
--------------------------------------
The lag sampler, the server-side timer and the arm's JSON export all live in
the arm PROCESS (serve.py), which is outside both checkouts. That process
answers SIGTERM by writing its export and exiting -- which is LATER than this
harness writes its output, so an earlier version of this file read the export
after both arms had been driven and found nothing: both loop_lag quadrants were
null BY CONSTRUCTION, the file did not exist yet at read time, and the run
could not distinguish "lag was fine" from "lag was never measured".

The designed order is now: each arm writes its export on a fixed LIVE interval
AS WELL AS on SIGTERM, so the harness reads a snapshot of the arm as it ran,
without owning the arm's lifetime and without stopping anything. The snapshot is
read immediately after THAT arm's run, not after the second arm's, so each
quadrant covers its own arm's seeding, logins, warm-up and measured requests.
The wait for the file is bounded (ARM_EXPORT_WAIT_SECONDS): the first live tick
happens seconds after the arm boots, so a still-missing file after the wait is
a MISSED MEASUREMENT, recorded as null with the reason stated -- never as an
assumption that the loop was healthy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import asyncpg
import bcrypt
import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.benchmarks.percentiles import PERCENTILES, nearest_rank  # noqa: E402

#: From src/benchmarks/protocol.py's noise-floor note: 2x intra-arm, 7.1x
#: run-to-run. Declared here rather than imported because that module is not
#: guaranteed arm-agnostic, and a harness that silently substituted a different
#: bar would be the exact defect this programme keeps finding. Its own docstring
#: records the two source measurements (perf E1 1808ms vs 4059ms same arm; perf
#: C11 /users/me p50 7.36 vs 9.81ms) behind the run-to-run figure.
NOISE_FLOOR_INTRA_ARM: Final[float] = 2.0
NOISE_FLOOR_RUN_TO_RUN: Final[float] = 7.1

#: GATE A(8): p99 needs this many pooled samples.
P99_SAMPLE_FLOOR: Final[int] = 10_000
#: GATE A(9).
MIN_REPETITIONS: Final[int] = 5
#: The degrading concurrency (rrct's knee, arm A under 4.5).
DEFAULT_CONCURRENCY: Final[int] = 8
#: How long to wait for an arm's LIVE export to appear before recording the
#: loop-lag quadrant as a missed measurement. Generous on purpose: serve.py's
#: first tick lands seconds after the arm boots, and the arms have been seeding,
#: logging in, warming and measuring for minutes by then.
ARM_EXPORT_WAIT_SECONDS: Final[float] = 30.0

SERVER_MS_HEADER: Final[str] = "x-ab-server-ms"
PROBE_TENANT: Final[str] = "system"
SEED_PASSWORD_ENV_VAR: Final[str] = "AB_SEED_PASSWORD"
SEED_PROFILE_IMAGE_URL: Final[str] = "https://profileimageurl.com/i.png"
#: A desktop-Chrome UA. It trips the attack-detection prefilter, which is the
#: conservative branch, so the middleware work is on the request path in both
#: arms rather than short-circuited away.
PROBE_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _seed_password() -> str:
    """Read at CALL time, never as a module constant.

    A constant would freeze from os.environ before main() runs load_dotenv --
    the same trap that has bitten three times today.
    """
    value = os.environ.get(SEED_PASSWORD_ENV_VAR)
    if not value:
        raise RuntimeError(
            f"{SEED_PASSWORD_ENV_VAR} is not set. It is required because these "
            "accounts are minted into a live database, and a password baked "
            "into a committed script is a secret in a file that reads like a "
            "fixture. It is a throwaway for a local database, never a "
            "production secret."
        )
    return value


@dataclass
class Arm:
    """One arm of the A/B: a checkout, a port, and its own fresh identities."""

    name: str
    base_url: str
    worktree: str
    arm_json: str
    #: The tenant the arm's requests address, and the tenant its seeded
    #: accounts belong to. It is a FIXTURE choice, not a measurement knob: both
    #: arms must address the SAME tenant, because the question is whether one
    #: commit serves the request faster than the other, not whether two
    #: different tenants do. It is recorded in the artefact so the environment
    #: a figure was taken in is part of the figure.
    tenant: str = PROBE_TENANT
    accounts: list[str] = field(default_factory=list)
    #: One token PER ACCOUNT, not one token for the arm. Tier pro is
    #: (1000, 3600) PER USER PER PATH, so 10000 requests driven by a single
    #: token is 10000 against a 1000 budget and the limiter fires mid-run --
    #: which is a void run, not a slow one. Keyed by username so the
    #: round-robin is a real distribution rather than a comment claiming one.
    tokens: dict[str, str] = field(default_factory=dict)
    client_ms: list[float] = field(default_factory=list)
    server_ms: list[float] = field(default_factory=list)
    per_rep_client: list[list[float]] = field(default_factory=list)
    per_rep_server: list[list[float]] = field(default_factory=list)
    status_counts: dict[int, int] = field(default_factory=dict)
    unexpected: list[dict[str, Any]] = field(default_factory=list)
    #: Folded from the arm's LIVE export (serve.py writes on a fixed interval and
    #: on SIGTERM), read right after this arm's run rather than after the arm is
    #: stopped -- see the module docstring for why the old read order made the
    #: loop_lag quadrant null by construction.
    loop_lag: dict[str, Any] | None = None
    loop_lag_note: str | None = None
    src_app_from: str | None = None
    server_instrument: str | None = None

    def stats(self) -> dict[str, Any]:
        """The client-observed distribution. Named, not inlined at the call
        sites, so the delta and the per-arm summary cannot read different
        shapes of the same thing."""
        return _dist(self.client_ms)


def _headers(*, token: str = "", tenant: str = PROBE_TENANT) -> dict[str, str]:
    headers = {"User-Agent": PROBE_USER_AGENT, "X-Tenant-ID": tenant}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _username(arm: str, index: int) -> str:
    return f"ab{arm}{index:04d}"


async def _connect() -> Any:
    """Keyword args, never a DSN string.

    POSTGRES_PASSWORD is a SecretStr in the app's settings; interpolating it into
    a DSN is how the literal `**********` once reached a live connection.
    """
    connection = await asyncpg.connect(
        host=os.environ["POSTGRES_SERVER"],
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        database=os.environ["POSTGRES_DB"],
    )
    return connection


async def seed_accounts(*, arm: str, count: int, tier: str, tenant: str = PROBE_TENANT) -> list[str]:
    """Insert FRESH users, so neither arm inherits the other's cache.

    Every Python-side column default is set explicitly. The ORM supplies them at
    construction; a raw INSERT that omits one stores NULL, and the profile
    directory filters is_deleted=False, which silently excludes the row instead
    of failing.
    """
    import uuid as uuid_pkg

    password = _seed_password()
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()
    connection = await _connect()
    try:
        tenant_id = await connection.fetchval("SELECT id FROM tenants WHERE slug = $1", tenant)
        if tenant_id is None:
            raise RuntimeError(f"no tenant with slug {tenant!r} exists; cannot seed")
        # Case-insensitive, because the app resolves the tier the same way:
        # `rate_policy.py` lowercases the tier name before looking it up in
        # TIER_RATE_LIMITS, and `resolve_entitlement` lowercases it too. A
        # tier row named `Pro` and one named `pro` are the same tier to the
        # application, so they must be the same tier to the fixture. The rows
        # in this database are named `Free`/`Pro`.
        tier_id = await connection.fetchval(
            "SELECT id FROM tier WHERE lower(name) = lower($1)", tier
        )
        if tier_id is None:
            raise RuntimeError(
                f"tier {tier!r} does not exist. Both arms must run at the SAME "
                "declared tier or TIER_RATE_LIMITS is not held constant, and the "
                "comparison stops being about the 28 commits."
            )
        names: list[str] = []
        now = await connection.fetchval("SELECT now()")
        for index in range(count):
            username = _username(arm, index)
            email = f"{username}@abprobe.org"
            await connection.execute("DELETE FROM users WHERE username = $1", username)
            await connection.execute(
                """
                INSERT INTO users (
                    tenant_id, name, username, email, hashed_password, uuid,
                    tier_id, profile_image_url, is_active, is_email_verified,
                    is_superuser, is_deleted, token_version, created_at,
                    updated_at, deleted_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, true, true, false, false, 0,
                    $9, $9, NULL
                )
                """,
                tenant_id,
                f"AB {arm}",
                username,
                email,
                hashed,
                uuid_pkg.uuid4(),
                tier_id,
                SEED_PROFILE_IMAGE_URL,
                now,
            )
            names.append(username)
        return names
    finally:
        await connection.close()


async def login(arm: Arm) -> None:
    """Each arm mints its OWN token, through its OWN auth code.

    The A/B window rewrote the auth path substantially, so a token minted by one
    arm and replayed at the other would be a token from the wrong implementation
    -- the instrument changing between arms, which is the thing GATE A forbids.
    """
    from urllib.parse import quote_plus

    # Imported here, not at module scope, for the reason every harness in this
    # repo has learned the hard way: the settings singleton is built at first
    # import, and main() has not loaded the env file yet at import time.
    # `security_state` itself imports settings inside its functions, so importing
    # the MODULE is safe -- it is the `src.app.core.config` import at its top
    # that is not.
    from src.benchmarks.security_state import FLUSHDB_SETTLE_SECONDS, clear_security_state

    password = quote_plus(_seed_password())
    async with httpx.AsyncClient(base_url=arm.base_url, timeout=60.0) as client:
        for position, username in enumerate(arm.accounts):
            if position:
                # Purge the limiter families between seed logins. This is a SETUP
                # cost, not measured work, and 4.5's purge is per measured phase
                # -- so purging here cannot contaminate a phase. Without it the
                # 6th login in a minute answers 429 and 128 logins cannot fit
                # inside a 10-per-hour anonymous budget at all.
                await clear_security_state()
                await asyncio.sleep(FLUSHDB_SETTLE_SECONDS)
            response = await client.post(
                "/api/v1/account/login",
                content=f"username={username}&password={password}",
                headers={
                    **_headers(tenant=arm.tenant),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"{arm.name}: login for {username!r} answered "
                    f"{response.status_code}: {response.text[:300]}"
                )
            arm.tokens[username] = response.json()["access_token"]


async def _drive_once(arm: Arm, *, count: int, concurrency: int, collect: bool) -> None:
    """One repetition, recording client and server figures PAIRED per request."""
    semaphore = asyncio.Semaphore(concurrency)
    index = {"i": 0}

    async def one() -> None:
        # Round-robin across accounts: tier pro is per user per path, and a
        # single account would hit its own wall before the run finished.
        account = arm.accounts[index["i"] % len(arm.accounts)]
        index["i"] += 1
        token = arm.tokens[account]
        async with semaphore:
            async with httpx.AsyncClient(base_url=arm.base_url, timeout=120.0) as client:
                started = time.perf_counter()
                response = await client.get(
                    "/api/v1/users/me", headers=_headers(token=token, tenant=arm.tenant)
                )
                elapsed_ms = (time.perf_counter() - started) * 1000.0
        status = response.status_code
        arm.status_counts[status] = arm.status_counts.get(status, 0) + 1
        if status != 200 and len(arm.unexpected) < 5:
            arm.unexpected.append({"status": status, "body": response.text[:300]})
        if not collect:
            return
        arm.client_ms.append(elapsed_ms)
        # The server-side figure is the ONLY thing that cannot see the load
        # generator. Absent header means the instrument did not run, which is a
        # measurement failure and must not be silently recorded as 0.
        raw = response.headers.get(SERVER_MS_HEADER)
        if raw is None:
            raise RuntimeError(
                f"{arm.name}: response carried no {SERVER_MS_HEADER}. The server-side "
                "view is the discriminator this A/B exists to produce; a run without "
                "it would have one view and no way to say which one is lying."
            )
        arm.server_ms.append(float(raw))

    await asyncio.gather(*(one() for _ in range(count)))


async def measure_one_rep(arm: Arm, *, requests_per_rep: int, concurrency: int) -> None:
    """One repetition of one arm: drive it, and keep its samples as a block.

    The block is what GATE A(9) reads -- the repetition form is computed from
    the per-repetition lists -- so a repetition has to stay separable.
    """
    c0, s0 = len(arm.client_ms), len(arm.server_ms)
    await _drive_once(
        arm, count=requests_per_rep, concurrency=concurrency, collect=True
    )
    arm.per_rep_client.append(arm.client_ms[c0:])
    arm.per_rep_server.append(arm.server_ms[s0:])


async def measure_interleaved(
    arms: list[Arm], *, requests_per_rep: int, repetitions: int, concurrency: int
) -> None:
    """Drive the arms ALTERNATELY: one repetition each, in lockstep.

    The arms are never in flight at the same time -- one host, one database
    pool, and running them together would CREATE the confounder this design
    exists to measure rather than create. But they are also not measured in
    blocks: a block makes every millisecond of host drift between the two
    blocks indistinguishable from the difference between the two arms.

    The failure is not hypothetical. A separate measurement session on this
    same host recorded a monotonic inflation across the repetitions of a
    single NON-interleaved run (149 us -> 529 us), and that run had to be
    discarded as host drift rather than published as a result. Interleaving
    is the fix: host drift now moves both arms together instead of moving
    one arm relative to the other.

    Alternating, not overlapping: repetition 1 of before, repetition 1 of
    after, repetition 2 of before, and so on.
    """
    for repetition in range(repetitions):
        for arm in arms:
            await measure_one_rep(
                arm, requests_per_rep=requests_per_rep, concurrency=concurrency
            )
            print(
                f"AB {arm.name}: repetition {repetition + 1}/{repetitions} done "
                f"(pooled_n={len(arm.client_ms)})",
                flush=True,
            )


def _dist(samples: list[float]) -> dict[str, Any]:
    if not samples:
        return {"count": 0}
    out: dict[str, Any] = {"count": len(samples), "min": round(min(samples), 4)}
    for q in PERCENTILES:
        out[f"p{int(q * 100)}"] = round(nearest_rank(samples, q), 4)
    out["max"] = round(max(samples), 4)
    out["mean"] = round(statistics.fmean(samples), 4)
    return out


def _repetition_form(per_rep: list[list[float]]) -> dict[str, Any]:
    """GATE A(9): >=5 reps, median-of-medians AND min-of-medians.

    BOTH statistics and BOTH figures: the plan asks for the min pair and the
    p50 pair, and a producer that flattened the batches would still report a p50
    and still pass. `medians` is not decoration -- it is the other half of the
    form, and dropping it would leave the gate able to pass on half the evidence.
    """
    if not per_rep:
        return {}
    mins = [min(batch) for batch in per_rep]
    medians = [statistics.median(batch) for batch in per_rep]
    return {
        "repetitions": len(per_rep),
        "meets_minimum_repetitions": len(per_rep) >= MIN_REPETITIONS,
        "min_median_of_repetitions": round(statistics.median(mins), 4),
        "min_min_of_repetitions": round(min(mins), 4),
        "p50_median_of_repetitions": round(statistics.median(medians), 4),
        "p50_min_of_repetitions": round(min(medians), 4),
    }


def _ratio(after: dict[str, Any], before: dict[str, Any], key: str) -> float | None:
    """after[key] / before[key], or None if either side cannot carry it.

    The zero-denominator guard is not defensive padding. A ratio against a
    near-zero denominator produced a 6779.58 "share" earlier in this programme
    that looked like a finding and was pure arithmetic artefact, and it survived
    because nothing refused to divide. A missing figure must be None, never a
    number.
    """
    b = before.get(key)
    a = after.get(key)
    if not isinstance(b, int | float) or not isinstance(a, int | float):
        return None
    if b == 0:
        return None
    return round(a / b, 4)


def _classify(client_ratio: float | None, server_ratio: float | None) -> dict[str, Any]:
    """Symmetric by construction: the gate is not 'the regression is confirmed'.

    A null result is a result. The plan says so, and a gate that could only be
    passed by finding the regression would be able to distinguish nothing.
    """
    def exceeds(value: float | None) -> bool:
        return value is not None and value >= NOISE_FLOOR_INTRA_ARM

    client_big, server_big = exceeds(client_ratio), exceeds(server_ratio)
    if client_big and server_big:
        verdict = "REGRESSION IS REAL AND SERVER-ATTRIBUTABLE"
        detail = (
            "Both views exceed the intra-arm noise floor, so the tail inflation is "
            "in the server, not in the measurement path."
        )
    elif client_big and not server_big:
        verdict = "REGRESSION CLAIM REFUTED AS STATED: the tail is client-side"
        detail = (
            "The client view exceeds the noise floor while the server view does "
            "not. Time inside the app did not grow, so the inflation is in the "
            "socket, the load generator, or the host -- not in the 28 commits."
        )
    elif server_big and not client_big:
        verdict = "SERVER SLOWED WITHOUT THE CLIENT SEEING IT"
        detail = (
            "The server view exceeds the noise floor while the client view does "
            "not. Unexpected in the claimed direction; report it rather than "
            "explain it."
        )
    else:
        verdict = "NULL RESULT: no delta exceeds the noise floor"
        detail = (
            "Neither view reaches the intra-arm floor. The claimed 39x does not "
            "reproduce, and the plan treats a null as a valid and valuable result."
        )
    return {
        "verdict": verdict,
        "detail": detail,
        "noise_floor_intra_arm": NOISE_FLOOR_INTRA_ARM,
        "noise_floor_run_to_run": NOISE_FLOOR_RUN_TO_RUN,
        "client_ratio_exceeds_floor": client_big,
        "server_ratio_exceeds_floor": server_big,
    }


async def prepare_arm(arm: Arm, args: argparse.Namespace) -> None:
    """Get one arm ready to be measured: fresh identities, a token, a warm cache.

    Preparation is SEPARATE from measurement so that both arms can be prepared
    before either is measured. An arm measured while the other arm is still
    cold is measured under different conditions, which is the same confound as
    measuring the arms in blocks, in a smaller window.
    """
    arm.accounts = await seed_accounts(
        arm=arm.name, count=args.users, tier=args.tier, tenant=arm.tenant
    )
    print(f"AB {arm.name}: seeded {len(arm.accounts)} fresh accounts at tier {args.tier}", flush=True)
    await login(arm)
    print(f"AB {arm.name}: minted its own token", flush=True)
    # Warm both views before timing, identically per arm: the claim is about a
    # warm saturated system, and the ORIGINAL baselines were disqualified partly
    # for having no warm-up exclusion.
    await _drive_once(arm, count=args.warmup, concurrency=args.concurrency, collect=False)
    print(f"AB {arm.name}: warmed {args.warmup} requests (not collected)", flush=True)


def build_artefact(arms: list[Arm], args: argparse.Namespace) -> dict[str, Any]:
    by_name = {arm.name: arm for arm in arms}
    before, after = by_name["before"], by_name["after"]

    # GATE A(6): ZERO rate-limit/auto-ban events. Asserted from what was
    # observed, not assumed because a purge ran.
    for arm in arms:
        offenders = {k: v for k, v in arm.status_counts.items() if k != 200}
        if offenders:
            raise RuntimeError(
                f"arm {arm.name} is VOID: non-200 statuses {offenders} with bodies "
                f"{arm.unexpected}. GATE A(6) requires zero rate-limit/auto-ban "
                "events, and a figure taken through a limiter is not a figure "
                "about the 28 commits."
            )

    # Two SEPARATE distributions, read from two SEPARATE sample lists. Computing
    # the "server ratio" from the client list would be the single most
    # consequential mistake available here: it would make the discriminator
    # agree with the client by construction and the verdict unfalsifiable.
    before_client, after_client = before.stats(), after.stats()
    before_server, after_server = _dist(before.server_ms), _dist(after.server_ms)
    client_ratio = _ratio(after_client, before_client, "p95")
    server_ratio = _ratio(after_server, before_server, "p95")
    return {
        "artefact": "ab_users_me",
        "generated_by": "scripts/ab_users_me.py",
        "question": (
            "Did GET /users/me really regress 8->16 ms p50 and 19->740 ms p95 "
            "across 4cd00654..7de0ff2a? Judged on the DELTA between two arms "
            "under one instrument, NOT against the stored CSVs, which the plan "
            "disqualifies."
        ),
        "arms": {
            arm.name: {
                "base_url": arm.base_url,
                "worktree": arm.worktree,
                "fresh_accounts": len(arm.accounts),
                "tier": args.tier,
                "tenant": arm.tenant,
                "client_observed_ms": arm.stats(),
                "server_side_ms": _dist(arm.server_ms),
                "repetition_form_client": _repetition_form(arm.per_rep_client),
                "repetition_form_server": _repetition_form(arm.per_rep_server),
                "status_counts": arm.status_counts,
                "unexpected": arm.unexpected,
                "pooled_n": len(arm.client_ms),
                "loop_lag": arm.loop_lag,
                "loop_lag_note": arm.loop_lag_note,
                "src_app_from": arm.src_app_from,
                "server_instrument": arm.server_instrument,
            }
            for arm in arms
        },
        "delta": _classify(client_ratio, server_ratio),
        "client_p95_ratio": client_ratio,
        "server_p95_ratio": server_ratio,
        "gate_a_compliance": {
            "3_loop_lag_histogram": "read from each arm's instrument export; see loop_lag",
            "4_separate_load_host": {
                "satisfied": False,
                "deviation": (
                    "UNSATISFIABLE in this environment. perf-app and perf-load are "
                    "both TCP-unreachable and only one host exists. Recorded here "
                    "in the vocabulary GATE A(5) provides for exactly this case. "
                    "What IS delivered is the substance of (4): server-side AND "
                    "client-observed both reported per arm, so generator starvation "
                    "shows up as a client-minus-server gap instead of as a fake "
                    "regression."
                ),
            },
            "5_production_budgets": (
                "No control was relaxed. At tier pro the budget is (1000, 3600) per "
                "user per path and this run issues ~156 per account, so no arm can "
                "be starved by the other; GATE A(6) is then satisfied by asserting "
                "zero non-200 rather than by trusting a purge."
            ),
            "6_zero_rate_limit_events": "asserted from observed status counts",
            "8_p99_needs_10000": P99_SAMPLE_FLOOR,
            "9_five_repetitions": MIN_REPETITIONS,
        },
        "instrument": {
            "server_side": "harness-owned TimingMiddleware, outside both checkouts",
            "why": (
                "Measured: neither arm contains request_timer.py, so no in-app "
                "timer can be imported. Owning the instrument outside both trees "
                "is what guarantees neither arm carries a different one."
            ),
            "percentiles": "src.benchmarks.percentiles.nearest_rank (the canonical)",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True)
    parser.add_argument("--before-url", required=True)
    parser.add_argument("--after-url", required=True)
    parser.add_argument("--before-worktree", required=True)
    parser.add_argument("--after-worktree", required=True)
    parser.add_argument("--before-arm-json", required=True)
    parser.add_argument("--after-arm-json", required=True)
    parser.add_argument("--tier", default="pro")
    parser.add_argument(
        "--tenant",
        default=PROBE_TENANT,
        help=(
            "The tenant slug the requests address and the seeded accounts "
            "belong to. Both arms MUST use the same one: the question is "
            "whether one commit serves the request faster than the other, "
            "not whether two different tenants do."
        ),
    )
    parser.add_argument("--users", type=int, default=64)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--repetitions", type=int, default=MIN_REPETITIONS)
    parser.add_argument("--requests-per-rep", type=int, default=2000)
    parser.add_argument("--warmup", type=int, default=200)
    parser.add_argument("--out", required=True)
    return parser


async def _read_arm_export(arm: Arm) -> dict[str, Any]:
    """Read the arm's LIVE export, waiting a bounded time for it.

    serve.py writes the export on a fixed interval AND on SIGTERM, so a snapshot
    exists while the arm is still running; the file is renamed into place, so a
    reader sees either the previous snapshot or this one, never a partial JSON.
    The wait exists for the arm's FIRST tick only, which lands seconds after
    boot. Returning {} after the deadline is how a missed measurement is
    distinguished from a measured one -- the quadrant is null and the caller
    states why, rather than the lag being assumed healthy.
    """
    deadline = time.monotonic() + ARM_EXPORT_WAIT_SECONDS
    path = Path(arm.arm_json)
    while True:
        if path.exists():
            try:
                loaded = json.loads(path.read_text())
            except json.JSONDecodeError:
                loaded = None
            if isinstance(loaded, dict):
                return loaded
        if time.monotonic() >= deadline:
            return {}
        await asyncio.sleep(0.5)


def _fold_arm_export(arm: Arm, export: dict[str, Any]) -> None:
    """Record the arm's export snapshot as the arm's loop-lag quadrant."""
    server_side = export.get("server_side")
    arm.loop_lag = export.get("loop_lag")
    arm.src_app_from = export.get("src_app_from")
    arm.server_instrument = server_side.get("instrument") if isinstance(server_side, dict) else None
    if arm.loop_lag is None:
        arm.loop_lag_note = (
            f"{arm.arm_json} carried no live export after waiting "
            f"{ARM_EXPORT_WAIT_SECONDS:g}s. The designed order is that each arm "
            "writes its export on a fixed interval AND on SIGTERM, so the snapshot "
            "is read while the arm runs; no snapshot therefore means the lag "
            "histogram was NOT measured this run, which is stated rather than "
            "assumed fine."
        )


async def amain(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv(args.env, override=True)
    before = Arm("before", args.before_url, args.before_worktree, args.before_arm_json, tenant=args.tenant)
    after = Arm("after", args.after_url, args.after_worktree, args.after_arm_json, tenant=args.tenant)
    # INTERLEAVED, never concurrent. The two arms are never in flight at the
    # same time: one host, 4 CPUs, one database pool, and running the arms
    # together would have them fight for all three, which is the confounder
    # this whole design exists to measure rather than create. But they are
    # also not measured in blocks: a block makes host drift between the two
    # blocks indistinguishable from the difference between the two arms.
    # The arms alternate -- one repetition each, in lockstep.
    #
    # Both arms are PREPARED first, so neither is measured while the other is
    # still cold.
    for arm in (before, after):
        await prepare_arm(arm, args)
    await measure_interleaved(
        [before, after],
        requests_per_rep=args.requests_per_rep,
        repetitions=args.repetitions,
        concurrency=args.concurrency,
    )
    for arm in (before, after):
        print(
            f"AB {arm.name}: measured pooled_n={len(arm.client_ms)} "
            f"reps={len(arm.per_rep_client)} statuses={arm.status_counts}",
            flush=True,
        )
        # The export is folded after the measurement, per arm. The old order
        # read the file after BOTH arms, which was already after the harness
        # had written its output -- and the arm file was written on SIGTERM
        # after that, so the loop-lag quadrant was null by construction. The
        # serving script now also writes the export LIVE every 5 s, so the
        # file is readable without a SIGTERM at all.
        export = await _read_arm_export(arm)
        _fold_arm_export(arm, export)
        print(
            f"AB {arm.name}: export snapshot loop_lag="
            f"{'present' if arm.loop_lag is not None else 'MISSING'} "
            f"src_app_from={arm.src_app_from}",
            flush=True,
        )
    return build_artefact([before, after], args)


def main() -> None:
    args = build_parser().parse_args()
    artefact = asyncio.run(amain(args))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(artefact, indent=2, default=str))
    print(json.dumps(artefact["delta"], indent=2))
    print(
        f"DELTA client_p95={artefact['client_p95_ratio']} "
        f"server_p95={artefact['server_p95_ratio']}"
    )


if __name__ == "__main__":
    main()
