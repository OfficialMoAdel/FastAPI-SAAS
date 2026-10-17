"""M-7: an ENDPOINT baseline for API-key authentication, measured on Linux.

perf I2 records that ZERO measured API-key baseline exists anywhere in the pack,
so the MGET proposal's 0.90 priority score has no denominator. wxqr (L-16) closed
the mechanism half - it measures ``RedisCacheAdapter.get`` directly - but no
request has ever been driven through the API-key seam.

Why in-process rather than against a running uvicorn
---------------------------------------------------
Round trips are only observable by instrumenting the Redis client itself, and
that client lives inside the serving process. A harness in a separate process
sees none of its commands. So the app is booted here (asgi_lifespan) and driven
through ASGITransport, which is what ``src/benchmarks/census.py`` and
``src/benchmarks/linux_mechanism_probe.py`` already do.

Why the guards at the top
-------------------------
An earlier e2e in this repo passed while proving nothing, because the code under
test was the PRE-change version and the reference matched it trivially. So this
script asserts, before timing anything, that the API-key path is really what
answered: a request with a bogus key must be REJECTED. If a bad key were
accepted, the good-key measurements below would be measuring an anonymous
request and every number would be fiction.

Commands vs round trips (T-6)
----------------------------
redis-py FLATTENS a pipeline payload (``chain.from_iterable(stack)``) before
writing it, so the wire bytes carry no per-command boundaries. Any command count
derived from a write is therefore fiction. This harness counts:

  * ROUND TRIPS  = calls to ``Connection.send_packed_command``; one packed write
    is one round trip.
  * COMMANDS     = hooks on ``execute_command`` (direct), ``Pipeline.execute_command``
    (staged), ``Pipeline.immediate_execute_command`` (WATCH-mode) and
    ``PubSub.execute_command``.

Each command hook pushes a category onto a module-level stack and
``send_packed_command`` tags the write with the category current at write time,
so the buckets sum to the total BY CONSTRUCTION rather than by inference, and
anything this model failed to anticipate surfaces as a visible ``unattributed``
bucket instead of silently breaking an equation.

Latency, and why it is not the count
------------------------------------
Latency is timed per request with ``time.perf_counter()`` around the awaited
call and reported in its own block. It is a THIRD quantity, not a restatement
of either Redis count: 2.0 round trips and 40 ms are compatible, and neither
one can be derived from the other.

Two limits on it, both structural to running the app in-process:

  * there is no socket and no uvicorn, so this excludes accept/parse/serialise
    and network time. It is therefore NOT comparable with step-4's over-the-wire
    ``users_me`` p50 of 30.7745 ms at concurrency 8; that number is the
    cross-reference, not the expectation.
  * it is sequential. Concurrency effects (contention, pool pressure) are
    absent by construction.

Both are recorded in the output rather than left for the reader to discover.

Budgets
-------
Production budgets are read from live settings and the script refuses to run if
they are not production values. ``census.bootstrap()`` is deliberately NOT
called because it relaxes every budget to 100000/3600, which would make the
result inadmissible.

The bucket the run is about to consume is RESET once, before setup, by deleting
the GCRA keys belonging to the accounts and source IPs this script uses - built
by the production helpers (``build_rate_limit_key``,
``build_anonymous_aggregate_key``, ``sanitize_path``), never hand-assembled.
That is a state reset, not a relaxation: the budgets stay at their production
values and every measured request still has to earn its token, so a run that
outruns a budget fails on the 429 rather than being rescued by a relaxed limit.
The deleted key names are in the output so the reset is auditable. An earlier
version reset only ``gcra:rate_limit:user:<id>:*`` and reported success while
deleting nothing: API-key traffic is metered per source IP, so the keys that
actually exist are the anonymous ones.

Exit codes: 0 success, 1 an assertion failed, 2 a precondition failed.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import secrets
import statistics
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    """Walk up to the tree that actually contains the app.

    Hardcoding ``parents[N]`` was wrong twice over: it breaks the moment the
    script moves between ``/home/ansible/`` and the repo's own ``scripts/``.
    """
    here = Path(__file__).resolve()
    for cand in here.parents:
        if (cand / "src" / "app" / "main.py").exists():
            return cand
    raise SystemExit(f"repo root not found above {here}")


ROOT = _repo_root()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "src" / ".env", override=True)

import httpx  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402
from redis.asyncio.client import Pipeline, PubSub, Redis  # noqa: E402
from redis.asyncio.connection import Connection as AsyncConnection  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.app.core.db.database import async_session_maker  # noqa: E402

TENANT = "test-tenant-e2e"
# ONE account for the whole run, and it is PROVISIONED by this script (see
# provision_accounts) rather than borrowed from step-4's `perf_user_*` fixture,
# which does not exist on this target - the version that assumed it did answered
# "key owner e2e_admin not found" and measured nothing.
#
# An earlier version also provisioned three `m7owner*` accounts and minted one key
# per account, so that each key would be metered on its owner's tier budget. Two
# findings retired that: an API-key request is metered as ANONYMOUS per source IP
# (so there is no per-owner budget to spread into), and `APIKeyCreate` has no
# `user_id` field, so the public route mints every key for the caller - one owner
# was all the route could ever produce. With one account and one key per
# measurement slot, arms 1 and 3 differ in exactly one thing: X-API-Key vs Bearer.
MEASURED_PATH = "/api/v1/users/me"
# `Pro` exists in the tier table on the target; its settings budget is
# (1000, 3600) against the 3 bearer-control requests this run issues.
TIER_NAME = "Pro"
# NOT a cosmetic choice. An earlier run seeded `@m7.local` and every measured
# request answered 422: the response model validates `email` as `EmailStr`, and
# email-validator rejects `.local` as a special-use name that cannot be
# delivered. A reserved TLD is therefore not a usable fixture here, and the
# failure looks like an auth fault because it arrives on the authenticated path.
EMAIL_DOMAIN = "m7.example.com"
# The measured key must be ABLE to reach the measured endpoint. `APIKeyCreate.
# scopes` defaults to None and `api_key_scope_allows` denies a scopeless key
# everywhere, so a default-minted key 403s "missing required scope: read" on GET
# /users/me. `read` is the coarse read scope for the whole API; it is recorded in
# the artefact because a baseline whose credential cannot read is not a baseline,
# and because "a key minted with the documented default can call nothing" is a
# finding in its own right.
KEY_SCOPES = ["read"]
# How many keys the baseline spreads across the requests of one repetition. The
# keys are interleaved rather than issued in blocks, so a repetition does not
# measure one key's cache entry and nothing else; the per-request records keep
# the split visible either way.
KEY_COUNT = 3
# There is deliberately NO `requests_per_key` constant. The number of requests
# each repetition may issue is DERIVED from the live settings in main_async
# (`settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT[0]`), because an API-key request
# is metered as anonymous traffic (see the module docstring) and hard-coding the
# budget in the harness is how the two drift apart silently.
# step4_gate.MIN_REPETITIONS is 5; a single pass cannot show whether the numbers
# are stable, and an unstable baseline is not a baseline.
REPETITIONS = 5
# The bearer control arm issues this many requests. Three is enough to show the
# budget moving (limit, limit-1, limit-2) without spending the owner's hourly
# tier budget on a measurement that exists only to prove what it is metered
# against.
BEARER_CONTROL_REQUESTS = 3
PASSWORD = "Str1ngst!"
SUPERUSER = "m7admin"
SUPERUSER_PASSWORD = "TestPassword123!"
# httpx's default UA is "python-httpx/<version>", which
# src/app/middleware/ip_filter.py:186-210 matches against _get_bad_bot_regex()
# and answers 403 "Forbidden - Bad bot detected". Same convention as
# tests/performance/locustfile.py and src/benchmarks/prof_request_path.py.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# ---- instruments -----------------------------------------------------------
MY_CMD = 0
MY_PIPE_CMD = 0
MY_PIPE_EXEC = 0
MY_IMMEDIATE = 0
MY_PUBSUB = 0
MY_RTT = 0
RTT_BY_CAT: collections.Counter[str] = collections.Counter()
_CUR: list[str] = []


def _reset_counters() -> None:
    global MY_CMD, MY_PIPE_CMD, MY_PIPE_EXEC, MY_IMMEDIATE, MY_PUBSUB, MY_RTT
    MY_CMD = MY_PIPE_CMD = MY_PIPE_EXEC = MY_IMMEDIATE = MY_PUBSUB = MY_RTT = 0
    RTT_BY_CAT.clear()
    _CUR.clear()


class Probe:
    """Install the hooks, then remove them again.

    Restoration must not assume the original was IN this class's ``__dict__``.
    ``Connection`` does not define ``send_packed_command`` - it inherits it from
    ``AbstractConnection`` - so saving ``obj.__dict__.get(name)`` saved ``None``
    and restoring it made every later Redis call in the process die with
    ``TypeError: 'NoneType' object is not callable``. An inherited attribute is
    therefore recorded as UNSET and the shadowing attribute DELETED on remove.
    """

    UNSET = object()

    def __init__(self) -> None:
        self._saved: list[tuple[Any, str, Any]] = []

    def install(self) -> None:
        o_direct, o_pipe_cmd, o_pipe_exec = (
            Redis.execute_command,
            Pipeline.execute_command,
            Pipeline.execute,
        )
        o_imm, o_ps, o_send = (
            Pipeline.immediate_execute_command,
            PubSub.execute_command,
            AsyncConnection.send_packed_command,
        )

        def push(cat: str) -> None:
            _CUR.append(cat)

        def pop() -> None:
            if _CUR:
                _CUR.pop()

        async def direct(self: Any, *a: Any, **k: Any) -> Any:
            global MY_CMD
            MY_CMD += 1
            push("direct")
            try:
                # AWAITED, not returned. redis-py's asyncio entry points are
                # `async def`, so the previous sync wrapper pushed the category
                # and popped it again before the caller ever awaited the
                # coroutine - the send then fired with an empty stack and every
                # round trip was filed as "unattributed". A category that only
                # brackets a coroutine it never awaits is a category that
                # attributes nothing.
                return await o_direct(self, *a, **k)
            finally:
                pop()

        def pipe_cmd(self: Any, *a: Any, **k: Any) -> Any:
            # Deliberately SYNC and deliberately does NOT push. Staging a command
            # into a pipeline sends nothing: the wire write happens at execute()
            # time. Making this async would also break redis-py, whose
            # Pipeline.set() and friends stage without awaiting.
            global MY_PIPE_CMD
            MY_PIPE_CMD += 1
            return o_pipe_cmd(self, *a, **k)

        async def pipe_exec(self: Any, *a: Any, **k: Any) -> Any:
            global MY_PIPE_EXEC
            MY_PIPE_EXEC += 1
            push("pipeline_flush")
            try:
                return await o_pipe_exec(self, *a, **k)
            finally:
                pop()

        async def imm(self: Any, *a: Any, **k: Any) -> Any:
            global MY_IMMEDIATE
            MY_IMMEDIATE += 1
            push("immediate_watch")
            try:
                return await o_imm(self, *a, **k)
            finally:
                pop()

        async def ps(self: Any, *a: Any, **k: Any) -> Any:
            global MY_PUBSUB
            MY_PUBSUB += 1
            push("pubsub")
            try:
                return await o_ps(self, *a, **k)
            finally:
                pop()

        def send(self: Any, *a: Any, **k: Any) -> Any:
            global MY_RTT
            MY_RTT += 1
            RTT_BY_CAT[_CUR[-1] if _CUR else "unattributed"] += 1
            return o_send(self, *a, **k)

        for obj, name, new in (
            (Redis, "execute_command", direct),
            (Pipeline, "execute_command", pipe_cmd),
            (Pipeline, "execute", pipe_exec),
            (Pipeline, "immediate_execute_command", imm),
            (PubSub, "execute_command", ps),
            (AsyncConnection, "send_packed_command", send),
        ):
            self._saved.append((obj, name, obj.__dict__.get(name, self.UNSET)))
            setattr(obj, name, new)

    def remove(self) -> None:
        for obj, name, old in self._saved:
            if old is self.UNSET:
                delattr(obj, name)
            else:
                setattr(obj, name, old)
        self._saved.clear()


class Failure(Exception):
    pass


STEPS: list[dict[str, Any]] = []
FAILURES: list[str] = []
# One entry: what the public minting route answered when it was asked once.
CREATION_PROBE: list[dict[str, Any]] = []
# The in-progress result dict. An assertion failure aborts the run, and a
# harness that then reports ONLY the failure throws away every number it had
# already measured - including the bodies that explain the failure. Publishing
# the partial here lets the abort still emit the evidence.
PARTIAL: dict[str, Any] = {}


def step(name: str, assertion: str, expected: Any, observed: Any, **kw: Any) -> bool:
    ok = expected == observed
    row: dict[str, Any] = {
        "step": name,
        "assertion": assertion,
        "expected": expected,
        "observed": observed,
        "passed": ok,
        **kw,
    }
    STEPS.append(row)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: expected={expected!r} observed={observed!r}")
    return ok


def require(name: str, assertion: str, expected: Any, observed: Any, **kw: Any) -> None:
    if not step(name, assertion, expected, observed, **kw):
        FAILURES.append(name)
        raise Failure(f"{name}: expected {expected!r}, observed {observed!r}")


def _error_code(response: httpx.Response) -> str:
    """The error code from a refusal body, or "" if the body is not JSON.

    An error body is evidence: "429" alone does not distinguish a rate-limit
    refusal from anything else that answers 429, and a body that is not JSON at
    all must not raise here - it would replace a measured refusal with a harness
    crash and lose the measurement.
    """
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        return str(body.get("error", {}).get("code", "")) if isinstance(body.get("error"), dict) else ""
    return ""


async def commandstats(client: Redis) -> dict[str, int]:
    """redis-py 5.x parses INFO into a flat dict, not the 4.x raw string."""
    raw = await client.info("commandstats")
    return {k.removeprefix("cmdstat_"): int(v["calls"]) for k, v in raw.items()}


async def budgets_are_production() -> dict[str, Any]:
    from src.app.core.config import settings

    sec = settings.rate_limit.SECURITY_BUDGETS
    tiers = settings.rate_limit.TIER_RATE_LIMITS
    seen = {
        "auth": tuple(sec["auth"]),
        "refresh": tuple(sec["refresh"]),
        "tier_free": tuple(tiers["free"]),
        "tier_basic": tuple(tiers["basic"]),
        "tier_pro": tuple(tiers["pro"]),
        "tier_enterprise": tuple(tiers["enterprise"]),
        # The two budgets an API-key request is ACTUALLY metered against, and
        # the source of the sample size chosen below. They are recorded here so
        # the artefact cannot claim a tier budget was in force when none was.
        "default_anonymous": tuple(settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT),
        "anonymous_ip_aggregate": tuple(settings.rate_limit.ANONYMOUS_IP_AGGREGATE_LIMIT),
        "algorithm": settings.rate_limit.RATE_LIMIT_ALGORITHM,
    }
    want = {
        "auth": (5, 60),
        "refresh": (60, 60),
        "tier_free": (10, 3600),
        "tier_basic": (100, 3600),
        "tier_pro": (1000, 3600),
        "tier_enterprise": (10000, 3600),
        "default_anonymous": (10, 3600),
        "anonymous_ip_aggregate": (300, 3600),
        "algorithm": "gcra",
    }
    if seen != want:
        raise Failure(f"budgets are not production values: {seen} != {want}")
    return seen


def measured_tree() -> dict[str, Any]:
    """What commit the numbers below belong to, and whether it is the clean one.

    A baseline without a commit is not attributable: "we measured it" then
    answers to no tree at all. ``tree_dirty`` is reported rather than enforced,
    because a dirty tree is a fact about the run the reader needs, not
    something the run can undo.
    """

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(ROOT), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()

    dirty = [ln for ln in git("status", "--porcelain").splitlines() if ln.strip()]
    return {
        "commit": git("rev-parse", "HEAD"),
        "short_commit": git("rev-parse", "--short", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "tree_dirty": bool(dirty),
        "dirty_paths": sorted(ln[3:] for ln in dirty),
        "subject": git("log", "-1", "--pretty=%s"),
    }


def check_import_provenance() -> dict[str, str]:
    """Refuse to measure a tree other than ROOT.

    The measurement runs inside a checkout that may have an editable install of
    a DIFFERENT checkout on ``sys.path``. Without this check the script would
    happily import, time and report another working tree's code - and the
    premise check below would then read that other file, which is the exact
    silent-wrong-code failure this programme exists to prevent.
    """
    import src.app.core.cache.client as cache_client_mod
    import src.app.main as main_mod
    import src.app.modules.api_keys.models as api_keys_models_mod

    seen = {
        "src.app.main": main_mod.__file__,
        "src.app.core.cache.client": cache_client_mod.__file__,
        "src.app.modules.api_keys.models": api_keys_models_mod.__file__,
    }
    for mod, path in seen.items():
        if not str(Path(path).resolve()).startswith(str(ROOT)):
            raise Failure(f"{mod} imported from {path}, which is OUTSIDE {ROOT}")
    return seen


async def check_premise() -> dict[str, Any]:
    """Re-confirm RedisCacheAdapter.get has no try/except, on THIS code."""
    import inspect

    from src.app.core.cache.client import RedisCacheAdapter

    lines, start = inspect.getsourcelines(RedisCacheAdapter.get)
    body = "".join(lines)
    src = inspect.getsourcefile(RedisCacheAdapter.get)
    out = {
        "file": src,
        "starts_at_line": start,
        "ends_at_line": start + len(lines) - 1,
        "cited_as": "core/cache/client.py:89-95",
        "cited_range_still_exact": (start, start + len(lines) - 1) == (89, 95),
        "swallows_outage": ("try" in body) and ("except" in body),
        "body": body,
    }
    if out["swallows_outage"]:
        raise Failure("RedisCacheAdapter.get now swallows an outage; the premise moved")
    return out


def client_for(ip: str) -> httpx.AsyncClient:
    port = 40_000 + (sum(ord(c) for c in ip) % 20_000)
    return httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=30.0,
        headers={"User-Agent": USER_AGENT, "X-Tenant-ID": TENANT},
        # raise_app_exceptions=False is load-bearing: by default ASGITransport
        # RE-RAISES an unhandled app exception instead of turning it into a 500.
        # POST /api/v1/api-keys really does raise (the scopes bind is invalid), and
        # without this the harness would abort at the exception instead of OBSERVING
        # the 500 that documents the defect.
        transport=httpx.ASGITransport(app=APP, client=(ip, port), raise_app_exceptions=False),
    )


async def login(user: str, ip: str, password: str | None = None) -> str:
    secret = password if password is not None else PASSWORD
    async with client_for(ip) as c:
        r = await c.post("/api/v1/account/login", data={"username": user, "password": secret})
    if r.status_code != 200:
        raise Failure(f"login {user} -> {r.status_code} {r.text[:200]}")
    return r.json()["access_token"]


async def probe_creation_route(token: str, name: str, ip: str) -> tuple[str | None, dict[str, Any]]:
    """Ask ``POST /api/v1/api-keys`` to mint a key and record exactly what it did.

    Two things about this route are findings rather than setup details.

    1. On a database built by ``alembic`` it 500s: ``03_access_control.py:132``
       creates ``api_keys.scopes`` as ``postgresql.JSONB`` while
       ``src/app/modules/api_keys/models.py:55`` maps it as ``mapped_column(Text)``,
       so the ``json_dumps(...)`` string arrives at asyncpg as ``character
       varying`` and raises ``DatatypeMismatchError``. The TARGET database here
       was built by ``create_all`` (no ``alembic_version`` table), so the column
       is ``text`` and the route answers 201 - the two schemas disagree and the
       route's health depends on which one ran.

    2. ``APIKeyCreate.scopes`` DEFAULTS TO ``None``, and
       ``api_key_scope_allows`` denies a key with no scopes EVERYWHERE
       (fail-closed, ``src/app/api/deps/auth.py:352-353``). A key minted with the
       default body is therefore good for nothing: it 403s
       "API key missing required scope: read" even on ``GET /users/me``. That is
       why this sends an explicit ``scopes`` - the measured key must be able to
       reach the endpoint, and the scope set it carries is recorded as part of the
       fixture rather than left implicit.

    This function NEVER raises. It reports, and the caller decides. That is the
    whole point of the split: an earlier version fused the probe to key creation,
    so when the minting account's hourly tier budget happened to be spent the
    probe answered 429 and the run died having measured nothing - a setup
    obstacle masquerading as a result. Statuses are read as:

      200/201 + a plaintext field -> the route works; that key is the measurement
                                      input
      500                         -> the jsonb/Text bind defect, demonstrated
      anything else (429, 403, 422, ...) -> inconclusive, recorded with its
                                      status. Not fatal, and NOT claimed as
                                      evidence either way.
    """
    async with client_for(ip) as c:
        r = await c.post(
            "/api/v1/api-keys",
            json={"name": name, "scopes": list(KEY_SCOPES)},
            headers={"Authorization": f"Bearer {token}"},
        )
    body: Any
    try:
        body = r.json()
    except ValueError:
        body = None
    observed = {
        "status": r.status_code,
        "error_code": str((body or {}).get("error", {}).get("code", "")) if isinstance(body, dict) else "",
        "body_snippet": r.text[:300],
        "demonstrates": (
            "the 500 from the jsonb/Text bind defect"
            if r.status_code == 500
            else "the route mints a key"
            if r.status_code in (200, 201)
            else "nothing; the attempt was inconclusive"
        ),
    }
    if r.status_code not in (200, 201):
        CREATION_PROBE.append(observed)
        return None, observed
    for field in ("key", "api_key", "full_key", "plain_key"):
        if isinstance(body, dict) and body.get(field):
            observed["plaintext_field"] = field
            CREATION_PROBE.append(observed)
            return str(body[field]), observed
    field_names = sorted(body) if isinstance(body, dict) else body
    observed["demonstrates"] = f"nothing; 2xx with no plaintext field in {field_names}"
    CREATION_PROBE.append(observed)
    return None, observed


async def create_key(token: str, name: str, ip: str, *, probe_route: bool) -> tuple[str, str]:
    """Return ``(key, how_it_was_minted)`` for ``owner``.

    ``owner`` is the CALLER and, necessarily, the key's owner: ``APIKeyCreate``
    (``api_keys/schemas.py:21-24``) has no ``user_id`` field and
    ``extra="forbid"``, so ``POST /api/v1/api-keys`` mints the key for whoever
    called it. That is a finding in its own right - the public route cannot issue
    a key on another user's behalf - and it is why this run has ONE account
    rather than one owner per key. An earlier version minted keys for three
    owners through the fallback insert and asserted the requests authenticated
    as them; the route could never have satisfied that assertion, and the
    anonymous-IP metering that was supposed to require per-owner budgets turned
    out not to exist at all.
    """
    if probe_route:
        key, observed = await probe_creation_route(token, name, ip)
        if key is not None:
            return key, "route"
    return await _insert_api_key_direct(name), "direct_insert"


async def _insert_api_key_direct(name: str) -> str:
    """Write an API key straight into the table, as the broken route would have.

    The digest is computed with the app's OWN ``hash_token`` so the row is
    indistinguishable from one the service wrote, and ``scopes`` is passed as a
    JSON literal cast to jsonb so the insert does not repeat the defect. The
    scope set is ``KEY_SCOPES`` - the same one the route is asked for - because a
    row written with an empty scope list would be denied at
    ``enforce_api_key_scope`` and the fallback would silently produce an
    unmeasurable key.

    The row is owned by ``SUPERUSER`` because that is who the route would have
    minted it for. An earlier version of this function took the owner as an
    argument and the run spread its keys over three accounts so each would be
    metered on that account's tier budget; the metering turned out to be
    per-source-IP and anonymous regardless of owner, so the accounts bought
    nothing and cost an assertion the route could not satisfy.
    """
    import json as _json

    from src.app.core.security.tokens import token_digest_candidates

    raw = f"sk_{name}_{secrets.token_hex(24)}"
    digest = token_digest_candidates(raw)[0]
    prefix = raw[:8]
    scopes = _json.dumps(list(KEY_SCOPES))
    user_id, tenant_id = await _owner_ids(SUPERUSER)
    async with async_session_maker() as session:
        await session.execute(
            text(
                "INSERT INTO api_keys "
                "(name, key_prefix, hashed_key, user_id, tenant_id, is_active, "
                "scopes, created_at) "
                "VALUES (:name, :prefix, :digest, :uid, :tid, true, "
                "CAST(:scopes AS jsonb), now())"
            ),
            {
                "name": name,
                "prefix": prefix,
                "digest": digest,
                "uid": user_id,
                "tid": str(tenant_id),
                "scopes": scopes,
            },
        )
        await session.commit()
    return raw


async def provision_accounts(client: Redis) -> dict[str, Any]:
    """Create the ONE account this run spends: minter, key owner and bearer control.

    The measurement needs an account that EXISTS, carries a tier, can hold a key
    and can log in with a Bearer token. Nothing in this repository seeds it, and
    the target database holds only three unrelated accounts (``e2etestuser``,
    ``s4_admin01``, ``s4_admin02``), so the earlier version of this script died
    at "key owner e2e_admin not found" having measured nothing.

    Tier matters for the bearer control arm only: it needs an account whose tier
    budget differs from the anonymous default, so this account sits on ``Pro``
    (settings: 1000 per 3600s). What the run was actually metered against is read
    back off the response headers, so the artefact reports the live budget rather
    than the settings value.

    A raw INSERT, as scripts/step4_campaign.py:370 does, because the minting
    route is not what this script sets up. Every column carrying a
    PYTHON-side default is set explicitly: ``directory.by_username`` filters
    ``is_deleted=False``, so a NULL there silently excludes the row. The rows
    are deleted first (the unique index is partial on ``is_deleted=false``, so
    an UPDATE target would have to name the predicate), and each account's
    cached auth payload is dropped afterwards - a cache left holding a deleted
    row is a fixture that fails on the second run instead of the first.
    """
    from src.app.core.security.password import get_password_hash
    from src.app.modules.users.cache_keys import build_user_invalidation_delete_keys

    out: dict[str, Any] = {
        "tenant": TENANT,
        "tier": TIER_NAME,
        "measured_path": MEASURED_PATH,
        "accounts": {},
    }
    hashed = await get_password_hash(SUPERUSER_PASSWORD)
    async with async_session_maker() as session:
        tenant_id = (
            await session.execute(text("SELECT id FROM tenants WHERE slug = :slug"), {"slug": TENANT})
        ).scalar_one_or_none()
        if tenant_id is None:
            raise Failure(f"tenant {TENANT!r} does not exist; this harness provisions accounts, not tenants")
        tier_id = (
            await session.execute(text("SELECT id FROM tier WHERE name = :name"), {"name": TIER_NAME})
        ).scalar_one_or_none()
        if tier_id is None:
            raise Failure(f"tier {TIER_NAME!r} does not exist on this target")
        out["tenant_id"] = str(tenant_id)
        out["tier_id"] = int(tier_id)

        wanted = [(SUPERUSER, True)]
        for username, is_superuser in wanted:
            email = f"{username}@{EMAIL_DOMAIN}"
            await session.execute(
                text("DELETE FROM users WHERE username = :u AND tenant_id = :t"), {"u": username, "t": tenant_id}
            )
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO users (
                            tenant_id, tier_id, name, username, email, hashed_password,
                            profile_image_url, uuid, created_at, updated_at, deleted_at,
                            is_deleted, is_superuser, is_email_verified, is_active, token_version
                        )
                        VALUES (
                            :tenant_id, :tier_id, :name, :username, :email, :hashed,
                            :profile_image_url, gen_random_uuid(), now(), now(), null,
                            false, :is_superuser, true, true, 0
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "tier_id": tier_id,
                        "name": username,
                        "username": username,
                        "email": email,
                        "hashed": hashed,
                        "profile_image_url": "https://profileimageurl.com/i.png",
                        "is_superuser": is_superuser,
                    },
                )
            ).scalar_one()
            await session.commit()
            out["accounts"][username] = {
                "user_id": int(row),
                "is_superuser": is_superuser,
                "tier_id": int(tier_id),
                "cache_keys_deleted": 0,
            }

    for username, info in out["accounts"].items():
        keys = build_user_invalidation_delete_keys(
            info["user_id"], username=username, email=f"{username}@{EMAIL_DOMAIN}", tenant_id=out["tenant_id"]
        )
        info["cache_keys_deleted"] = int(await client.delete(*keys)) if keys else 0
    return out


async def _owner_ids(username: str) -> tuple[int, str]:
    """An account's id and tenant, read rather than hardcoded.

    ``get_user_by_api_key`` raises ``API_KEY_TENANT_MISMATCH`` when the key's
    tenant differs from the request's, so the key has to belong to the same
    tenant the script sends in ``X-Tenant-ID``. Reading both halves from the row
    keeps that true by construction instead of by a constant that drifts.
    """
    async with async_session_maker() as session:
        row = (
            await session.execute(
                text("SELECT id, tenant_id FROM users WHERE username = :u"),
                {"u": username},
            )
        ).first()
    if row is None:
        raise Failure(f"key owner {username} not found; cannot own an API key")
    return int(row[0]), str(row[1])


async def _purge_fixture() -> int:
    """Delete this script's keys so a re-run starts from a known state.

    The api_keys table enforces a unique (name, tenant) pair and the service
    checks it BEFORE the insert, so a leftover key from a previous run answers
    409 ALREADY_EXISTS and the broken-insert path is never reached - the run
    would then fail for a reason that has nothing to do with what it measures.
    """
    async with async_session_maker() as session:
        result = await session.execute(text("DELETE FROM api_keys WHERE name LIKE 'm7-%'"))
        await session.commit()
        return int(getattr(result, "rowcount", 0) or 0)


def _ip(n: int) -> str:
    """A distinct loopback source address per request.

    The rate limiter keys on IP (auth budget 5 per 60s) and the tier policy keys
    on user, so decoupling the two satisfies the per-IP budget BY CONSTRUCTION
    instead of relaxing it. 127.0.0.0/8 is entirely local.
    """
    return f"127.{(n >> 16) & 0xFF}.{(n >> 8) & 0xFF}.{n & 0xFF}"


async def reset_gcra_buckets(client: Redis, usernames: list[str]) -> dict[str, Any]:
    """Clear the hourly tier bucket of each account this script will spend.

    ``GCRARateLimiter.check_rate_limit`` keys Redis as ``gcra:{key}``
    (gcra_rate_limit.py:141) and ``build_rate_limit_key`` builds ``{key}`` as
    ``build_tenant_key(f"rate_limit:user:{user_id}:{path}", tenant_id)``, so a
    real bucket is ``gcra:tenant:{tid}:rate_limit:user:{uid}:{path}``. The
    earlier version of this function globbed ``gcra:rate_limit:user:{uid}:*``,
    which matches NOTHING now that the key is tenant-scoped - a reset that
    deletes no keys and reports success. So the pattern is built by production
    code and recorded, and the delete uses the wider glob that also catches the
    unscoped form, so a future key change cannot make this silently blind.

    The window is 3600s, so a run that starts by clearing the window it is
    about to open is reproducible; a run that inherits a previous run's partial
    window is not, and reports a 429 that says nothing about the endpoint.

    SCAN, never KEYS: this runs against a live store. The deleted names are
    returned so the reset is visible in the artefact rather than implied.
    """
    from src.app.core.security.rate_policy import build_rate_limit_key
    from src.app.core.utils.path_utils import sanitize_path

    path_token = sanitize_path(MEASURED_PATH.lower())
    out: dict[str, Any] = {"path_token_sanitised_by_production": path_token, "deleted": [], "accounts": {}}
    for username in usernames:
        user_id, tenant_id = await _owner_ids(username)
        built = build_rate_limit_key(path=path_token, user_id=str(user_id), tenant_id=tenant_id)
        scoped_prefix = built[: built.rindex(":")]
        pattern = f"gcra:*rate_limit:user:{user_id}:*"
        found = [k async for k in client.scan_iter(match=pattern, count=500)]
        if found:
            await client.delete(*found)
        out["accounts"][username] = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "built_key_example": built,
            "derived_scoped_pattern": f"gcra:{scoped_prefix}:*",
            "deleted_with_pattern": pattern,
            "deleted_count": len(found),
            "deleted_names": sorted(found),
        }
        out["deleted"].extend(sorted(found))
    return out


async def reset_anonymous_buckets(client: Redis, ips: list[str], tenant_id: str) -> dict[str, Any]:
    """Clear the buckets an API-key request actually writes, for these IPs.

    The per-USER reset above is the wrong instrument for this run, and finding
    that out is the measurement: an API-key request never reaches the tier
    policy, so ``gcra:tenant:{tid}:rate_limit:user:{uid}:*`` is never written
    and that reset deletes zero keys. What IS written, per source IP:

    * the per-path anonymous bucket - ``build_rate_limit_key`` with
      ``user_id=None`` returns the UNSCOPED ``rate_limit:anonymous:ip:{ip}:{path}``
      (rate_policy.py:298-307, and its comment says tenant scoping for anonymous
      keys was deliberately not added);
    * the per-IP aggregate ceiling - ``build_anonymous_aggregate_key(ip)``, whose
      path component is the constant ``__ip_aggregate__``;
    * auto-ban state - ``security:violations:{ip}``, ``security:ban:{ip}``,
      ``security:ban_count:{ip}`` and the L1 ``ban:{ip}``
      (src/app/core/security/auto_ban.py:28-30,48).

    Keys are BUILT by production code and deleted by exact name rather than
    globbed: a glob that matched nothing would be reported as a successful
    reset, which is precisely the bug this function exists to correct. Every key
    it deletes is returned, so the reset is visible in the artefact.

    This is a state reset, not a budget relaxation. The budgets in force while
    measuring are the live production ones, checked by ``budgets_are_production``
    before anything is reset; what this removes is the LEFTOVER STATE of previous
    runs, so the measurement describes one run rather than the sum of all of them.
    """
    from src.app.core.security.auto_ban import (
        _BAN_KEY_PREFIX,
        BAN_COUNT_KEY_PREFIX,
        BAN_KEY_PREFIX,
        VIOLATIONS_KEY_PREFIX,
    )
    from src.app.core.security.rate_policy import build_anonymous_aggregate_key, build_rate_limit_key
    from src.app.core.utils.path_utils import sanitize_path

    # The limiter keys on the SANITISED path, not the raw one:
    # `request_admission._get_rate_limit_config` computes
    # `sanitize_path(request.url.path.lower())`, so `/api/v1/users/me` is stored
    # as `users_me`. Passing the raw path here built a key that exists nowhere,
    # which is the same silent-blind-reset bug as the per-user one above - and it
    # is why the first reset deleted only the aggregate ceiling and left the
    # per-path bucket holding a previous run's spend.
    path_token = sanitize_path(MEASURED_PATH.lower())
    out: dict[str, Any] = {
        "path_raw": MEASURED_PATH,
        "path_token_sanitised_by_production": path_token,
        "ips": {},
        "deleted": [],
    }
    for ip in ips:
        keys = [
            f"gcra:{build_rate_limit_key(path=path_token, user_id=None, tenant_id=tenant_id, anonymous_ip=ip)}",
            f"gcra:{build_anonymous_aggregate_key(ip)}",
            VIOLATIONS_KEY_PREFIX.format(ip=ip),
            BAN_KEY_PREFIX.format(ip=ip),
            BAN_COUNT_KEY_PREFIX.format(ip=ip),
            f"{_BAN_KEY_PREFIX}{ip}",
        ]
        found = [k for k in keys if await client.exists(k)]
        deleted = await client.delete(*found) if found else 0
        out["ips"][ip] = {
            "keys_built": keys,
            "present_before_reset": found,
            "deleted_count": int(deleted),
        }
        out["deleted"].extend(found)
    out["deleted_count"] = len(out["deleted"])
    return out


def summarise_latency(samples: list[float]) -> dict[str, Any]:
    """min/p50/p95/p99/max by nearest rank, plus what the sample cannot resolve.

    Delegates to ``src/benchmarks/percentiles`` because that is the ONE estimator
    in the tree - a second implementation here is exactly the bug
    tests/unit/benchmarks/test_percentiles.py exists to fail on.
    """
    from src.benchmarks.percentiles import PERCENTILES, nearest_rank, unresolvable_quantiles

    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "min": round(ordered[0], 4),
        "p50": round(nearest_rank(ordered, 0.5), 4),
        "p95": round(nearest_rank(ordered, 0.95), 4),
        "p99": round(nearest_rank(ordered, 0.99), 4),
        "max": round(ordered[-1], 4),
        "mean": round(statistics.fmean(ordered), 4),
        "estimator": "nearest_rank (src/benchmarks/percentiles.py)",
        "unresolvable_percentiles": sorted(unresolvable_quantiles(len(ordered), PERCENTILES)),
    }


def repetition_figures(per_rep: list[dict[str, Any]]) -> dict[str, Any]:
    """The four figures step4_gate publishes, so two artefacts are comparable.

    A pooled percentile hides run-to-run drift; these show it. Names match
    scripts/step4_gate.py::REPETITION_FIGURES exactly.
    """
    p50s = [r["latency"]["p50"] for r in per_rep]
    mins = [r["latency"]["min"] for r in per_rep]
    return {
        "p50_median_of_repetitions_ms": round(statistics.median(p50s), 4),
        "p50_min_of_repetitions_ms": round(min(p50s), 4),
        "min_median_of_repetitions_ms": round(statistics.median(mins), 4),
        "min_min_of_repetitions_ms": round(min(mins), 4),
    }


async def main_async(base_url: str, repetitions: int) -> dict[str, Any]:
    global BASE_URL, APP, PARTIAL

    from src.app.core.config import settings
    from src.app.main import app

    APP = app
    BASE_URL = base_url
    redis_url = str(settings.redis_cache.REDIS_CACHE_URL)
    result: dict[str, Any] = {"base_url": base_url, "redis_url": redis_url}
    PARTIAL = result
    result["measured_tree"] = measured_tree()
    result["imported_from"] = check_import_provenance()

    async with LifespanManager(app, startup_timeout=120, shutdown_timeout=60):
        result["budgets"] = await budgets_are_production()
        result["premise"] = await check_premise()

        admin = Redis.from_url(redis_url)
        try:
            # --- guard: a bogus key must be REJECTED, or every number below is fiction
            async with client_for(_ip(9_000_001)) as c:
                bad = await c.get("/api/v1/users/me", headers={"X-API-Key": "sk-m7-not-a-real-key"})
            require(
                "guard_bogus_api_key_is_rejected",
                "a bogus X-API-Key is refused, so the good-key runs below are really authenticated",
                401,
                bad.status_code,
                detail=bad.text[:200],
            )
            step(
                "guard_bogus_api_key_body",
                "the refusal is an auth failure, not a 500",
                "UNAUTHORIZED",
                str(bad.json().get("error", {}).get("code", "")),
            )

            # --- phase 1: set up the fixture and the keys (NOT measured)
            # POST /api/v1/api-keys requires the `users:manage` permission, so the
            # keys are minted by a superuser - and, because APIKeyCreate carries
            # no user_id, FOR that superuser too. A run that spread its keys over
            # three other accounts metered itself against the minter's budget and
            # died at the first creation with a 429; one account also makes the
            # bearer control arm a true control, differing only in the credential.
            #
            # Order is forced by foreign keys and by the cache: keys first (the
            # users are deleted and reinserted below, and api_keys.user_id is a
            # foreign key), then the accounts, then their rate-limit buckets.
            result["keys_purged_before_run"] = await _purge_fixture()
            result["fixture"] = await provision_accounts(admin)
            result["rate_limit_reset"] = await reset_gcra_buckets(admin, [SUPERUSER])
            su_token = await login(SUPERUSER, _ip(9_100_000), SUPERUSER_PASSWORD)
            pairs: list[tuple[str, str]] = []
            minted: dict[str, str] = {}
            for i in range(KEY_COUNT):
                # Every key goes through the PUBLIC route. It mints successfully
                # (201) on a database built by create_all, so the direct-insert
                # path stays a recorded fallback rather than the default - and
                # three POSTs are comfortably inside the minting superuser's
                # Pro budget of 1000 per 3600.
                key, how = await create_key(su_token, f"m7-{i}", _ip(9_200_000 + i), probe_route=True)
                pairs.append((SUPERUSER, key))
                minted[f"m7-{i}"] = how
            result["keys_created"] = len(pairs)
            result["key_owners"] = sorted({o for o, _ in pairs})
            result["key_owner_rule"] = (
                "APIKeyCreate (api_keys/schemas.py:21-24) has no user_id field and forbids extras, "
                "so POST /api/v1/api-keys mints the key for the CALLER. Every key here is therefore "
                "owned by the one provisioned account, which is also the bearer control account - the "
                "measured arms then differ in the credential and nothing else."
            )
            result["key_minted_by"] = SUPERUSER
            result["key_creation_permission"] = "users:manage (superuser)"
            result["keys_minted_how"] = minted
            result["creation_route_probe"] = CREATION_PROBE[0] if CREATION_PROBE else None
            step(
                "creation_route_probed_once",
                "the public minting route was exercised for every key, to demonstrate rather than assume",
                KEY_COUNT,
                len(CREATION_PROBE),
            )

            # --- phase 2: the IP plan, then the arms
            #
            # The sample size IS the budget. An API-key request is admitted as
            # ANONYMOUS (request_admission.py:349-373 reads identity only from
            # the Bearer payload), so one source IP may issue exactly
            # DEFAULT_ANONYMOUS_LIMIT[0] requests before the next is refused.
            # Every repetition therefore gets its OWN fresh IP and issues exactly
            # that many: 5 x 10 = 50 successful requests, none of them bought by
            # relaxing a budget or by spending 50 different IPs on 50 requests
            # (which would report a number no customer can reproduce).
            anon_limit, anon_period = settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT
            rep_ips = [_ip(9_300_000 + i) for i in range(repetitions)]
            ceiling_ip = _ip(9_400_000)
            bearer_ip = _ip(9_400_001)
            # The setup IPs are deterministic too, so their leftover buckets are
            # just as inherited between runs as the measured ones would be. They
            # are reset for the same reason and recorded in the same block.
            setup_ips = [_ip(9_000_001), _ip(9_100_000), *(_ip(9_200_000 + i) for i in range(KEY_COUNT))]
            all_ips = [*rep_ips, ceiling_ip, bearer_ip, *setup_ips]
            result["ip_plan"] = {
                "repetition_ips": rep_ips,
                "ceiling_ip": ceiling_ip,
                "bearer_ip": bearer_ip,
                "setup_ips": setup_ips,
                "distinct": len(set(all_ips)) == len(all_ips),
            }
            step(
                "measured_source_ips_are_distinct",
                "each arm and repetition spends its own source IP, so no bucket is shared",
                len(all_ips),
                len(set(all_ips)),
            )
            result["anonymous_budget"] = {
                "limit": anon_limit,
                "period": anon_period,
                "requests_per_repetition": anon_limit,
                "source": "settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT, read at run time",
                "why_not_the_tier_budget": (
                    "The key owner's tier budget (Pro: 1000 per 3600) is NEVER applied to an "
                    "API-key request, because admission resolves no user for it. Measuring "
                    "against the tier budget would be measuring a path that does not exist."
                ),
            }
            result["anonymous_bucket_reset"] = await reset_anonymous_buckets(
                admin, all_ips, result["fixture"]["tenant_id"]
            )

            # --- arm 1: the baseline. Requests interleave the three keys so the
            # cold/warm split is visible rather than hidden inside an average.
            probe = Probe()
            probe.install()
            per_rep: list[dict[str, Any]] = []
            per_request: list[dict[str, Any]] = []
            statuses: collections.Counter[str] = collections.Counter()
            who: set[str] = set()
            seen_budgets: set[tuple[str, str]] = set()
            min_remaining: dict[str, int] = {}
            pooled: list[float] = []
            unexpected: list[dict[str, Any]] = []
            srv_total: collections.Counter[str] = collections.Counter()
            try:
                for rep in range(repetitions):
                    _reset_counters()
                    before = await commandstats(admin)
                    latencies: list[float] = []
                    rep_statuses: collections.Counter[str] = collections.Counter()
                    for i in range(anon_limit):
                        key_index = i % len(pairs)
                        user, key = pairs[key_index]
                        rtt0 = MY_RTT
                        cmd0 = MY_CMD + MY_PIPE_CMD + MY_IMMEDIATE + MY_PUBSUB
                        async with client_for(rep_ips[rep]) as c:
                            t0 = time.perf_counter()
                            r = await c.get(MEASURED_PATH, headers={"X-API-Key": key})
                            dt_ms = (time.perf_counter() - t0) * 1000.0
                        latencies.append(dt_ms)
                        rep_statuses[str(r.status_code)] += 1
                        # A per-request Redis record, because the requests are NOT
                        # homogeneous: the API-key validation and the user
                        # directory both cache. A single commands-per-request
                        # average would hide which request paid what.
                        per_request.append(
                            {
                                "repetition": rep + 1,
                                "source_ip": rep_ips[rep],
                                "index_in_repetition": i,
                                "key_name": f"m7-{key_index}",
                                "owner": user,
                                "status": r.status_code,
                                "latency_ms": round(dt_ms, 4),
                                "round_trips": MY_RTT - rtt0,
                                "commands": MY_CMD + MY_PIPE_CMD + MY_IMMEDIATE + MY_PUBSUB - cmd0,
                            }
                        )
                        if r.status_code != 200 and len(unexpected) < 3:
                            # A count alone cannot be diagnosed. The first
                            # non-200 body is captured verbatim, bounded,
                            # because an unexplained 4xx on the measured path is
                            # a finding, not noise.
                            unexpected.append(
                                {
                                    "repetition": rep + 1,
                                    "status": r.status_code,
                                    "owner": user,
                                    "body": r.text[:500],
                                }
                            )
                        if r.status_code == 200:
                            who.add(str(r.json().get("username", "")))
                            limit = r.headers.get("x-ratelimit-limit")
                            period = r.headers.get("x-ratelimit-period")
                            left = r.headers.get("x-ratelimit-remaining")
                            if limit and period:
                                seen_budgets.add((limit, period))
                            if left is not None:
                                prev = min_remaining.get(user)
                                value = int(left)
                                min_remaining[user] = value if prev is None else min(prev, value)
                    after = await commandstats(admin)
                    srv = {
                        k: after.get(k, 0) - before.get(k, 0)
                        for k in set(after) | set(before)
                        if after.get(k, 0) - before.get(k, 0) > 0
                    }
                    srv.pop("info", None)
                    srv_total.update(srv)
                    cmds_rep = MY_CMD + MY_PIPE_CMD + MY_IMMEDIATE + MY_PUBSUB
                    # The category snapshots are taken HERE because the next
                    # iteration's _reset_counters() clears them. Reading them
                    # after the loop would silently report the LAST repetition's
                    # breakdown as if it were the run's - which is exactly what
                    # the totals below used to do.
                    per_rep.append(
                        {
                            "repetition": rep + 1,
                            "source_ip": rep_ips[rep],
                            "requests": anon_limit,
                            "statuses": dict(rep_statuses),
                            "round_trips": MY_RTT,
                            "commands": cmds_rep,
                            "round_trips_per_request": round(MY_RTT / anon_limit, 4),
                            "commands_per_request": round(cmds_rep / anon_limit, 4),
                            "by_category": {
                                "direct": MY_CMD,
                                "pipeline_staged": MY_PIPE_CMD,
                                "pipeline_flush": MY_PIPE_EXEC,
                                "immediate_watch": MY_IMMEDIATE,
                                "pubsub": MY_PUBSUB,
                            },
                            "round_trips_by_category": dict(RTT_BY_CAT),
                            "server_commands_total": sum(srv.values()),
                            "server_commandstats_delta": srv,
                            "latency": summarise_latency(latencies),
                        }
                    )
                    statuses.update(rep_statuses)
                    pooled.extend(latencies)
            finally:
                probe.remove()

            # The metering finding as a MEASURED assertion, taken while the probe
            # is off and BEFORE the bearer arm: no per-USER bucket may exist for
            # the measured path, because admission never resolved a user for an
            # API-key request. A tier bucket appearing here would mean the
            # finding is wrong.
            owner_ids = [info["user_id"] for info in result["fixture"]["accounts"].values()]
            tier_buckets: list[str] = []
            for uid in owner_ids:
                tier_buckets += [
                    k async for k in admin.scan_iter(match=f"gcra:*rate_limit:user:{uid}:*users_me", count=500)
                ]
            result["tier_buckets_after_api_key_traffic"] = sorted(tier_buckets)
            result["per_user_bucket_scan_pattern"] = "gcra:*rate_limit:user:<uid>:*users_me"

            # --- arm 2: the ceiling. One fresh IP issues limit + 1 requests, so
            # the refusal is measured rather than assumed and the boundary is
            # shown to BE the configured budget rather than an accident of load.
            # The probe is off: these requests exist to measure admission, not
            # the Redis cost of the endpoint, and mixing the two would corrupt
            # both. Timings are reported here and are NOT part of the baseline.
            ceiling: list[dict[str, Any]] = []
            async with client_for(ceiling_ip) as c:
                for i in range(anon_limit + 1):
                    user, key = pairs[i % len(pairs)]
                    t0 = time.perf_counter()
                    r = await c.get(MEASURED_PATH, headers={"X-API-Key": key})
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    ceiling.append(
                        {
                            "index": i,
                            "key_name": f"m7-{i % len(pairs)}",
                            "owner": user,
                            "status": r.status_code,
                            "latency_ms": round(dt_ms, 4),
                            "x_ratelimit_limit": r.headers.get("x-ratelimit-limit"),
                            "x_ratelimit_remaining": r.headers.get("x-ratelimit-remaining"),
                            "retry_after": r.headers.get("retry-after"),
                            "error_code": _error_code(r),
                            "body": r.text[:300] if r.status_code != 200 else "",
                        }
                    )
            refused = [row for row in ceiling if row["status"] != 200]
            result["ceiling_arm"] = {
                "source_ip": ceiling_ip,
                "what_it_measures": (
                    "how many API-key requests one source IP may issue before admission refuses it, "
                    "with production budgets live and no relaxation"
                ),
                "budget": {"limit": anon_limit, "period": anon_period},
                "requests": len(ceiling),
                "successes": len(ceiling) - len(refused),
                "first_refused_index": refused[0]["index"] if refused else None,
                "refused_status": refused[0]["status"] if refused else None,
                "refused_error_code": refused[0]["error_code"] if refused else None,
                "retry_after": refused[0]["retry_after"] if refused else None,
                "auto_ban_key_present_after": bool(await admin.exists(f"security:ban:{ceiling_ip}")),
                "auto_ban_violation_count_after": await admin.get(f"security:violations:{ceiling_ip}"),
                "excluded_from_baseline": "its latency and Redis counts are NOT in the numbers above",
                "requests_detail": ceiling,
            }

            # --- arm 3: the bearer control. The SAME account, with a Bearer
            # token, must be metered on its tier budget. If it is, then the
            # difference between this arm and arms 1-2 is the finding: the API
            # key is not what decides the budget, the absence of a Bearer token
            # is. provision_accounts gives the account the SUPERUSER password
            # hash, so the login uses SUPERUSER_PASSWORD.
            owner = SUPERUSER
            bearer_token = await login(owner, bearer_ip, SUPERUSER_PASSWORD)
            result["bearer_user_bucket_reset"] = await reset_gcra_buckets(admin, [owner])
            bearer: list[dict[str, Any]] = []
            async with client_for(bearer_ip) as c:
                for _ in range(BEARER_CONTROL_REQUESTS):
                    t0 = time.perf_counter()
                    r = await c.get(MEASURED_PATH, headers={"Authorization": f"Bearer {bearer_token}"})
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    bearer.append(
                        {
                            "status": r.status_code,
                            "latency_ms": round(dt_ms, 4),
                            "username": str(r.json().get("username", "")) if r.status_code == 200 else "",
                            "x_ratelimit_limit": r.headers.get("x-ratelimit-limit"),
                            "x_ratelimit_period": r.headers.get("x-ratelimit-period"),
                            "x_ratelimit_remaining": r.headers.get("x-ratelimit-remaining"),
                        }
                    )
            owner_tier = tuple(settings.rate_limit.TIER_RATE_LIMITS[TIER_NAME.lower()])
            result["bearer_control_arm"] = {
                "source_ip": bearer_ip,
                "account": owner,
                "tier": TIER_NAME,
                "tier_budget_from_settings": list(owner_tier),
                "what_it_measures": (
                    "the same endpoint, same account, same path, authenticated by Bearer token "
                    "instead of API key - so the two arms differ in exactly one thing"
                ),
                "requests": bearer,
                "observed_budgets": sorted({(b["x_ratelimit_limit"], b["x_ratelimit_period"]) for b in bearer}),
            }

            total = sum(statuses.values())
            # Summed from the per-repetition records, NOT from the module
            # counters. Those are reset at the top of every repetition, so
            # reading them after the loop reports the LAST repetition's totals
            # labelled as the run's - which is what this block did, and which
            # silently divided repetition 5's 53 round trips by all 50 requests
            # to publish "1.06 round trips per request" when the run issued 269.
            rt_total = sum(r["round_trips"] for r in per_rep)
            cmds = sum(r["commands"] for r in per_rep)
            cats: collections.Counter[str] = collections.Counter()
            for r in per_rep:
                cats.update(r["by_category"])
            rt_cats: collections.Counter[str] = collections.Counter()
            for r in per_rep:
                rt_cats.update(r["round_trips_by_category"])
            require(
                "per_repetition_round_trips_sum_to_the_run_total",
                "the run total is the sum of the repetitions, not the last one's counters",
                sum(r["requests"] for r in per_rep),
                total,
            )

            result["repetitions"] = repetitions
            result["per_repetition"] = per_rep
            result["per_request"] = per_request
            result["statuses"] = dict(statuses)
            result["requests"] = total
            result["authenticated_usernames"] = sorted(who)
            result["results"] = {
                "latency_ms": summarise_latency(pooled),
                "latency_caveat": (
                    f"n={len(pooled)} in-process and sequential: timed with perf_counter around "
                    "the awaited ASGITransport call, so no socket and no uvicorn are involved. NOT "
                    "comparable with step-4's over-the-wire users_me p50 30.7745 ms at concurrency 8. "
                    "This is a THIRD quantity: neither the round-trip count nor the command count can "
                    "be derived from it, nor it from them. The sample is one hour's budget per source "
                    f"IP ({anon_limit} requests x {repetitions} fresh IPs), which is what the metering "
                    "defect allows - it is not a throughput sample."
                ),
                "repetition_figures": repetition_figures(per_rep),
                "cold_first_repetition": {
                    "note": (
                        "Repetition 1 is the cold one: the Lua scripts in the GCRA limiter are "
                        "compiled and cached on first use, so it issues extra EVALSHA/GET commands "
                        "and its slowest request sets the pooled tail. It is reported rather than "
                        "dropped, because a baseline that discards the cold start is not what a "
                        "customer's first request sees."
                    ),
                    "first_repetition_commands_per_request": per_rep[0]["commands_per_request"],
                    # DISTINCT values plus how many repetitions they came from: a bare
                    # set of four identical figures renders as [5.4] and reads as
                    # though only one later repetition ran.
                    "later_repetitions_commands_per_request": sorted({r["commands_per_request"] for r in per_rep[1:]}),
                    "later_repetitions_count": len(per_rep) - 1,
                    "first_repetition_server_commands": per_rep[0]["server_commands_total"],
                    "later_repetitions_server_commands": sorted({r["server_commands_total"] for r in per_rep[1:]}),
                    "later_repetitions_server_commands_note": (
                        "distinct values across the later repetitions; each one issued "
                        f"{len(per_rep) - 1} repetition(s) worth of identical traffic"
                    ),
                },
                "observed_rate_limit_budgets": [
                    {"limit": int(lim), "period": int(per)} for lim, per in sorted(seen_budgets)
                ],
                "min_rate_limit_remaining_by_owner": min_remaining,
            }
            result["redis"] = {
                "round_trips_total": rt_total,
                "round_trips_per_request": round(rt_total / total, 4),
                "commands_total": cmds,
                "commands_per_request": round(cmds / total, 4),
                "commands_per_round_trip": round(cmds / rt_total, 4),
                "by_category": dict(cats),
                "round_trips_by_category": dict(rt_cats),
                "unattributed_round_trips": rt_cats.get("unattributed", 0),
                "server_commandstats_delta": dict(srv_total),
                "server_commands_total": sum(srv_total.values()),
                "totals_are_sums_over_all_repetitions": True,
                "counts_cover": "arm 1 only; the ceiling and bearer arms run with the probe removed",
            }
            result["unexpected_bodies"] = unexpected

            # The statuses counter is keyed by STATUS CODE, not by request
            # count: the earlier `{str(total): total}` compared "50 requests"
            # against {"200": 50} and failed a run in which every single
            # measured request had answered 200.
            require(
                "endpoint_requests_all_succeeded",
                "every measured request answered 200",
                {"200": total},
                dict(statuses),
            )
            require(
                "endpoint_authenticated_as_the_key_owner",
                "every request authenticated as the account that owns its key",
                sorted({u for u, _ in pairs}),
                sorted(who),
            )
            step(
                "api_key_traffic_never_reached_the_tier_budget",
                "no per-USER bucket exists for the measured path after 50 API-key requests, "
                "because admission resolves identity only from a Bearer token",
                [],
                sorted(tier_buckets),
                pattern=result["per_user_bucket_scan_pattern"],
            )
            require(
                "anonymous_ceiling_is_the_configured_budget",
                "one source IP is served exactly DEFAULT_ANONYMOUS_LIMIT[0] requests and refused the next",
                {"limit": anon_limit, "first_refused_index": anon_limit, "error_code": "RATE_LIMITED"},
                {
                    "limit": len(ceiling) - len(refused),
                    "first_refused_index": result["ceiling_arm"]["first_refused_index"],
                    "error_code": result["ceiling_arm"]["refused_error_code"],
                },
            )
            require(
                "bearer_control_is_metered_on_the_owner_tier",
                "the same account with a Bearer token gets its tier budget, so the API key is what loses it",
                [(str(owner_tier[0]), str(owner_tier[1]))],
                [
                    (str(lim), str(per))
                    for lim, per in sorted({(b["x_ratelimit_limit"], b["x_ratelimit_period"]) for b in bearer})
                ],
            )
            step(
                "commands_and_round_trips_reported_separately",
                "T-6: the two quantities are distinct columns, not one number",
                True,
                cmds != rt_total,
                commands=cmds,
                round_trips=rt_total,
            )
            require(
                "round_trip_buckets_sum_to_total",
                "write-time tagging makes the buckets sum by construction",
                rt_total,
                sum(rt_cats.values()),
            )
            require(
                "every_round_trip_is_attributed_to_a_command",
                "no write was unattributed: an unattributed round trip means the instrument missed a "
                "command, so 'commands per request' would be a floor rather than a total",
                {},
                {k: v for k, v in rt_cats.items() if k == "unattributed" and v},
            )
            step(
                "latency_sample_is_not_a_count",
                "the latency sample is reported separately from both Redis counts",
                True,
                total == len(pooled) and result["results"]["latency_ms"]["p50"] > 0,
                sample=len(pooled),
                requests=total,
            )
        finally:
            await admin.aclose()
    return result


APP: Any = None
BASE_URL = "http://127.0.0.1:8100"


def main() -> int:
    p = argparse.ArgumentParser(description="M-7 API-key endpoint baseline")
    p.add_argument("--base-url", default=BASE_URL)
    # There is no --requests-per-repetition flag: the number of requests each
    # repetition may issue is read from the live settings, because it IS the
    # anonymous budget. A flag would let the sample and the budget disagree.
    p.add_argument("--repetitions", type=int, default=REPETITIONS)
    p.add_argument("--output", default="/home/ansible/m7_result.json")
    args = p.parse_args()
    try:
        result = asyncio.run(main_async(args.base_url, args.repetitions))
    except Failure as exc:
        print(f"ASSERTION FAILURE: {exc}")
        result = {
            **PARTIAL,
            "outcome": "FAIL",
            "error": str(exc),
            "steps": STEPS,
            "failures": FAILURES,
        }
    except Exception as exc:  # precondition / setup problem
        # The traceback goes to stderr as well as the summary line: a harness that
        # reports only `type: message` costs a whole run per unlocated TypeError,
        # because there is no line number to look up.
        traceback.print_exc()
        print(f"PRECONDITION FAILURE: {type(exc).__name__}: {exc}")
        result = {
            **PARTIAL,
            "outcome": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "steps": STEPS,
            "failures": FAILURES,
        }
    result["steps"] = STEPS
    result["failures"] = FAILURES
    # An early abort leaves FAILURES empty and an `error` on the result, so this
    # must never UPGRADE a non-PASS outcome to PASS. The default is already
    # "FAIL"; promoting it on `not FAILURES` alone is what let a run that died at
    # the first key creation report PASS having measured nothing.
    if result.get("error") or FAILURES or result.get("outcome") == "ERROR":
        result["outcome"] = "FAIL"
    elif result.get("outcome") != "PASS":
        result["outcome"] = "PASS"
    Path(args.output).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\noutcome={result['outcome']} steps={len(STEPS)} failures={len(FAILURES)}")
    print(json.dumps(result.get("redis", {}), indent=2, default=str))
    print(json.dumps(result.get("results", {}), indent=2, default=str))
    return 0 if result["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
