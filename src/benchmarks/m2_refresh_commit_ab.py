"""M-2 (bead FastAPI_saas-z0oi): the refresh COMMIT's contribution, measured.

WHAT
    A before/after A/B of the refresh request on the Linux target, with the
    refresh's single critical-path COMMIT isolated, and the ~3.2 ms / 22.5 ms
    projection either confirmed or corrected.

WHY
    EM-4's artefact (``perf-results/RESULT-20261012-sweepB``'s refresh run)
    states the COMMIT's position and refuses to attribute a cost to it:

        "The handler COMMITS a transaction on the request path. Its cost is
         NOT separately attributed here and MUST NOT be read as included:
         M-2 (bead z0oi) measures the COMMIT in isolation and remains open."

    The ~3.2 ms / 22.5 ms figures this bead has to settle are a PURE
    PROJECTION on ``census.txt``, which is pipeline-blind (perf J6): the
    denominator itself is unverified, and the COMMIT's share of it was never
    measured at all.

THE TWO ARMS
    ARM A ("before", today's code): the refresh request exactly as it runs.
        ``src/app/core/security/jwt_service.py``'s ``blacklist_token`` does
        ``db.add(TokenBlacklist(...))`` and then ``await db.commit()`` on the
        request path. That COMMIT is the thing under test.

    ARM B ("after", the COMMIT moved off the critical path): the same request,
        with the COMMIT taken off the request's path. It is NOT a no-op
        commit. A no-op commit would also skip the FLUSH, so the INSERT's
        round trip AND the WAL fsync (which Postgres performs at COMMIT, not
        at INSERT) would both disappear and the saving would be OVERSTATED.
        Instead arm B keeps the INSERT on the path (``flush()``) and defers
        ONLY the COMMIT - the fsync and its round trip - to after the
        response has been produced. The transaction stays open; the probe
        commits and closes it once the response is in hand.

    The two arms therefore differ by exactly one thing: whether the COMMIT's
    round trip and WAL fsync happen before the response (A) or after it (B).

    The deferred COMMIT is ALSO timed, separately. That is the "COMMIT in
    isolation" EM-4 asked for: the COMMIT's own cost, measured directly on
    the request's own connection, in both arms.

HOW THE MEASUREMENT IS KEPT HONEST
    * Arms are INTERLEAVED and alternate request by request, so any drift in
      the box cancels instead of landing on one arm.
    * Every measured refresh is preceded by a fresh LOGIN, because refresh
      REUSES a token and a second refresh of the same token is not the same
      request. The login's cost is NOT in the measurement; the login count is
      recorded.
    * The denominator is measured IN THIS RUN, on THIS box, arm A's pooled
      p50 - never the cold-start-inflated 22.5 ms. Failure mode (i) of the
      bead is a ratio built on a cold artefact; this harness refuses to build
      one.
    * The store addresses are recorded with the figure. Failure mode (ii) of
      the bead: a COMMIT on a REMOTE Postgres is an RTT, so the answer moves
      with store locality and the locality is part of the number.
    * Both arms are on Linux. Failure mode (iii) of the bead: a Windows
      ``before`` arm is never admissible (A-5).
    * The arms are reported against the noise floor, and if the arms are not
      separated by it, this harness says the A/B did not resolve the
      difference rather than quoting the difference as if it did.

WHAT IS NOT ASSERTED
    No p95 claim, no capacity claim, and no figure for any other endpoint.
    The probe judges the refresh request only.

NO TEST WAS WEAKENED TO ACCOMMODATE A CHANGE; NO ASSERTION WAS RELAXED TO MAKE
A NUMBER PASS. This harness adds no assertion to any suite and weakens none.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

from src.benchmarks.percentiles import nearest_rank

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "perf-results" / "RESULT-20261020-z0oi" / "refresh_commit_ab.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TENANT_HEADER = "test-tenant-e2e"

# The projection this bead has to confirm or correct. It is the INPUT, never
# an output: nothing below is tuned to reproduce it.
PROJECTED_COMMIT_MS = 3.2
PROJECTED_REQUEST_MS = 22.5

# The programme's noise floor (perf C11 / the A/B discriminator bead).
INTRA_ARM_NOISE_FLOOR_X = 2.0
RUN_TO_RUN_NOISE_FLOOR_X = 7.1


# The percentile estimator is NOT defined here. src/benchmarks/percentiles.py
# holds the one implementation in the harness tree, and every harness in it
# delegates - a second implementation is a bug the tree-wide scan in
# tests/unit/benchmarks/test_percentiles.py fails on. The estimator is
# nearest rank in both, so the numbers this harness published are unchanged;
# only the CALLS move, from a 0-100 scale to the canonical fractions.



class DeferredCommits:
    """Arm B's machinery: hold the COMMIT until the response is produced.

    The session's COMMIT is replaced, for the duration of arm B only, by a
    FLUSH plus a queue entry. The real COMMIT - the fsync and its round
    trip - is performed by the probe once the response is in hand, and it is
    timed there. The row still lands; it lands later. That is precisely what
    "off the critical path" means, and it is what the difference between the
    arms measures.
    """

    def __init__(self) -> None:
        self.queued: list[Any] = []
        self.queued_ids: set[int] = set()
        self.commit_ms: list[float] = []
        self._original_commit: Any = None
        self._original_close: Any = None
        self._patched = False

    def install(self) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        self._original_commit = AsyncSession.commit
        self._original_close = AsyncSession.close
        original_close = self._original_close
        queued = self.queued
        queued_ids = self.queued_ids

        async def patched_commit(self: Any, *args: Any, **kwargs: Any) -> Any:
            # Keep the INSERT on the request's path; defer only the COMMIT.
            await self.flush()
            queued.append(self)
            queued_ids.add(id(self))
            return None

        async def patched_close(self: Any, *args: Any, **kwargs: Any) -> Any:
            # A queued session must survive the dependency's ``finally``
            # so the deferred COMMIT can still be issued.
            if id(self) in queued_ids:
                return None
            return await original_close(self, *args, **kwargs)

        AsyncSession.commit = patched_commit  # type: ignore[method-assign]
        AsyncSession.close = patched_close  # type: ignore[method-assign]
        self._patched = True

    def restore(self) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        if not self._patched:
            return
        AsyncSession.commit = self._original_commit  # type: ignore[method-assign]
        AsyncSession.close = self._original_close  # type: ignore[method-assign]
        self._patched = False

    async def flush_pending(self) -> None:
        """Perform the deferred COMMITs, off the request's path, and time them."""
        original_commit = self._original_commit
        while self.queued:
            session = self.queued.pop(0)
            started = time.perf_counter()
            await original_commit(session)
            self.commit_ms.append((time.perf_counter() - started) * 1000.0)
            try:
                await self._original_close(session)
            except Exception:  # noqa: BLE001 - a failed close must not abort the measurement
                pass
            self.queued_ids.discard(id(session))


