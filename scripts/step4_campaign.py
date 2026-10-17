#!/usr/bin/env python
"""Step 4 endpoint campaign for bead FastAPI_saas-v8qm.

Ten endpoints, measured at the degrading concurrency, under PRODUCTION values for
all eight of the 4.5 rate/ban controls. Replaces the ten single-endpoint probe
scripts, which were serial and top out far below the protocol's n floor.

Three things separate this from the probes it replaces, and each exists because a
criterion of the Step 4 exit gate could not otherwise be met:

1. **Real concurrency.** Every probe ran ``for _ in range(n): await hit()``, so
   every published latency was uncontended. This drives a worker pool at
   ``--concurrency`` (default 8, the knee rrct identified for the arm-A /
   production-controls case).
2. **Pooled sampling.** 4.4's p99 floor is n>=10,000 on a single sample. The
   >=5 repetitions feed median-of-medians and min-of-medians for MIN and p50;
   the repetitions' latencies are then pooled so p99 has a real denominator.
   Per ADR-0022 (accepted, Option A) an endpoint that cannot reach the floor
   prints ``unresolvable_percentiles()`` and says why, rather than interpolating.
3. **The eight controls, asserted at run start AND run end.** 4.5 is explicit
   that a phase is void if ANY of the eight sat at a load-producing value, and
   that a stale re-read at the end is how a 48-minute run was voided. Both reads
   are recorded in the artefact and compared.

Usage::

    .venv/bin/python -m scripts.step4_campaign --env /path/env \\
        --tier pro --users 64 --concurrency 8 --repetitions 5 \\
        --requests-per-rep 2000 --out reports/step4.json
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import statistics
import sys
import time
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import httpx
from dotenv import load_dotenv

# NOTE: nothing from ``src.app`` is imported at module scope here. The settings
# singleton is built the first time it is touched, so a module-scope src import
# would freeze it from os.environ BEFORE main() calls load_dotenv -- and every
# later read would silently see defaults instead of the env file. security_state
# and percentiles are safe at module scope precisely because they import settings
# inside their own functions.
from src.benchmarks.percentiles import PERCENTILES, nearest_rank, unresolvable_quantiles
from src.benchmarks.security_state import FLUSHDB_SETTLE_SECONDS, clear_security_state

#: 4.4's p99 floor. Below it, p99 is reported as unresolvable with the arithmetic.
P99_SAMPLE_FLOOR: Final[int] = 10_000

#: The host name ASGITransport is addressed by. It never leaves the process --
#: httpx only needs a syntactically valid base_url to build absolute request
#: URLs -- so it must not be a routable name: `.invalid` is reserved by RFC 2606
#: precisely so nothing can resolve it.
IN_PROCESS_BASE_URL: Final[str] = "http://step4.invalid"

#: The degrading concurrency rrct identified for the production-controls arm.
DEFAULT_CONCURRENCY: Final[int] = 8

#: Tenants on the measurement host.
PROBE_TENANT: Final[str] = "system"

#: A desktop Chrome UA. It trips the attack-detection prefilter, so the full
#: combined regex runs on it -- the conservative branch, and the one a real
#: browser client would take.
PROBE_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

#: A user agent that does NOT trip the prefilter, for the header-scan contrast.
PROBE_HEADERS_BENIGN: Final[dict[str, str]] = {
    **PROBE_HEADERS,
    "User-Agent": "Step4Probe/1.0 (+https://probe.invalid/step4)",
}

#: The seed credential comes from STEP4_SEED_PASSWORD in the environment, and
#: there is deliberately no default. The repo's rule is "No silent defaults for
#: required config, env vars, secrets", and this repo has real credentials in
#: its history, so a plaintext fixture password committed here would outlive
#: every judgement call made about it. Read at CALL time, not as a module
#: constant, because the constant would freeze from os.environ before main()
#: runs load_dotenv(args.env) -- the same trap the module-scope src.app import
#: note below records.
SEED_PASSWORD_ENV_VAR: Final[str] = "STEP4_SEED_PASSWORD"
SEED_EMAIL_DOMAIN: Final[str] = "s4probe.org"
SEED_PROFILE_IMAGE_URL: Final[str] = "https://profileimageurl.com/i.png"
#: Name of the seeded full-access role. A DECLARED CONFIGURATION -- see
#: ``seed_rbac`` for why it is recorded as one.
RBAC_ROLE_NAME: Final[str] = "step4-full-access"
#: The only endpoint the seeder calls for credentials. Kept as a constant
#: because ``seed_accounts`` and the ``login`` endpoint entry must not be able
#: to drift onto different paths.
LOGIN_PATH: Final[str] = "/api/v1/account/login"


def seed_username(index: int) -> str:
    """The username ``seed_accounts`` will mint for account ``index``.

    One definition, used by both the seeder and the login endpoint's body. The
    login endpoint posts a real username, so a second copy of this format is a
    credential that was never seeded -- and a 401 that looks like an auth bug.
    """
    return f"s4c{index:04d}"


SEED_USERNAMES: Final[tuple[str, ...]] = tuple(seed_username(i) for i in range(256))


# --------------------------------------------------------------------------------------
# The eight 4.5 controls, read from the live settings object
# --------------------------------------------------------------------------------------


def read_eight_controls() -> dict[str, Any]:
    """Read the eight rate/ban controls from the running settings object.

    Not from ``src/.env.example`` and not from source: 4.5 says a phase is void if
    any of the eight "was in force at a load-producing value", which is a statement
    about the process, so it has to be read from the process.
    """
    from src.app.core.config import settings

    rate_limit = settings.rate_limit
    return {
        "auto_ban_enabled": bool(settings.security.AUTO_BAN_ENABLED),
        "security_budgets": {k: list(v) for k, v in rate_limit.SECURITY_BUDGETS.items()},
        "default_authenticated_limit": list(rate_limit.DEFAULT_AUTHENTICATED_LIMIT),
        "default_anonymous_limit": list(rate_limit.DEFAULT_ANONYMOUS_LIMIT),
        "anonymous_ip_aggregate_limit": list(rate_limit.ANONYMOUS_IP_AGGREGATE_LIMIT),
        # Bead FastAPI_saas-86p9. The API-key arm is only honest if the budget it
        # paces against is PINNED here like the other controls: an api_key
        # rate_budget read from a settings object nobody asserted would pace
        # against whatever the environment happened to say.
        "api_key_limit": list(rate_limit.API_KEY_LIMIT),
        "tier_rate_limits": {k: list(v) for k, v in rate_limit.TIER_RATE_LIMITS.items()},
        "default_rate_limit_limit": int(rate_limit.DEFAULT_RATE_LIMIT_LIMIT),
        "default_rate_limit_period": int(rate_limit.DEFAULT_RATE_LIMIT_PERIOD),
    }


#: The eight controls are only honest if they are pinned, so the campaign asserts
#: them against this. A difference is not a warning -- it voids the run.
EXPECTED_EIGHT: Final[dict[str, Any]] = {
    "auto_ban_enabled": True,
    "security_budgets": {
        "auth": [5, 60],
        "mfa_verify": [5, 60],
        "mfa_verify_global": [15, 60],
        "mfa_mutate": [5, 60],
        "magic_link_send": [3, 60],
        "passkey_login": [10, 60],
        "refresh": [60, 60],
        "ai_chat": [60, 60],
    },
    "default_authenticated_limit": [50, 3600],
    "default_anonymous_limit": [10, 3600],
    "anonymous_ip_aggregate_limit": [300, 3600],
    "api_key_limit": [1000, 3600],
    "tier_rate_limits": {
        "free": [10, 3600],
        "basic": [100, 3600],
        "pro": [1000, 3600],
        "enterprise": [10000, 3600],
    },
    "default_rate_limit_limit": 100,
    "default_rate_limit_period": 3600,
}


def controls_match(actual: dict[str, Any]) -> tuple[bool, list[str]]:
    """Compare the live eight against the pinned eight. Returns (ok, differences)."""
    differences: list[str] = []
    for key, expected in EXPECTED_EIGHT.items():
        got = actual.get(key)
        if got != expected:
            differences.append(f"{key}: expected {expected!r}, process has {got!r}")
    return (not differences, differences)


# --------------------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SeededAccount:
    username: str
    password: str
    access_token: str
    refresh_cookie: str
    tier: str
    #: ``users.id``. Needed by ``seed_rbac``: ``user_roles.user_id`` is an int,
    #: not the username the tokens carry.
    user_id: int


async def _invalidate_user_cache(user_id: int, username: str, email: str, tenant_id: Any) -> int:
    """Drop every cache key that can hold a copy of one seeded user.

    The auth-info key is keyed by USERNAME or EMAIL but its VALUE carries the
    user's integer id, so an entry written before the row was deleted and
    re-inserted hands the application an id that no longer exists. Nothing
    about the entry looks stale -- username, email, tenant and name all still
    match -- so the failure only surfaces where that id is used as a foreign
    key.

    Measured, not inferred. With a stale entry in place a POST to
    ``/api/v1/posts/by-user/{username}`` failed with ``Key
    (created_by_user_id)=(430) is not present in table "users"`` while the
    seeded rows were ids 360..439 and ``users`` held no row 430 at all:
    ``user:auth:{tenant}:s4c0005`` still read ``{"id":430,...}`` from a previous
    run. The username matched, so ``_require_post_owner`` passed and the wrong
    id reached the INSERT, failing on ``fk_post_created_by_user_id``.

    The key set comes from ``build_user_invalidation_delete_keys``, which is the
    OWNER of the delete side of this family and already covers the by-id key
    and both tenant scopes. Spelling keys here would be a second copy of a rule
    that has an owner -- and the first attempt at exactly that did: it used
    ``_user_username_cache_key``, which builds ``user:profile:...``, and so
    deleted nothing at all. The first version of this helper therefore fixed
    nothing while passing every gate; it was caught by reading the key the
    failing run left behind, not by any test.

    Returns the number of keys deleted. A missing client is a hard failure
    rather than a silent no-op: a purge that reports success while it is blind
    has already cost this harness a run.
    """
    from src.app.core.redis_provider import get_redis
    from src.app.modules.users.cache_keys import build_user_invalidation_delete_keys

    client = get_redis()
    if client is None:
        raise RuntimeError("Redis client is not initialised; cannot invalidate the user cache")

    keys = build_user_invalidation_delete_keys(user_id, username=username, email=email, tenant_id=tenant_id)
    return int(await client.delete(*keys))


async def _tenant_id() -> Any:
    """Resolve the probe tenant's UUID. A module-scope settings import would
    build the settings singleton before ``main()`` loads the env file, so every
    ``src.app`` import here is function-local."""
    import asyncpg

    from src.app.core.config import settings

    postgres = settings.postgres
    password = postgres.POSTGRES_PASSWORD
    if password is None:
        raise RuntimeError("POSTGRES_PASSWORD is not configured")
    connection = await asyncpg.connect(
        host=postgres.POSTGRES_SERVER,
        port=postgres.POSTGRES_PORT,
        user=postgres.POSTGRES_USER,
        password=password.get_secret_value(),
        database=postgres.POSTGRES_DB,
    )
    try:
        tenant_id = await connection.fetchval("select id from tenants where slug = $1", PROBE_TENANT)
    finally:
        await connection.close()
    if tenant_id is None:
        raise RuntimeError(f"tenant {PROBE_TENANT!r} does not exist")
    return tenant_id


def _application() -> Any:
    """Import the ASGI app lazily.

    Module scope deliberately holds no ``src.app`` import: the settings
    singleton is built at first import, and this module's ``main`` calls
    ``load_dotenv(args.env, override=True)`` afterwards. Importing ``src.app``
    at module scope therefore reads the environment *before* ``--env`` is
    applied and silently queries the wrong database.
    """
    from src.app.main import app

    return app


async def seed_accounts(count: int, tier: str, application: Any) -> tuple[list[SeededAccount], dict[str, Any]]:
    """Seed ``count`` accounts on ``tier`` and obtain a credential for each.

    Credentials come from the real ``POST /api/v1/account/login`` endpoint, one
    login per account. ``SECURITY_BUDGETS["auth"]`` is (5, 60) per SOURCE IP and
    the load host has exactly one, so seeding is rate-limited to five accounts a
    minute. That is a SETUP cost paid before any timing starts; preparing the
    pool and measuring the endpoint are different activities and only the second
    one is a measurement.

    Minting the token in-process was tried and abandoned -- see the comment at
    the login call below for the measured evidence. Anything the harness
    synthesises here risks measuring the harness rather than the application.
    """
    import asyncpg
    from asgi_lifespan import LifespanManager

    from src.app.core.config import settings
    from src.app.core.security.password import get_password_hash

    # settings.postgres.* not the flat delegation, and an explicit unwrap because
    # POSTGRES_PASSWORD is a SecretStr | None -- interpolating the DSN directly
    # sends the literal '**********' and fails authentication. Keyword arguments,
    # never a DSN string, so URL-encoding can never bite.
    postgres = settings.postgres
    password = postgres.POSTGRES_PASSWORD
    if password is None:
        raise RuntimeError("POSTGRES_PASSWORD is unset; the campaign cannot seed accounts")
    connection = await asyncpg.connect(
        host=postgres.POSTGRES_SERVER,
        port=postgres.POSTGRES_PORT,
        user=postgres.POSTGRES_USER,
        password=password.get_secret_value(),
        database=postgres.POSTGRES_DB,
    )
    # The login below has to run INSIDE the app's lifespan.
    # ``composition.py`` hands the Redis client to ``session_manager`` there and
    # nowhere else, and production's own ``register_login_session`` answers
    # "redis_unavailable" without it -- which under the default fail_closed mode
    # rejects the login with 503. Booting the lifespan for the seeding phase only
    # keeps that startup cost out of every measured request.
    #
    # ``lifespan`` is bound BEFORE the try so the finally can never reach an
    # unbound name; ``connection`` likewise, which is why the pool is opened above
    # the try rather than inside it.
    # LifespanManager's default startup AND shutdown timeout is five seconds,
    # which this application blows through on both -- it warms caches and opens
    # the ARQ pool on the way up. The defaults were producing a TimeoutError that
    # then masked whatever the seeding loop was actually doing.
    lifespan = LifespanManager(application, startup_timeout=180.0, shutdown_timeout=180.0)
    try:
        tenant_id = await connection.fetchval("select id from tenants where slug = $1", PROBE_TENANT)
        if tenant_id is None:
            raise RuntimeError(f"tenant {PROBE_TENANT!r} does not exist")
        tier_id = await connection.fetchval("select id from tier where name = $1", tier)
        if tier_id is None:
            raise RuntimeError(f"tier {tier!r} does not exist; seed it first")

        # Every column on the user model carries a PYTHON-side default, so a raw
        # INSERT that omits one stores NULL -- and directory.by_username filters
        # is_deleted=False, which silently excludes NULL. Set all of them.
        rows: list[Any] = []
        # Read once and reused: hashing N accounts would re-hash the same
        # credential N times otherwise.
        hashed = await get_password_hash(_seed_password())
        # ``__aenter__`` -- not ``startup()``. In asgi_lifespan 2.x ``startup()``
        # only puts {"type": "lifespan.startup"} on a queue and then waits for
        # ``_startup_complete``; the loop that receives that queue is started by
        # ``__aenter__``, which calls ``startup()`` itself. Calling ``startup()``
        # bare therefore enqueues a message nobody is listening for and blocks
        # until the timeout -- measured here, not inferred: it hung for the full
        # 180s and the receive queue was untouched.
        await lifespan.__aenter__()
        for index in range(count):
            username = seed_username(index)
            email = f"{username}@{SEED_EMAIL_DOMAIN}"
            await connection.execute("delete from users where username = $1", username)
            row = await connection.fetchrow(
                """
                insert into users (
                    tenant_id, tier_id, name, username, email, hashed_password,
                    profile_image_url, uuid, created_at, updated_at, deleted_at,
                    is_deleted, is_superuser, is_email_verified, is_active, token_version
                )
                values (
                    $1, $2, $3, $4, $5, $6,
                    $7, gen_random_uuid(), now(), now(), null,
                    false, false, true, true, 0
                )
                returning id, uuid, username, email, name, token_version
                """,
                tenant_id,
                tier_id,
                username,
                username,
                email,
                hashed,
                SEED_PROFILE_IMAGE_URL,
            )
            # The DELETE+INSERT above gave this username a NEW id, and every
            # cache that can hold a copy of the user still describes the row
            # the DELETE removed. This seeder is what makes those caches stale,
            # so this seeder is what has to clear them --
            # ``clear_security_state()`` cannot, because
            # ``security_state.py::SECURITY_KEY_PATTERNS`` deliberately covers
            # only the rate-limit and ban key families and not ``user:*``.
            #
            # It runs AFTER the insert so the by-id key of the row that now
            # exists is included, and BEFORE the login below so the session is
            # minted from the row rather than from a cached copy of its
            # predecessor. The measured consequence of getting this wrong is in
            # ``_invalidate_user_cache``.
            await _invalidate_user_cache(int(row["id"]), username, email, tenant_id)
            rows.append(row)

        # RBAC is granted BEFORE any token is minted, and that ORDER is the fix,
        # not a detail. The permission cache has two layers -- Redis at
        # ``perms:bitmask:{tenant}:{user}:{version}``
        # (``src/app/core/rbac/dependencies.py:110``) and an in-process L1 map in
        # ``src/app/modules/rbac/service.py``. Granting after the logins left the
        # first layer populated without the grant and
        # ``/users?per_page=100`` answered 403 on all four requests, while the
        # harness's own purge reported success throughout:
        # ``security_state.py::SECURITY_KEY_PATTERNS`` does not match ``perms:*``
        # at all, and no Redis purge can reach an in-process L1 entry regardless.
        # Fixing the order removes the stale entry instead of trying to
        # invalidate two caches. ``seed_rbac`` is idempotent.
        rbac = await seed_rbac([int(row["id"]) for row in rows], tenant_id)

        # Warm the app's OWN permission bitmask for every seeded account.
        #
        # Measured, not inferred. ``requires_permission``
        # (``src/app/core/rbac/dependencies.py:341-346``) prefers
        # ``request.state.cached_permissions_bitmask`` whenever that tuple is
        # non-None and never falls back when the mask denies. The tuple is filled
        # by the composite prefetch (``core/security/auth_prefetch.py:91``) from
        # the Redis key ``perms:bitmask:{tenant}:{user}:{version}``, and on a
        # cache MISS the prefetch yields mask 0 rather than consulting the
        # database. So on a cold key every RBAC-gated endpoint fails closed,
        # while a direct ``has_permission_cached`` call returns True -- measured
        # here as ``perm_int_direct True`` against ``BITMASK mask=0 ->
        # users:read False`` for the same user in the same process.
        #
        # Deleting the keys cannot fix this: the prefetch simply writes 0 again.
        # Calling ``has_permission_cached`` once per account is the app's own
        # backfill path (``_compute_and_cache_bitmask``), so this warms the cache
        # the prefetch reads. It computes the mask from the database -- it does
        # not fabricate one -- and it runs during SETUP, outside every timed
        # phase.
        from src.app.core.db.database import async_get_db
        from src.app.core.rbac.dependencies import has_permission_cached

        warmed = 0
        for row in rows:
            session = async_get_db()
            db = await session.__anext__()
            try:
                if await has_permission_cached(int(row["id"]), tenant_id, "users:read", db):
                    warmed += 1
            finally:
                await session.aclose()
        if warmed != len(rows):
            raise RuntimeError(
                f"permission backfill covered {warmed} of {len(rows)} seeded accounts; "
                "the RBAC grant is not visible to the application"
            )
        print(f"campaign: permission bitmask warmed for {warmed} accounts")

        accounts: list[SeededAccount] = []
        # Seeding is SETUP, not measured work, so the auth budget is cleared
        # between seed logins rather than slept through. The budget is (5, 60)
        # and IP-keyed, so 64 accounts at 5 per minute is 13 minutes of pure
        # sleeping before a single request is timed -- and the run proved it,
        # dying on "login for seeded account 's4c0005' answered 429". Sleeping
        # would also mean the seeded sessions were shaped by a budget the
        # measured phases never see. Purging here is declared in the artefact as
        # ``seed_login_budget_cleared`` so it can never be mistaken for a
        # production-shaped seed. The MEASURED phases are untouched: 4.5's
        # per-phase purge is separate and runs after every one of them.
        #
        # The FIRST login is not preceded by a purge. ``campaign()`` already
        # purged immediately before calling this function, so an extra purge
        # there would be redundant -- but a purge here would be WRONG, because
        # it would also clear the auth-info entries this function just
        # invalidated and let a fresh one be written from the row it inserted.
        seed_logins = 0
        for row in rows:
            if seed_logins:
                await clear_security_state()
                await asyncio.sleep(FLUSHDB_SETTLE_SECONDS)
            seed_logins += 1
            # Credentials come from the REAL login endpoint, never from a token
            # minted here.
            #
            # Two synthetic routes were tried and both are dead ends, and the
            # measured evidence for rejecting them belongs in this comment:
            #
            # 1. A token minted here via TokenIssuer carries a ``sid`` for which
            #    no session exists, so the auth dependency answers 401
            #    "Session has been revoked."
            # 2. Registering that sid ourselves does not fix it either. The
            #    registration lands under ``user_sessions_v2:<int id>`` and
            #    ``session_manager.is_session_valid`` returns True for exactly
            #    that key with the right field name -- verified directly on the
            #    target -- yet the request still 401s, because
            #    ``_enforce_session_valid`` raises on the composite prefetch's
            #    verdict BEFORE it consults that store.
            #
            # The claims were never the fault: a claim-for-claim identical pair
            # taken from a real login works. So the campaign drives the
            # production path and uses the token the server issues. Any
            # measurement taken from a synthesised credential would be a
            # measurement of the harness, not of the application.
            #
            # Cost: the auth budget is (5, 60) per source IP, so seeding N
            # accounts performs N logins at 5 per minute. That is a SETUP cost
            # paid once before any timing starts, not a measurement cost -- and
            # it is the same budget ADR-0022 already accounts for when it puts
            # EM-5 login in rule 2.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application),
                base_url="http://step4.invalid",
                timeout=60.0,
            ) as client:
                response = await client.post(
                    LOGIN_PATH,
                    content=_login_form(row["username"]),
                    # X-Tenant-ID is required on every non-exempt path; without it the
                    # tenant middleware answers 400 before the login is even read.
                    headers={
                        **PROBE_HEADERS,
                        "X-Tenant-ID": PROBE_TENANT,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
            if response.status_code != 200:
                raise RuntimeError(
                    f"login for seeded account {row['username']!r} answered "
                    f"{response.status_code}: {response.text[:300]}"
                )
            payload = response.json()
            access_token = payload["access_token"]
            refresh_cookie = response.cookies.get("refresh_token") or payload.get("refresh_token")
            if refresh_cookie is None:
                raise RuntimeError(
                    f"login for {row['username']!r} returned no refresh token; "
                    "the refresh endpoint cannot be driven without one"
                )

            accounts.append(
                SeededAccount(
                    username=row["username"],
                    password=_seed_password(),
                    access_token=access_token,
                    refresh_cookie=refresh_cookie,
                    tier=tier,
                    user_id=row["id"],
                )
            )
        return accounts, rbac
    finally:
        # The shutdown must never mask the failure that brought us here. An
        # exception raised inside a finally REPLACES the one already in flight,
        # so a shutdown timeout would silently swallow "login answered 429" --
        # which is precisely the failure this harness exists to surface. So the
        # in-flight exception is tested first and the shutdown only re-raises
        # when nothing else is propagating.
        #
        # ``__aexit__`` rather than ``shutdown()`` for the same reason as
        # ``__aenter__``: it is the entry that unwinds the receive loop
        # ``__aenter__`` started, and it forwards the exception triple so the
        # lifespan sees the failure the seeding loop actually hit.
        exc_type, exc_value, exc_tb = sys.exc_info()
        propagating = exc_type is not None
        try:
            await lifespan.__aexit__(exc_type, exc_value, exc_tb)
        except Exception:
            if not propagating:
                raise
        await connection.close()


# --------------------------------------------------------------------------------------
# Endpoint definitions
# --------------------------------------------------------------------------------------


async def seed_rbac(user_ids: Sequence[int], tenant_id: Any) -> dict[str, Any]:
    """Grant the seeded accounts a role carrying every permission in the catalog.

    Without this, ``GET /api/v1/users?per_page=100`` answers 403
    ``Permission denied: users:read``: ``permissions`` ships with 26 rows but
    ``roles``, ``role_permissions`` and ``user_roles`` are all empty on a fresh
    database, so an authenticated user holds no permissions at all.

    This is a DECLARED CONFIGURATION, exactly like the tier. It is recorded in
    the artefact and must be reviewed as one: "full access" is a real shape a
    customer role can have, and picking it because it is the cheapest to seed
    is the same failure mode as picking a tier because it makes n easy. The
    alternative -- a read-only role -- measures the permission cache under a
    different grant and is not what the endpoint's own cost is dominated by.

    Idempotent: re-running replaces the probe role's grants rather than
    accumulating them.
    """
    import asyncpg

    from src.app.core.config import settings

    postgres = settings.postgres
    password = postgres.POSTGRES_PASSWORD
    if password is None:
        raise RuntimeError("POSTGRES_PASSWORD is not configured")
    connection = await asyncpg.connect(
        host=postgres.POSTGRES_SERVER,
        port=postgres.POSTGRES_PORT,
        user=postgres.POSTGRES_USER,
        password=password.get_secret_value(),
        database=postgres.POSTGRES_DB,
    )
    try:
        role_id = await connection.fetchval(
            """
            insert into roles (tenant_id, name, description, is_system)
            values ($1, $2, $3, true)
            on conflict do nothing
            returning id
            """,
            tenant_id,
            RBAC_ROLE_NAME,
            "Step 4 campaign: full-access role, seeded so RBAC-gated endpoints are reachable.",
        )
        if role_id is None:
            role_id = await connection.fetchval(
                "select id from roles where tenant_id = $1 and name = $2",
                tenant_id,
                RBAC_ROLE_NAME,
            )
        if role_id is None:
            raise RuntimeError(f"could not resolve role {RBAC_ROLE_NAME!r}")

        permission_slugs = await connection.fetch("select slug from permissions order by slug")
        await connection.execute("delete from role_permissions where role_id = $1", role_id)
        granted = await connection.fetch(
            """
            insert into role_permissions (role_id, permission_id)
            select $1, id from permissions
            returning permission_id
            """,
            role_id,
        )
        await connection.execute(
            "delete from user_roles where tenant_id = $1 and role_id = $2",
            tenant_id,
            role_id,
        )
        assigned = await connection.fetch(
            """
            insert into user_roles (user_id, tenant_id, role_id)
            select unnest($1::int[]), $2, $3
            returning user_id
            """,
            list(user_ids),
            tenant_id,
            role_id,
        )
        return {
            "role": RBAC_ROLE_NAME,
            "role_id": role_id,
            "permissions_in_catalog": len(permission_slugs),
            "permissions_granted": len(granted),
            "users_assigned": len(assigned),
            "note": (
                "A DECLARED CONFIGURATION, like the tier. Full access was chosen "
                "because it is the grant a customer admin role would carry; a "
                "read-only role would measure the same endpoints under a "
                "different permission cache shape."
            ),
        }
    finally:
        await connection.close()


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str
    #: "authed" | "anon" | "none" | "api_key"
    auth: str
    #: Status codes that are a SUCCESS for this endpoint. Anything else voids the phase.
    expected_statuses: frozenset[int]
    #: Human note for the artefact.
    note: str
    #: Pre-encoded request body, sent verbatim. Built at import time so the
    #: cost of building it is never inside a measured phase.
    body: str | None = None
    #: Username to form-encode as a login body LAZILY, because the credential
    #: comes from the environment and this dataclass is built at import time.
    #: Set on the ``login`` entry only; ``_login_username`` fills ``body``.
    login_username: str | None = None
    #: Content-Type for ``body``. None means no body and no header.
    content_type: str | None = None
    #: Which of the EIGHT controls bounds this endpoint, or None when nothing
    #: does. This is not a tuning knob: it names the knob, and the value is read
    #: from the LIVE settings object at run time, so it cannot drift from the
    #: control the campaign already asserts.
    #:
    #: It exists because the alternative was measured and it does not work.
    #: Fired flat out, ``login`` answers 200 exactly 5 times and RATE_LIMITED
    #: for the next 55 seconds, ``logout`` and ``refresh`` inherit the same
    #: auth leg, and ``users_me_anon`` answers 401 exactly 10 times -- which is
    #: DEFAULT_ANONYMOUS_LIMIT. Every one of those is a real limiter doing its
    #: job, and together they mean these endpoints have no clean sample at any
    #: n the harness can reach from one source IP.
    #:
    #: So the harness PACES instead. That is the opposite of relaxing: 4.5 voids a
    #: run where a control was in force at a LOAD-PRODUCING value, and pacing is
    #: how the run RESPECTS the production value. The alternative -- recording
    #: 49 of 50 responses as limiter rejections -- measures the limiter, not the
    #: endpoint.
    #:   "auth"      -> SECURITY_BUDGETS["auth"]           (5, 60)
    #:   "anonymous" -> DEFAULT_ANONYMOUS_LIMIT            (10, 3600)
    #:   "api_key"   -> API_KEY_LIMIT                     (1000, 3600)
    rate_budget: tuple[str, ...] = ()


def _resolve_rate_budget(
    names: tuple[str, ...], controls: dict[str, Any]
) -> tuple[list[tuple[int, int]], list[str]] | None:
    """The live ``(limit, period_seconds)`` for EVERY control that binds an endpoint.

    Returns ``(budgets, names)`` -- a list, not a winner. An endpoint can be
    charged by more than one control and the honest pacing honours all of them,
    because no single one of them is the whole truth.

    Read from the controls the campaign already read and already asserted, so
    the pacing and the pin cannot disagree: if the value in force is not the
    production value the run is void before any endpoint is driven, and if it is
    then this returns the numbers the app is actually enforcing.

    Measured on perf-local, and the two runs disagree in a way that is the
    reason this is not a single number. Both ``login`` and ``logout`` are charged
    ``SECURITY_BUDGETS['auth'] (5, 60)`` AND the anonymous per-path limit
    ``DEFAULT_ANONYMOUS_LIMIT (10, 3600)`` (keys
    ``gcra:rate_limit:anonymous:ip:127.0.0.1:account_login`` and
    ``...:account_logout``, TTL near 3600, stored value advancing 360000 ms per
    request). Paced at 5/60, the auth window always refills and the anonymous
    one is spent at request 10 -- ``/tmp/s4_paced.json`` logged out 15x200 then
    9x429. Paced at 10/3600, a burst spends the auth budget first and requests
    6-10 are refused with the auth body -- ``/tmp/s4_anon.json`` logged in 5x200
    then 5x429. Neither run was a pacing failure; the shape was wrong.
    """
    if not names:
        return None

    budgets: list[tuple[int, int]] = []
    for name in names:
        if name == "auth":
            pair = controls["security_budgets"]["auth"]
        elif name == "anonymous":
            pair = controls["default_anonymous_limit"]
        elif name == "api_key":
            pair = controls["api_key_limit"]
        else:
            raise ValueError(f"unknown rate_budget {name!r}; the selectors are 'auth', 'anonymous' and 'api_key'")
        limit, period = int(pair[0]), int(pair[1])
        if limit <= 0 or period <= 0:
            raise ValueError(f"rate_budget {name!r} resolved to a non-positive ({limit}, {period})")
        budgets.append((limit, period))
    return budgets, list(names)


class BudgetGate:
    """Paces requests so no window carries more than ``limit`` of them.

    Sliding window over send timestamps, not a fixed bucket, because the limiter
    is a GCRA token bucket and a fixed-window pacer would sit idle at the seam
    while the budget was already spent.

    The lock is held across the sleep on purpose: pacing is a property of the
    whole phase, not of one request, and releasing it would let a burst of
    waiters fire together the moment the window opened.
    """

    def __init__(self, *budgets: tuple[int, float]) -> None:
        if not budgets:
            raise ValueError("BudgetGate needs at least one (limit, period_seconds) window")
        for limit, period in budgets:
            if limit <= 0 or period <= 0:
                raise ValueError(f"non-positive budget ({limit}, {period})")
        # A sliding window PER BUDGET, not one window for the lot. Measured on
        # perf-local: login and logout are each charged two controls with
        # different shapes -- SECURITY_BUDGETS['auth'] (5, 60) and the anonymous
        # per-path limit (10, 3600) -- and NO single rate respects both. Paced at
        # 5/60 the auth window always refills and the anonymous one is spent at
        # request 10 (/tmp/s4_paced.json: logout 15x200 then 9x429). Paced at
        # 10/3600 a burst spends the auth budget first and requests 6-10 are
        # refused with the auth body (/tmp/s4_anon.json: login 5x200 then 5x429).
        # Two runs, two different answers, neither a pacing failure: the shape
        # was wrong. This shape holds every window at once, which is what GCRA
        # does, so it is the only one that can honestly claim the run respected
        # both controls.
        self._budgets = tuple((int(limit), float(period)) for limit, period in budgets)
        self._stamps: list[list[float]] = [[] for _ in self._budgets]
        self._lock = asyncio.Lock()
        self.waits = 0
        self.waited_s = 0.0
        self.bound_by: dict[str, int] = {}

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.perf_counter()
                # Drop what each window no longer counts, and find the earliest
                # moment at which EVERY window has room. The binding window is
                # the one with the longest wait, not the first one found.
                waits: list[float] = []
                for index, (limit, period) in enumerate(self._budgets):
                    kept = [s for s in self._stamps[index] if now - s < period]
                    self._stamps[index] = kept
                    if len(kept) >= limit:
                        waits.append(period - (now - kept[0]))
                if not waits:
                    for index in range(len(self._budgets)):
                        self._stamps[index].append(now)
                    return
                longest = max(waits)
                binding = waits.index(longest)
                label = f"{self._budgets[binding][0]} per {self._budgets[binding][1]:g}s"
                self.waits += 1
                self.bound_by[label] = self.bound_by.get(label, 0) + 1
                self.waited_s += longest
                await asyncio.sleep(longest)

    def describe(self) -> dict[str, Any]:
        return {
            "budgets_enforced": [
                {"limit_per_window": limit, "window_seconds": period} for limit, period in self._budgets
            ],
            "times_a_window_had_to_wait": self.waits,
            "seconds_spent_waiting": round(self.waited_s, 3),
            "which_window_bound": self.bound_by,
            "note": (
                "Pacing is how the run RESPECTS the production limit. 4.5 voids a run "
                "in which a control was in force at a load-producing value; pacing is "
                "the opposite of raising one. Every window named by the endpoint is "
                "enforced at once, because no single rate respects (5, 60) and "
                "(10, 3600) together -- measured, both ways, in two runs."
            ),
        }


#: EM-8's search term, in KILOBYTES. The query string is what carries it, and the
#: attack-detection middleware scans the query string on EVERY method, above the
#: method gate -- so this is the prefilter-HIT read path on a GET, which is the
#: case scripts/query_search_scan.py exists to describe and the only one of the
#: ten that puts the expensive scan on a method that does not send a body.
SEARCH_TERM_KB: Final[int] = 4

#: The filler is a real English pangram sequence, and it is chosen because it
#: CONTAINS "or" (in "for"), which is the shortest entry in
#: ``_PREFILTER_LITERALS_BYTES`` at two bytes -- so a term built from it must
#: trip the prefilter. That is a claim about a string, so the harness ASSERTS the
#: property against the middleware's own prefilter before timing anything, rather
#: than trusting that this sentence is still true after someone edits the
#: literal set. A term that quietly stopped hitting would otherwise measure the
#: cheap branch and publish it as the expensive one.
_SEARCH_FILLER: Final[str] = "for the quick brown wiggly daffy numbats "


def build_search_term() -> str:
    """A ``SEARCH_TERM_KB``-kilobyte search term that trips the attack prefilter."""
    repeats = (SEARCH_TERM_KB * 1024) // len(_SEARCH_FILLER) + 1
    return (_SEARCH_FILLER * repeats)[: SEARCH_TERM_KB * 1024]


def build_search_path() -> str:
    """EM-8's path, with the search term percent-encoded.

    The encoding is not cosmetic. What the middleware scans is the query string
    AS IT ARRIVES, so the term has to be encoded into the path and then
    asserted in that encoded form. A raw space in a URL is not a valid URL, and
    a term asserted before encoding but sent encoded would be asserting a
    property of a string the server never sees.
    """
    encoded = urllib.parse.quote(build_search_term(), safe="")
    return f"/api/v1/users?search={encoded}&page=1&per_page=100"


def assert_search_term_hits() -> None:
    """Fail fast unless the term really does hit the prefilter, and really is 4 KB.

    Asserted on the ENCODED query string, which is the only form the server ever
    sees. The assertion is the endpoint's whole premise: EM-8 is the
    prefilter-HIT regime, and a measurement of the MISS branch published under
    this name would be worse than no measurement.
    """
    from src.app.middleware.attack_detection import _attack_prefilter_hit

    path = build_search_path()
    query = path.split("?", 1)[1]
    encoded = query.encode()
    if not _attack_prefilter_hit(encoded):
        raise RuntimeError(
            f"the EM-8 search term ({len(encoded)} bytes on the wire) does NOT trip "
            "the attack prefilter, so this endpoint would measure the cheap branch "
            "under the name of the expensive one. Change _SEARCH_FILLER."
        )
    if len(encoded) < SEARCH_TERM_KB * 1024:
        raise RuntimeError(
            f"the EM-8 search term is {len(encoded)} bytes on the wire, which is under "
            f"the {SEARCH_TERM_KB} KB the endpoint is named for: percent-encoding "
            f"expanded it by {len(encoded) - SEARCH_TERM_KB * 1024} bytes."
        )


def _resolve_login_bodies(endpoints: tuple[Endpoint, ...]) -> tuple[Endpoint, ...]:
    """Fill ``body`` for the login form at call time, not import time.

    ``main()`` loads the env file, so ``_seed_password()`` cannot be read while
    the module is being imported -- it would fail fast at import instead of at
    the seeding phase, which is where the operator needs the error.
    """
    return tuple(
        dataclasses.replace(ep, body=_login_form(ep.login_username)) if ep.login_username is not None else ep
        for ep in endpoints
    )


#: The credentials the seeded accounts are created with. Form-encoded because
#: ``POST /api/v1/account/login`` takes form data, not JSON (the shape
#: ``scripts/rtt_distribution_probe.py:143-148`` already proves works).
def _seed_password() -> str:
    """The seed credential, read from the environment at call time.

    Raises rather than defaulting: an unset value means the operator has not
    told the harness what credential to seed with, and inventing one would
    publish accounts nobody can log into.
    """
    value = os.environ.get(SEED_PASSWORD_ENV_VAR)
    if value is None:
        raise RuntimeError(
            f"{SEED_PASSWORD_ENV_VAR} is not set. The campaign seeds accounts and "
            "logs them in through the real endpoint, so it needs the credential to "
            "create them with. Add it to the env file passed by --env; it is a "
            "throwaway fixture for a local database, never a production secret."
        )
    if not value:
        raise RuntimeError(f"{SEED_PASSWORD_ENV_VAR} is set but empty")
    return value


#: Bead FastAPI_saas-86p9. The api_key arm authenticates with a key minted OUT OF
#: BAND, exactly as the seed password comes from the environment: the campaign's
#: job is to measure the endpoint, and minting a key is a setup concern. Mint it
#: with ``POST /api/v1/api-keys`` on the target and export the plaintext once.
API_KEY_ENV_VAR: Final[str] = "STEP4_API_KEY"

#: One source IP per element, asserted distinct below. The api_key budget is keyed
#: by SOURCE IP, so a phase that wants more than API_KEY_LIMIT requests needs more
#: than one source. The campaign drives 127.0.0.1, which ``is_trusted_proxy``
#: treats as a trusted proxy (src/app/core/utils/ip_utils.py:88-110), so
#: ``get_client_ip`` honours X-Forwarded-For from it -- which is what a real
#: deployment behind an edge looks like.
API_KEY_SOURCE_IPS: Final[tuple[str, ...]] = tuple(f"127.20.0.{i}" for i in range(1, 17))
assert len(set(API_KEY_SOURCE_IPS)) == len(API_KEY_SOURCE_IPS), "source IP pool must be distinct"


def api_key() -> str:
    """The plaintext API key, read from the environment.

    Fails fast and loudly, like ``_seed_password``: a missing credential that
    defaulted to something would publish 401s under an endpoint's name.
    """
    value = os.environ.get(API_KEY_ENV_VAR)
    if not value:
        raise RuntimeError(
            f"{API_KEY_ENV_VAR} is not set. The api_key arm measures an authenticated "
            "key; mint one with POST /api/v1/api-keys on the target and export its "
            "plaintext once."
        )
    return value


def _login_form(username: str) -> str:
    return f"username={urllib.parse.quote_plus(username)}&password={urllib.parse.quote_plus(_seed_password())}"


ENDPOINTS: Final[tuple[Endpoint, ...]] = (
    Endpoint("users_me", "GET", "/api/v1/users/me", "authed", frozenset({200}), "EM-1"),
    Endpoint(
        "users_list",
        "GET",
        "/api/v1/users?page=1&per_page=100",
        "authed",
        frozenset({200}),
        "EM-7; needs the full-access RBAC role seeded, or 403 users:read",
    ),
    Endpoint(
        "health",
        "GET",
        "/api/v1/health",
        "none",
        frozenset({200}),
        (
            "EM-3; /api/v1/health is in COMMON_EXEMPT_PATHS "
            "(src/app/core/middleware_exempt.py:29) and is not in "
            "_BAN_CHECKED_EXEMPT_PREFIXES, so request_admission.py:87-91 "
            "returns it fully unchecked. No limiter applies at any n."
        ),
    ),
    Endpoint(
        "users_search",
        "GET",
        # The search term goes in the QUERY STRING, which is where the middleware
        # scans it on every method -- this is the prefilter-HIT regime on a GET.
        # `read_users` itself takes only page/per_page and ignores the extra
        # parameter, which is the point: the request is shaped by what the
        # middleware has to read, not by what the handler does with it.
        build_search_path(),
        "authed",
        frozenset({200}),
        (
            "EM-8. The prefilter-HIT read path, on a GET: the query string is "
            f"scanned on every method above the method gate, so a {SEARCH_TERM_KB} KB "
            "term puts the expensive scan on a request that sends no body. The term "
            "is percent-encoded into the path, so it is LARGER on the wire than the "
            f"{SEARCH_TERM_KB} KB of text it is built from -- that expansion is why "
            "the assertion checks the ENCODED query, which is the only form the "
            "server sees. The HIT property is ASSERTED against the middleware's own "
            "prefilter before timing (assert_search_term_hits), never assumed, "
            "because a term that quietly stopped matching would measure the cheap "
            "branch and publish it under this name. Carries no rate_budget: it is "
            "user-keyed, bounded by TIER_RATE_LIMITS, the same as users_list."
        ),
    ),
    Endpoint(
        "users_me_apikey",
        "GET",
        "/api/v1/users/me",
        "api_key",
        frozenset({200}),
        (
            "EM-11, added by bead FastAPI_saas-86p9. The same path and handler as "
            "users_me, reached with `X-API-Key` instead of a Bearer token, so the "
            "difference between the two numbers is the credential seam and nothing "
            "else. The arm exists because no API-key endpoint appeared in v8qm's "
            "set at all, which left MGET's 0.90 priority score with no denominator. "
            "Carries rate_budget=('api_key',): since g4my the request is charged "
            "API_KEY_LIMIT (1000, 3600) per SOURCE IP on "
            "rate_limit:api_key:ip:<ip>:<path> -- an admission-time bucket, because "
            "admission runs before the key can be validated -- and is exempt from "
            "the anonymous IP aggregate ceiling and from auto-ban. The pacing is "
            "therefore the production one, and n per repetition stays under 10,000 "
            "so p99 is WITHHELD rather than estimated (ADR-0022 Rule 2), the same "
            "shape users_me_anon carries. Requests rotate X-Forwarded-For over "
            "API_KEY_SOURCE_IPS because the bucket is IP-keyed and one address "
            "cannot carry a whole phase. The deeper per-request counts (round trips "
            "against commands) are M-7's, in "
            "perf-results/RESULT-20261017-m7/m7_apikey_endpoint_baseline.json; this "
            "arm measures the path over a real socket, as the other arms do."
        ),
        rate_budget=("api_key",),
    ),
    Endpoint(
        "users_me_anon",
        "GET",
        "/api/v1/users/me",
        "anon",
        frozenset({401}),
        (
            "EM-2; 401 is the expected rejection, not a void. Bound by "
            "DEFAULT_ANONYMOUS_LIMIT (10, 3600) per source IP, so n<10,000 -- "
            "ADR-0022 rule 2."
        ),
        # Measured: fired flat out this endpoint answered 401 exactly 10 times
        # and RATE_LIMITED thereafter, which is DEFAULT_ANONYMOUS_LIMIT exactly.
        # At 10 per 3600 this is also the endpoint whose pacing dominates: the
        # window is an HOUR, so any n above 10 takes proportionally longer than
        # the others and a run that wants more than a handful has to say so.
        rate_budget=("anonymous",),
    ),
    Endpoint(
        "login",
        "POST",
        "/api/v1/account/login",
        "anon",
        frozenset({200}),
        (
            "EM-5. TWO controls bind this endpoint and only the tighter one paces "
            "it. A login carries no access token, so the limiter keys it "
            "anonymous on the client IP, and its own path carries a per-path "
            "anonymous key: measured on perf-local, requests 0-9 answer 200 and "
            "request 10 answers 429, with gcra:rate_limit:anonymous:ip:127.0.0.1:"
            "account_login AND gcra:rate_limit:user:127.0.0.1:budget:auth both "
            "present. Which one binds depends on the RATE, and the two runs "
            "disagree in a way that is the finding: paced at 5/60 the AUTH leg "
            "always refills and the ANONYMOUS one runs out at request 10; paced "
            "at 10/3600 a burst spends the AUTH leg first and requests 6-10 are "
            "refused with the auth body 'Too many auth attempts' (/tmp/"
            "s4_anon.json, 5x200 then 5x429). So both are named and the tighter "
            "is chosen per run."
        ),
        # body= is filled in lazily: the seed credential comes from the
        # environment, and a module-scope call would read it at import time,
        # before main() runs load_dotenv(args.env). See _login_form.
        login_username=seed_username(0),
        content_type="application/x-www-form-urlencoded",
        rate_budget=("auth", "anonymous"),
    ),
    Endpoint(
        "logout",
        "POST",
        "/api/v1/account/logout",
        "authed",
        frozenset({200}),
        (
            "EM-6. Logout has no budget leg of its own, but it is charged two "
            "others. Measured on perf-local with a paced probe "
            "(l8_probe_which_limiter_logout.py, 16 requests at 5 per 60 s from a "
            "purged start): requests 0-10 answer 200 and request 11 answers 429 "
            "with security:violations:127.0.0.1 appearing at that request, while "
            "gcra:rate_limit:anonymous:ip:127.0.0.1:account_logout sits in the "
            "key set with a TTL near 3600 whose stored value advances by exactly "
            "360000 ms per request -- the GCRA 'when you may next act' stamp. "
            "Eleven successes is 10 anonymous per-path + 1, and the auth leg "
            "refilled throughout (TTL 72). ADR-0022 rule 2."
        ),
        rate_budget=("auth", "anonymous"),
    ),
    Endpoint(
        "refresh",
        "POST",
        "/api/v1/account/refresh",
        "authed",
        frozenset({200}),
        "EM-4; refresh token arrives as a cookie; SECURITY_BUDGETS['refresh'] is (60, 60) IP-keyed",
        # SECURITY_BUDGETS['refresh'] is (60, 60), so pacing at the AUTH leg
        # (5, 60) is strictly the more conservative of the two and is what the
        # observed rejection body named.
        rate_budget=("auth",),
    ),
)


# --------------------------------------------------------------------------------------
# Driving
# --------------------------------------------------------------------------------------


@dataclass
class PhaseResult:
    name: str
    method: str
    path: str
    note: str
    expected_statuses: frozenset[int]
    repetitions: int
    concurrency: int
    #: One latency list per repetition, in seconds.
    per_repetition: list[list[float]] = field(default_factory=list)
    status_counts: dict[int, int] = field(default_factory=dict)
    #: VERBATIM bodies for the first few UNEXPECTED statuses. A count alone
    #: cannot distinguish "the permission was denied" from "the limiter fired"
    #: or "the serialiser rejected the row", and bead FastAPI_saas-nymp's
    #: acceptance demands the 403 be read from an actual response body rather
    #: than theorised. Bounded so a 10000-request phase stays readable.
    unexpected_bodies: list[dict[str, Any]] = field(default_factory=list)
    #: How the phase was paced, or None when the endpoint carries no rate budget.
    #: Present so a reader can tell "this endpoint has no limiter" from "this
    #: endpoint was driven under its limiter" -- the two look identical in the
    #: latencies alone, and only one of them is a measurement of the endpoint.
    pacing: dict[str, Any] | None = None
    #: How many rotated refresh tokens this phase picked up. Only meaningful
    #: for the refresh endpoint; None elsewhere. See drive_phase -- replaying the
    #: seed-time cookie is what made 19 of 25 requests 401.
    refresh_rotations: int | None = None

    def summary(self) -> dict[str, Any]:
        pooled = [latency for rep in self.per_repetition for latency in rep]
        p50s = [nearest_rank(rep, 0.5) * 1000.0 for rep in self.per_repetition]
        mins = [min(rep) * 1000.0 for rep in self.per_repetition]
        pooled_n = len(pooled)

        # ADR-0022 Rule 2: below the 10,000 floor the p99 is WITHHELD, not
        # estimated. It is withheld on the floor, not on unresolvable_quantiles:
        # that function reports quantiles whose rank lands on the last SAMPLE,
        # a property of the sample, and at n=1000 it trips neither p95 nor p99.
        # Testing a string label against its float return was the bug that let
        # four endpoints publish a p99 from a sample of 10.
        p99_reportable = pooled_n >= P99_SAMPLE_FLOOR
        reportable: dict[str, Any] = {}
        for quantile in PERCENTILES:
            label = f"p{int(quantile * 100)}"
            if label == "p99" and not p99_reportable:
                reportable[label] = None
            else:
                reportable[label] = round(nearest_rank(pooled, quantile) * 1000.0, 4)

        observed = set(self.status_counts)
        return {
            "endpoint": self.name,
            "method": self.method,
            "path": self.path,
            "note": self.note,
            "repetitions": self.repetitions,
            "concurrency": self.concurrency,
            "pooled_n": len(pooled),
            "p50_median_of_repetitions_ms": round(statistics.median(p50s), 4),
            "p50_min_of_repetitions_ms": round(min(p50s), 4),
            "min_median_of_repetitions_ms": round(statistics.median(mins), 4),
            "min_min_of_repetitions_ms": round(min(mins), 4),
            "reportable_ms": reportable,
            "unresolvable_percentiles": sorted(unresolvable_quantiles(pooled_n, PERCENTILES)),
            "p99_reportable": p99_reportable,
            "p99_note": (
                None
                if p99_reportable
                else (
                    f"withheld: pooled n={pooled_n} is below the {P99_SAMPLE_FLOOR} sample "
                    "floor (ADR-0022 Rule 2). A tail quantile read off a sample this "
                    "size is a number with no estimate behind it. The p50 and p95 above "
                    "did resolve and are the ones to quote."
                )
            ),
            "status_counts": {str(k): v for k, v in sorted(self.status_counts.items())},
            "expected_statuses": sorted(self.expected_statuses),
            "status_is_clean": observed <= self.expected_statuses,
            "unexpected_statuses": sorted(observed - self.expected_statuses),
            "unexpected_bodies": self.unexpected_bodies,
            "unexpected_body_count": len(self.unexpected_bodies),
            "pacing": self.pacing,
            "refresh_rotations": self.refresh_rotations,
        }


async def drive_phase(
    endpoint: Endpoint,
    accounts: list[SeededAccount],
    *,
    requests_per_repetition: int,
    repetitions: int,
    concurrency: int,
    base_url: str = "",
    budget: tuple[list[tuple[int, int]], list[str]] | None = None,
) -> PhaseResult:
    """Drive one endpoint and collect one latency sample per repetition.

    ``base_url`` empty drives the app IN-PROCESS over ASGITransport. That is the
    fast form and it is the one this harness used until it was measured to be
    the wrong shape for criterion (6): with no uvicorn there is no socket and no
    HTTP implementation in the request path, so an app-host report built beside
    it would record a runtime that served nothing.

    A non-empty ``base_url`` drives a REAL uvicorn over a real socket. That adds
    a hop and changes every number, which is why the topology is recorded in the
    artefact rather than left implicit -- two sets of figures from two shapes
    that share an endpoint name is precisely how this programme produced a
    protocol.py figure that was understated by 10^6.
    """
    if base_url:
        transport = None
        client_kwargs: dict[str, Any] = {"base_url": base_url}
    else:
        from src.app.main import app as application

        transport = httpx.ASGITransport(app=application)
        client_kwargs = {"base_url": IN_PROCESS_BASE_URL}

    result = PhaseResult(
        name=endpoint.name,
        method=endpoint.method,
        path=endpoint.path,
        note=endpoint.note,
        expected_statuses=endpoint.expected_statuses,
        repetitions=repetitions,
        concurrency=concurrency,
    )

    semaphore = asyncio.Semaphore(concurrency)
    # ``budget`` is (budgets, names) as resolved by _resolve_rate_budget: every
    # window the endpoint is charged, with the control each came from, so the
    # phase records not just the numbers it was paced at but why those numbers.
    gate = BudgetGate(*budget[0]) if budget is not None else None
    budget_names = budget[1] if budget is not None else None

    # ONE client for the whole phase, not one per request. Measured on
    # perf-local against a real uvicorn at concurrency 8 (n=200,
    # /api/v1/health, p50 of per-request wall time):
    #
    #   in-process, per-request client   0.57 ms
    #   socket,    SHARED client        13.28 ms
    #   socket,    per-request client   56.84 ms
    #
    # A fresh client per request means a fresh TCP connection, a fresh connect
    # and a fresh teardown on every measured request, which is 43.6 ms of the
    # 56.84 -- about 78%. That is harness overhead sitting inside a measurement
    # of the application, and it dominated: health read p50 87.89 ms over the
    # socket when its own handler costs under 1 ms.
    #
    # The shared client is also the MORE representative shape. A real client
    # pools connections and reuses them; one-connection-per-request is not a
    # shape any production caller produces. So this is not a measurement being
    # bent toward a nicer number, it is the measurement being taken against the
    # behaviour the endpoint actually has.
    client = httpx.AsyncClient(transport=transport, timeout=120.0, **client_kwargs)

    # Per-account live refresh cookies, seeded from the ones the seeder captured
    # and rewritten after every successful refresh. Kept local to the phase rather
    # than mutated onto SeededAccount so a phase cannot leave a rotated token
    # behind for the next one.
    live_refresh: dict[str, str] = {a.username: a.refresh_cookie for a in accounts}
    rotations = 0

    async def one(index: int) -> float:
        nonlocal rotations
        account = accounts[index % len(accounts)] if accounts else None
        headers = {
            **PROBE_HEADERS,
            "X-Tenant-ID": PROBE_TENANT,
        }
        cookies: dict[str, str] = {}
        if endpoint.auth == "api_key":
            headers["X-API-Key"] = api_key()
            # Rotated per request over a pool asserted distinct at module scope:
            # the api_key bucket is keyed by this value, so a pool that silently
            # collapsed to one address would charge a whole phase to one budget
            # and turn the phase's refusals into a pacing artefact.
            headers["X-Forwarded-For"] = API_KEY_SOURCE_IPS[index % len(API_KEY_SOURCE_IPS)]
        elif endpoint.auth == "authed" and account is not None:
            headers["Authorization"] = f"Bearer {account.access_token}"
            if endpoint.name == "refresh":
                # The refresh token arrives as an http-only cookie, not as a
                # header or a JSON field. Sending the access token alone gets a
                # 401 that looks like a credential problem and is not.
                #
                # It also ROTATES: a successful refresh mints a new refresh token
                # and the old one stops being valid. Replaying the cookie captured
                # at seed time therefore measures token rotation, not the endpoint
                # -- measured here as 19 of 25 requests answering 401 "Invalid
                # refresh token." So the cookie is re-read from every response and
                # carried into the next request, which is what a real client does.
                cookies["refresh_token"] = live_refresh[account.username]
        elif endpoint.auth == "anon":
            headers["User-Agent"] = PROBE_HEADERS_BENIGN["User-Agent"]

        if endpoint.content_type is not None:
            headers["Content-Type"] = endpoint.content_type

        # The gate is acquired BEFORE the timer starts, so the pacing wait is
        # never counted as endpoint latency. It is the harness's own budget, not
        # the server's.
        if gate is not None:
            await gate.acquire()

        async with semaphore:
            started = time.perf_counter()
            response = await client.request(
                endpoint.method,
                endpoint.path,
                headers=headers,
                cookies=cookies,
                content=endpoint.body.encode() if endpoint.body is not None else None,
            )
            elapsed = time.perf_counter() - started
        if endpoint.name == "refresh" and response.status_code == 200 and account is not None:
            rotated = response.cookies.get("refresh_token")
            if rotated:
                live_refresh[account.username] = rotated
                rotations += 1
        result.status_counts[response.status_code] = result.status_counts.get(response.status_code, 0) + 1
        if response.status_code not in endpoint.expected_statuses and len(result.unexpected_bodies) < 3:
            # Bound the capture and record WHY it was captured. Reading a body
            # once costs nothing; reading it on all 10000 requests would turn a
            # measurement into a log dump.
            result.unexpected_bodies.append(
                {
                    "endpoint": endpoint.name,
                    "status_code": response.status_code,
                    "expected_statuses": sorted(endpoint.expected_statuses),
                    "body": response.text[:1200],
                    "tenant_id_header": headers.get("X-Tenant-ID"),
                }
            )
        return elapsed

    try:
        for _ in range(repetitions):
            latencies = await asyncio.gather(*(one(index) for index in range(requests_per_repetition)))
            result.per_repetition.append(list(latencies))
    finally:
        # One close for the whole phase, and it has to be here rather than at
        # the end of the happy path: a phase that raises mid-flight would
        # otherwise leak a connection pool, and a leaked pool is the kind of
        # thing that shows up as unexplained slowness three runs later.
        await client.aclose()
    if gate is not None:
        # Recorded so a reader can see the endpoint was measured UNDER its
        # shipped budget rather than being allowed to trip it.
        result.pacing = gate.describe()
        if budget_names is not None:
            # WHICH controls paced it. Without this a reader cannot tell
            # "paced at 5 per 60 s AND 10 per 3600 s, because both bind" from
            # "paced at one number someone chose" -- and the first two runs of
            # this harness answered the wrong question exactly that way.
            result.pacing["controls_enforced"] = budget_names
    if endpoint.name == "refresh":
        # How many times a rotated token was picked up. Zero would mean the
        # cookie was replayed unchanged, which is the 401 storm measured before
        # this was fixed, so the count is how a reader tells the two apart.
        result.refresh_rotations = rotations
    return result


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def _manifest_block(*, base_url: str) -> dict[str, Any]:
    """Criterion (6): the run manifest, or a stated reason there is not one.

    Built here and not left to a later gate checker because the thing criterion
    (6) is FOR is an artefact a reader can check without re-running anything.
    Leaving it to a future step is how it stayed absent for the whole of Step 4.

    ``build_app_host_report()`` is only truthful when a server actually served
    the requests. Over ASGITransport there is no uvicorn, no socket and no HTTP
    implementation in the path, and the report would name one anyway -- both of
    its runtime resolvers (`resolve_event_loop`, `resolve_http_impl`) ask what
    uvicorn's AUTO-SELECTION WOULD resolve to on this host, not what served the
    request. That is a real answer to a different question, so the in-process run
    refuses to emit it rather than emitting a plausible wrong thing. Same defect
    class as protocol.py reporting seconds into microsecond-named fields.

    With ``base_url`` set the report IS emitted, and it is asserted, not merely
    recorded: a runtime that is not uvloop+httptools raises instead of producing
    a manifest that describes a server this campaign never talked to.
    """
    if not base_url:
        return {
            "present": False,
            "reason": (
                "This run drove the app IN-PROCESS over ASGITransport, so there is "
                "no HTTP server, no socket and no HTTP implementation in the request "
                "path. build_app_host_report() resolves what uvicorn's auto-selection "
                "WOULD use on this host, which is a real answer to a different "
                "question, so no manifest was produced rather than one describing a "
                "server that served nothing. Re-run with --base-url pointing at a "
                "real uvicorn to get one."
            ),
            "owner": "scripts/step4_campaign.py --base-url",
        }

    from src.benchmarks.run_manifest import (
        assert_deployment_recorded,
        assert_representative_runtime,
        build_app_host_report,
    )

    report = build_app_host_report()
    # assert_representative_runtime RAISES on a runtime that is not uvloop +
    # httptools; assert_deployment_recorded RAISES on a manifest with a hole in
    # it. Neither is a warning. A run that got here with a non-representative
    # runtime aborts rather than writing a manifest nobody should read.
    report = assert_representative_runtime(report)
    report["deployment"] = assert_deployment_recorded(report["deployment"])
    return {
        "present": True,
        "served_by": base_url,
        "report": report,
    }


async def campaign(args: argparse.Namespace) -> dict[str, Any]:
    from src.app.core.config import settings
    from src.app.core.setup import (
        close_redis_cache_pool,
        create_redis_cache_pool,
        set_threadpool_tokens,
    )

    # Prove the env file actually reached the settings singleton. Without this
    # the failure mode is a silent query against the default database.
    print(f"campaign: db={settings.postgres.POSTGRES_DB} tier={args.tier} users={args.users}")

    await set_threadpool_tokens()
    await create_redis_cache_pool()

    controls_start = read_eight_controls()
    ok, differences = controls_match(controls_start)
    if not ok:
        await close_redis_cache_pool()
        raise RuntimeError(
            "the eight 4.5 controls are not at production values; this phase is void:\n  " + "\n  ".join(differences)
        )

    cleared = await clear_security_state()
    await asyncio.sleep(FLUSHDB_SETTLE_SECONDS)

    accounts, rbac = await seed_accounts(args.users, args.tier, _application())

    print(f"campaign: rbac role={rbac['role_id']} granted={rbac['permissions_granted']}")

    # Purge AGAIN, after seeding. Seeding is harness setup, not measured work,
    # and it spends budget that would otherwise be charged to the first phase:
    # each seeded account logs in for real (the auth budget is IP-keyed at
    # (5, 60)), and every request from this host also touches the anonymous and
    # per-IP aggregate counters. Purging only before seeding left the measured
    # phases starting several requests into a window they are supposed to open --
    # measured here, not inferred: with the purge before seeding alone, six of
    # seven endpoints answered 429 on their first request.
    cleared_after_seeding = await clear_security_state()
    await asyncio.sleep(FLUSHDB_SETTLE_SECONDS)
    print(f"campaign: security keys cleared before={cleared} after={cleared_after_seeding}")

    # Re-open the Redis pool. seed_accounts runs the app inside a
    # LifespanManager and closes it when it returns, and the app's own shutdown
    # calls close_redis_cache_pool(). So without this the measured phases run
    # against an app with NO pool: get_redis() returns None and the GCRA limiter
    # degrades into answering 429. That is what six of seven endpoints were
    # doing. The post-seed purge above did NOT catch this, because
    # clear_security_state() builds its own client from settings.REDIS_CACHE_URL
    # and so kept working on a pool the app could no longer see -- the purge
    # reported success while the app was already blind.
    await create_redis_cache_pool()

    # Resolved here, after load_dotenv(args.env) has run in main(), because the
    # login body embeds the seed credential from the environment.
    selected = _resolve_login_bodies(tuple(e for e in ENDPOINTS if not args.only or e.name in args.only))

    # Assert EM-8's premise before anything is timed, not after. A term that
    # stopped matching the prefilter would otherwise produce a clean-looking
    # artefact describing the wrong regime, which is the failure mode this
    # programme has already paid for twice (protocol.py's seconds, and a
    # manifest naming a server that was not serving).
    if any(ep.name == "users_search" for ep in selected):
        assert_search_term_hits()
        print(f"campaign: EM-8 search term asserted to hit the prefilter ({SEARCH_TERM_KB} KB)")

    # Purge before EVERY phase, not once per run. 4.5 requires it per phase, and
    # the single run-level purge was measurably wrong: the `login` phase spends
    # four of the five-per-minute IP-keyed auth budget, and logout and refresh
    # then inherited a wall they did not create -- measured here as logout
    # answering 200 once then 429 three times, and refresh 429 four times. Each
    # endpoint is measured in isolation, so each gets a clean window. The purge
    # is a targeted SCAN MATCH (clear_security_state) followed by the fixed
    # settle interval, which is the form 4.5 names; a full-keyspace scan
    # immediately before timing would contaminate the first requests.
    results: list[PhaseResult] = []
    for endpoint in selected:
        await clear_security_state()
        await asyncio.sleep(FLUSHDB_SETTLE_SECONDS)
        results.append(
            await drive_phase(
                endpoint,
                accounts,
                requests_per_repetition=args.requests_per_rep,
                repetitions=args.repetitions,
                concurrency=args.concurrency,
                base_url=args.base_url,
                # Resolved from the LIVE controls, not from a constant: the pin
                # above already proved these are the production values, so this
                # returns the number the app is actually enforcing right now.
                budget=(_resolve_rate_budget(endpoint.rate_budget, controls_start) if endpoint.rate_budget else None),
            )
        )

    controls_end = read_eight_controls()
    ok_end, differences_end = controls_match(controls_end)

    manifest_block = _manifest_block(base_url=args.base_url)

    artefact = {
        "artefact": "step4_campaign",
        "generated_by": "scripts/step4_campaign.py",
        "tier": args.tier,
        "declared_configuration": {
            "tier": args.tier,
            "users": args.users,
            "concurrency": args.concurrency,
            "repetitions": args.repetitions,
            "requests_per_repetition": args.requests_per_rep,
            "p99_sample_floor": P99_SAMPLE_FLOOR,
            "composition": (
                ">=5 repetitions feed median-of-medians and min-of-medians for MIN and "
                "p50; p99 is computed once on the POOLED sample (ADR-0022, accepted)"
            ),
        },
        "security_state_cleared": cleared,
        "security_state_cleared_after_seeding": cleared_after_seeding,
        # The seeder purged the IP-keyed auth budget between every seed login
        # (see ``seed_accounts``). Recorded so a reader can never mistake the
        # seeded sessions for ones shaped by production controls -- the
        # MEASURED phases are the only ones that claim that.
        "seed_login_budget_cleared": True,
        # The auth-info cache the seeder invalidates is the app's own, not the
        # purge's: ``security_state.py::SECURITY_KEY_PATTERNS`` covers the
        # rate-limit and ban families and deliberately not ``user:*``.
        "auth_cache_invalidated_per_seeded_username": True,
        "rbac": rbac,
        "settle_seconds": FLUSHDB_SETTLE_SECONDS,
        "controls_at_start": controls_start,
        "controls_at_end": controls_end,
        "controls_unchanged": ok_end,
        "control_differences_at_end": differences_end,
        "endpoints": [result.summary() for result in results],
        # The topology is the FIRST thing a reader needs, because every latency
        # in this artefact is only meaningful against the shape that produced
        # it. A real socket adds a hop that ASGITransport does not have, so the
        # two shapes are NOT comparable and are never presented as if they were.
        "topology": {
            "mode": "socket" if args.base_url else "in-process",
            "base_url": args.base_url or IN_PROCESS_BASE_URL,
            "http_server": (
                "uvicorn, started with no --loop and no --http so both resolve by "
                "auto-selection, which is the configuration Dockerfile's CMD ships"
                if args.base_url
                else "NONE. ASGITransport calls the ASGI app directly: no socket, no "
                "HTTP implementation, no server process."
            ),
            "seeding": (
                "IN-PROCESS over ASGITransport. Seeding is harness setup, not "
                "measured work; only the phases above go over the socket."
            ),
        },
        "manifest": manifest_block,
    }
    await close_redis_cache_pool()
    return artefact


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True, help="path to the env file")
    parser.add_argument("--tier", default="pro")
    parser.add_argument("--users", type=int, default=64)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--requests-per-rep", type=int, default=2000)
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument(
        "--base-url",
        default="",
        help=(
            "drive a real uvicorn over a real socket, e.g. http://127.0.0.1:8010. "
            "Empty (the default) drives the app in-process over ASGITransport, "
            "which has no server and therefore cannot carry a criterion-(6) manifest."
        ),
    )
    parser.add_argument("--out", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    load_dotenv(args.env, override=True)
    artefact = asyncio.run(campaign(args))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(artefact, indent=2, default=str))
    print(json.dumps(artefact["endpoints"], indent=2, default=str))


if __name__ == "__main__":
    main()