class ArmTimer:
    """Times the in-path COMMIT of arm A without changing its behaviour."""

    def __init__(self) -> None:
        self.commit_ms: list[float] = []
        self._original_commit: Any = None
        self._patched = False

    def install(self) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        self._original_commit = AsyncSession.commit
        original_commit = self._original_commit
        timings = self.commit_ms

        async def timed_commit(self: Any, *args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return await original_commit(self, *args, **kwargs)
            finally:
                timings.append((time.perf_counter() - started) * 1000.0)

        AsyncSession.commit = timed_commit  # type: ignore[method-assign]
        self._patched = True

    def restore(self) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        if not self._patched:
            return
        AsyncSession.commit = self._original_commit  # type: ignore[method-assign]
        self._patched = False


async def _clear_auth_rate_limits() -> None:
    """Clear the auth rate-limit keys, exactly as the e2e conftest does.

    The auth limiter allows only a handful of operations per minute per IP,
    and this harness logs in before every measured refresh. Without this the
    limiter trips mid-run and the measurement stops for a reason that has
    nothing to do with the refresh path. Both key patterns are cleared
    because the limiter may use fixed-window (``ratelimit:*``) or GCRA
    (``gcra:*``) keys depending on the active algorithm.
    """
    from src.app.core.utils import cache

    redis = getattr(cache, "client", None)
    if redis is None:
        return
    for pattern in ("ratelimit:*", "gcra:*"):
        keys = [key async for key in redis.scan_iter(match=pattern)]
        if keys:
            await redis.delete(*keys)


async def _login(client: Any, username: str, password: str) -> str | None:
    await _clear_auth_rate_limits()
    response = await client.post(
        "/api/v1/account/login",
        data={"username": username, "password": password},
    )
    if response.status_code != 200:
        return None
    token = response.cookies.get("refresh_token")
    return str(token) if token is not None else None


async def _refresh(client: Any, refresh_token: str) -> tuple[float, int, bool]:
    """One refresh; returns (client-observed ms, status, the token rotated)."""
    before = client.cookies.get("refresh_token")
    started = time.perf_counter()
    response = await client.post(
        "/api/v1/account/refresh",
        data={"refresh_token": refresh_token},
    )
    elapsed = (time.perf_counter() - started) * 1000.0
    rotated = response.cookies.get("refresh_token") != before
    return elapsed, response.status_code, rotated


async def _run_arm(
    client: Any,
    username: str,
    password: str,
    count: int,
    arm: str,
    deferred: DeferredCommits,
) -> dict[str, Any]:
    latencies: list[float] = []
    statuses: dict[int, int] = {}
    rotations = 0
    no_rotation = 0
    logins = 0
    failed_logins = 0

    for _ in range(count):
        refresh_token = await _login(client, username, password)
        if refresh_token is None:
            failed_logins += 1
            continue
        logins += 1
        elapsed, status, rotated = await _refresh(client, refresh_token)
        latencies.append(elapsed)
        statuses[status] = statuses.get(status, 0) + 1
        rotations += int(rotated)
        no_rotation += int(not rotated)
        if arm == "B":
            # The deferred COMMIT happens here: after the response, off the
            # request's path. Its cost is recorded, not hidden.
            await deferred.flush_pending()

    return {
        "arm": arm,
        "requests": len(latencies),
        "logins": logins,
        "failed_logins": failed_logins,
        "status_distribution": {str(k): v for k, v in sorted(statuses.items())},
        "rotations_observed": rotations,
        "no_new_access_token": no_rotation,
        "handler_executed": rotations == len(latencies) and len(latencies) > 0,
        "latency": {
            "min_ms": round(min(latencies), 4) if latencies else None,
            "p50_ms": round(nearest_rank(latencies, 0.5), 4) if latencies else None,
            "p95_ms": round(nearest_rank(latencies, 0.95), 4) if latencies else None,
            "p99_ms": round(nearest_rank(latencies, 0.99), 4) if latencies else None,
        },
        "_latencies": latencies,
    }


async def _main_async(args: argparse.Namespace) -> int:
    import httpx

    from src.app.main import app

    deferred = DeferredCommits()
    arm_a_timer = ArmTimer()
    arm_b_commit_ms: list[float] = []

    # The lifespan must run: the pools and the bootstrap are set up there.
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=120.0,
            headers={"X-Tenant-ID": TENANT_HEADER, "User-Agent": USER_AGENT},
        ) as client:
            # Warmup: the first requests pay pool and import warm-up.
            warm = await _run_arm(client, args.username, args.password, args.warmup, "A", deferred)
            if not warm["handler_executed"]:
                print("WARMUP FAILED: the refresh handler did not execute", file=sys.stderr)

            a_latencies: list[float] = []
            b_latencies: list[float] = []
            a_statuses: dict[int, int] = {}
            b_statuses: dict[int, int] = {}
            a_rotations = 0
            b_rotations = 0
            a_logins = 0
            b_logins = 0

            for index in range(args.requests):
                # INTERLEAVED: the arms alternate request by request.
                for arm in ("A", "B"):
                    refresh_token = await _login(client, args.username, args.password)
                    if refresh_token is None:
                        continue
                    if arm == "A":
                        arm_a_timer.install()
                        try:
                            elapsed, status, rotated = await _refresh(client, refresh_token)
                        finally:
                            arm_a_timer.restore()
                        a_latencies.append(elapsed)
                        a_statuses[status] = a_statuses.get(status, 0) + 1
                        a_rotations += int(rotated)
                        a_logins += 1
                    else:
                        deferred.install()
                        try:
                            elapsed, status, rotated = await _refresh(client, refresh_token)
                        finally:
                            deferred.restore()
                        b_latencies.append(elapsed)
                        b_statuses[status] = b_statuses.get(status, 0) + 1
                        b_rotations += int(rotated)
                        b_logins += 1
                        await deferred.flush_pending()
                        arm_b_commit_ms.extend(deferred.commit_ms)
                        deferred.commit_ms.clear()
                if args.progress and (index + 1) % 25 == 0:
                    print(f"interleaved {index + 1}/{args.requests}", file=sys.stderr)

    a_commit_ms = arm_a_timer.commit_ms
    arm_a: dict[str, Any] = {
        "arm": "A",
        "description": "the refresh request exactly as it runs today; the COMMIT is on the request path",
        "requests": len(a_latencies),
        "logins": a_logins,
        "status_distribution": {str(k): v for k, v in sorted(a_statuses.items())},
        "rotations_observed": a_rotations,
        "handler_executed": a_rotations == len(a_latencies) and len(a_latencies) > 0,
        "latency": {
            "min_ms": round(min(a_latencies), 4) if a_latencies else None,
            "p50_ms": round(nearest_rank(a_latencies, 0.5), 4) if a_latencies else None,
            "p95_ms": round(nearest_rank(a_latencies, 0.95), 4) if a_latencies else None,
            "p99_ms": round(nearest_rank(a_latencies, 0.99), 4) if a_latencies else None,
        },
        "in_path_commit": {
            "samples": len(a_commit_ms),
            "p50_ms": round(nearest_rank(a_commit_ms, 0.5), 4) if a_commit_ms else None,
            "p95_ms": round(nearest_rank(a_commit_ms, 0.95), 4) if a_commit_ms else None,
            "min_ms": round(min(a_commit_ms), 4) if a_commit_ms else None,
        },
    }
    arm_b: dict[str, Any] = {
        "arm": "B",
        "description": (
            "the same request with the COMMIT off the critical path: the INSERT stays "
            "on the path (flush) and the COMMIT's round trip and WAL fsync are performed "
            "after the response, on the same connection, and timed there"
        ),
        "requests": len(b_latencies),
        "logins": b_logins,
        "status_distribution": {str(k): v for k, v in sorted(b_statuses.items())},
        "rotations_observed": b_rotations,
        "handler_executed": b_rotations == len(b_latencies) and len(b_latencies) > 0,
        "latency": {
            "min_ms": round(min(b_latencies), 4) if b_latencies else None,
            "p50_ms": round(nearest_rank(b_latencies, 0.5), 4) if b_latencies else None,
            "p95_ms": round(nearest_rank(b_latencies, 0.95), 4) if b_latencies else None,
            "p99_ms": round(nearest_rank(b_latencies, 0.99), 4) if b_latencies else None,
        },
        "deferred_commit": {
            "samples": len(arm_b_commit_ms),
            "p50_ms": round(nearest_rank(arm_b_commit_ms, 0.5), 4) if arm_b_commit_ms else None,
            "p95_ms": round(nearest_rank(arm_b_commit_ms, 0.95), 4) if arm_b_commit_ms else None,
            "min_ms": round(min(arm_b_commit_ms), 4) if arm_b_commit_ms else None,
        },
    }
    a_p50 = arm_a["latency"]["p50_ms"]
    b_p50 = arm_b["latency"]["p50_ms"]
    a_commit_p50 = arm_a["in_path_commit"]["p50_ms"]
    b_commit_p50 = arm_b["deferred_commit"]["p50_ms"]

    difference_ms = None
    ratio = None
    separated = False
    if a_p50 is not None and b_p50 is not None:
        difference_ms = round(a_p50 - b_p50, 4)
        if b_p50 > 0:
            ratio = round(a_p50 / b_p50, 4)
        separated = bool(ratio and ratio >= INTRA_ARM_NOISE_FLOOR_X)

    commit_contribution_ms = None
    if a_commit_p50 is not None and b_commit_p50 is not None:
        commit_contribution_ms = round((a_commit_p50 + b_commit_p50) / 2.0, 4)
    elif a_commit_p50 is not None:
        commit_contribution_ms = a_commit_p50

    denominator_ms = a_p50
    projection = {
        "projected_commit_ms": PROJECTED_COMMIT_MS,
        "projected_request_ms": PROJECTED_REQUEST_MS,
        "measured_denominator_ms": denominator_ms,
        "measured_commit_contribution_ms": commit_contribution_ms,
        "projected_share_percent": (
            round(PROJECTED_COMMIT_MS / PROJECTED_REQUEST_MS * 100.0, 3) if PROJECTED_REQUEST_MS else None
        ),
        "measured_share_percent": (
            round(commit_contribution_ms / denominator_ms * 100.0, 3)
            if commit_contribution_ms is not None and denominator_ms
            else None
        ),
        "projection_status": None,
    }
    if commit_contribution_ms is not None and denominator_ms:
        if abs(commit_contribution_ms - PROJECTED_COMMIT_MS) <= PROJECTED_COMMIT_MS * 0.5:
            projection["projection_status"] = "CONFIRMED_WITHIN_HALF"
        else:
            projection["projection_status"] = "CORRECTED"
        if abs(denominator_ms - PROJECTED_REQUEST_MS) <= PROJECTED_REQUEST_MS * 0.5:
            projection["denominator_status"] = "CONFIRMED_WITHIN_HALF"
        else:
            projection["denominator_status"] = "CORRECTED"

    artefact = {
        "schema": "perf-refresh-commit-ab/1",
        "bead": "FastAPI_saas-z0oi",
        "item": "M-2 - the refresh COMMIT's contribution",
        "driver": "in-process over ASGITransport with the lifespan running",
        "question": (
            "does moving the refresh COMMIT off the critical path save the claimed "
            "~3.2 ms of a 22.5 ms request, and is the denominator itself real"
        ),
        "machine": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(),
        },
        "repo_state": _repo_state(),
        "store_addresses": await _store_addresses(),
        "protocol": {
            "arms": ["A (COMMIT on the request path)", "B (COMMIT off the request path)"],
            "interleaved": True,
            "requests_per_arm": args.requests,
            "warmup_discarded": args.warmup,
            "logins": "every measured refresh is preceded by a fresh login; the login cost is not in the measurement",
            "denominator": "arm A's pooled p50 from THIS run; never the cold-start-inflated 22.5 ms",
            "percentile": "nearest rank, no interpolation",
        },
        "noise_floor": {
            "intra_arm_x": INTRA_ARM_NOISE_FLOOR_X,
            "run_to_run_x": RUN_TO_RUN_NOISE_FLOOR_X,
            "arms_separated_by_the_floor": separated,
            "ratio_a_over_b": ratio,
        },
        "arms": {"A": arm_a, "B": arm_b},
        "comparison": {
            "difference_ms": difference_ms,
            "ratio_a_over_b": ratio,
            "arms_separated_by_the_intra_arm_floor": separated,
            "commit_contribution_ms": commit_contribution_ms,
        },
        "projection": projection,
        "what_is_not_asserted": [
            "no p95 claim and no capacity claim: this is the refresh request only",
            "no figure for any other endpoint",
            "the deferred COMMIT is not free: it is timed, and the time is reported here",
        ],
    }

    payload = json.dumps(artefact, indent=2, sort_keys=False)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload + "\n", encoding="utf-8")
    print(f"wrote {output}")
    print(f"sha256 {hashlib.sha256(payload.encode('utf-8')).hexdigest()}")
    print(json.dumps(artefact["comparison"], indent=2))
    print(json.dumps(artefact["projection"], indent=2))
    return 0


def _repo_state() -> dict[str, Any]:
    import subprocess

    def run(args: list[str]) -> str | None:
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=20, cwd=REPO_ROOT)
        except (OSError, subprocess.SubprocessError):
            # git may be absent (OSError) or time out (SubprocessError). The
            # state is then UNKNOWN, not assumed - and the measurement itself
            # must not die for it.
            return None
        return out.stdout.strip() or None

    return {
        "head": run(["git", "rev-parse", "HEAD"]),
        "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "dirty": run(["git", "status", "--porcelain"]),
    }


async def _store_addresses() -> dict[str, Any]:
    """The addresses the COMMIT's round trip actually went to."""
    from src.app.core.config import settings

    out: dict[str, Any] = {}
    for name in ("POSTGRES_SERVER", "POSTGRES_HOST", "POSTGRES_PORT", "REDIS_CACHE_URL"):
        out[name] = str(getattr(settings, name, None))
    try:
        from src.app.core.db.database import _get_local_session

        out["local_session_url"] = str(getattr(_get_local_session(), "bind", None))
    except Exception as exc:  # noqa: BLE001 - pragma: no cover - reported, not swallowed
        out["local_session_url_error"] = str(exc)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--username",
        default=os.environ.get("TEST_EMAIL", ""),
        help="login username; defaults to $TEST_EMAIL so the secret stays on the box",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("TEST_PASSWORD", ""),
        help="login password; defaults to $TEST_PASSWORD so the secret stays on the box",
    )
    parser.add_argument("--requests", type=int, default=200, help="measured requests PER arm")
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()
    if not args.username or not args.password:
        parser.error(
            "no credentials: pass --username/--password, or export TEST_EMAIL and TEST_PASSWORD"
        )
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
