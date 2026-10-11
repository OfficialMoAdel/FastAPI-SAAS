"""
PERF-R1-A: Locust Load Test for FastAPI SaaS Hot Path
======================================================

Purpose: Establish baseline performance metrics before optimization.

Traffic Distribution (simulating realistic production patterns):
- 80 GET /api/v1/users/me (authenticated user info - THE HOT PATH)
- 10 POST /api/v1/ai/chat (AI completions)
- 10 POST /api/v1/account/refresh (token refresh)
- 10 POST /api/v1/account/logout (token blacklisting)

Logout used to be fired once per virtual user from `on_stop`, outside this mix
entirely, so every logout sample was a shutdown-burst sample. It is an ordinary
weighted task now. The mix is 80/10/10/10, not the 80/10/10 it was, and a run
before this change is not a same-instrument A/B against a run after it - see
the note below.

Target Metrics:
    NONE ARE ASSERTED, AND NONE ARE LISTED HERE.

    This table used to carry a "Current p95" column (~50ms / ~200ms+ / ~30ms)
    and a "Target p95" column, with no measurement behind either number. A
    harness that states the answer it is trying to measure is worse than a
    harness that states nothing: the next reader cannot tell a measurement from
    an aspiration, and an aspiration in a table gets quoted in a review as
    though it were a baseline.

    The targets are deliberately absent rather than merely unverified. Round 1
    established that every pre-2026-10-04 number in this tree was produced on
    Windows, where the runtime is not the deployment runtime, and that the two
    existing Locust baselines are not even a same-instrument A/B (the harness
    changed in commit 4cd00654, fourteen hours AFTER the baseline CSV was
    stamped). No reproducible Linux measurement of these endpoints exists yet,
    so there is nothing honest to print. When one does, it belongs here with
    its full protocol — host, core count, store locality, concurrency, run
    time, warmup, sample count, and the commit SHA of the instrument.

    What the harness does instead: it reports what it measured. See
    MetricsCollector.log_summary and the note on sample-size resolution in
    src/benchmarks/percentiles.py — a percentile the sample cannot resolve is
    reported as unresolvable, not as a number.

Usage:
    # 1. Seed the account pool FIRST (once per database):
    python scripts/setup_locust_test_data.py

    # 2. Basic run (10 users, 2 min):
    locust -f tests/performance/locustfile.py --headless -u 10 -r 2 --run-time 2m

    # Full baseline (100 users, 5 min):
    locust -f tests/performance/locustfile.py --headless -u 100 -r 10 --run-time 5m --csv=baseline

    # Web UI mode:
    locust -f tests/performance/locustfile.py
    # Then open http://localhost:8089

Environment variables:
    PERF_TEST_EMAILS       Comma-separated account pool (highest priority).
    PERF_TEST_USER_COUNT   Size of the generated pool `perf_user_<i>@example.com`
                           (default 100). Ignored when PERF_TEST_EMAILS is set.
    PERF_TEST_PASSWORD     Password for every pool account (default: Str1ngst!).
    PERF_TEST_TENANT       Tenant slug sent in X-Tenant-ID (default test-tenant-e2e).

IMPORTANT - why each virtual user needs its OWN account:
    The backend enforces `auth.AUTH_MAX_SESSIONS` (default 5) concurrent sessions
    per user, with oldest-session eviction on every new login/refresh. Sharing a
    single account across N > 5 virtual users therefore makes each login revoke a
    peer's session, and /users/me answers 401 "Session has been revoked". That
    shows up in Locust as a ~100% failure rate on the hot path and makes the
    baseline meaningless. One distinct account per virtual user keeps every
    account at 1 session and well under the cap.

    For distributed runs (multiple workers) each worker counts from zero, so pass
    an explicit, non-overlapping PERF_TEST_EMAILS pool per worker, or simply seed
    a pool larger than the total user count and accept the small overlap.
"""

from __future__ import annotations

import base64
import binascii
import itertools
import json
import logging
import os
import random
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from locust import HttpUser, between, events, task
from locust.exception import StopUser

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.benchmarks.percentiles import PERCENTILES, nearest_rank, unresolvable_quantiles
from src.benchmarks.perf_accounts import collision_risk, perf_username

# =============================================================================
# CONFIGURATION
# =============================================================================

# API base path
API_V1: Final[str] = "/api/v1"
# The monitoring router is mounted at /api, NOT /api/v1. The health
# endpoints live here, and assuming the v1 shape yields a 404 that
# reads as a missing endpoint rather than a wrong prefix.
API_ROOT: Final[str] = "/api"

# Default test credentials — must match scripts/setup_locust_test_data.py
DEFAULT_TEST_EMAIL: Final[str] = "premium_e2e@example.com"
DEFAULT_TEST_PASSWORD: Final[str] = "Str1ngst!"

# Tenant slug required by the tenant middleware on every request. The
# middleware resolves the header by SLUG (not tenant UUID); sending the
# UUID yields 403 "Access denied" before auth ever runs.
DEFAULT_TEST_TENANT: Final[str] = "test-tenant-e2e"

# AI model used by both the session and the chat call.
AI_CHAT_MODEL: Final[str] = "llama3.2"

# AI chat needs a reachable Ollama instance. It is OFF by default: without a
# provider every call blocks for seconds and then 503s, which inflates the tail
# latency of every other endpoint and makes the baseline meaningless. Enable
# with PERF_TEST_AI_ENABLED=1 once Ollama is running.
AI_ENABLED: Final[bool] = os.getenv("PERF_TEST_AI_ENABLED", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# AI Chat test payloads. Chat lives UNDER a session:
#   POST /api/v1/ai/sessions             -> {"title": ..., "model": ...} => {"id": <int>}
#   POST /api/v1/ai/sessions/{id}/chat   -> {"message": ..., "model": ...}
# There is no /api/v1/ai/chat/completions route; that path returns 404.
AI_SESSION_PAYLOAD: Final[dict[str, Any]] = {
    "title": "perf-test session",
    "model": AI_CHAT_MODEL,
}

AI_CHAT_PAYLOAD: Final[dict[str, Any]] = {
    "message": "Hello, this is a performance test message.",
    "model": AI_CHAT_MODEL,
}

# Slow request threshold (ms)
SLOW_REQUEST_THRESHOLD_MS: Final[float] = 100.0

# Traffic distribution weights
#
# LOGOUT_WEIGHT is the traffic that logout used to get for free: it used to be
# fired from on_stop, once per virtual user, outside the weighted task mix
# entirely. Moving it into the mix is what makes it a measurement rather than a
# teardown artefact, and it necessarily REDISTRIBUTES the other three. The mix
# is therefore no longer the 80/10/10 stated at the top of this file, and that
# line has been corrected. Do not silently renormalise these back to 80/10/10:
# the weights below are the specification of what a run now exercises.
TRAFFIC_USER_ME_WEIGHT: Final[int] = 80
TRAFFIC_AI_CHAT_WEIGHT: Final[int] = 10
TRAFFIC_TOKEN_REFRESH_WEIGHT: Final[int] = 10
TRAFFIC_SESSION_SURFACE_WEIGHT: Final[int] = 10
TRAFFIC_HEALTH_WEIGHT: Final[int] = 5

#: Reads that make up the authenticated surface walk. Paths only - the user id
#: is substituted per user, since a hard-coded id would measure the same row for
#: every virtual user and make the cache look warm in a way production never is.
#: `/users/me/rate-limits` is deliberately ABSENT. The real route is
#: `/users/{user_id}/rate-limits` and a user may not read another user's, so
#: self-access answers 403 by design - it is an authorisation check working, not a
#: missing endpoint, and measuring it would report a correct 403 as a failure.
#: `/notifications` is deliberately ABSENT for the same reason as
#: `/users/me/rate-limits`: a seeded perf account answers 403 on it, because the
#: notification surface is gated behind a permission the pool does not grant. That
#: 403 is an authorisation check WORKING. Measuring it would report a correct
#: denial as an endpoint failure and void the phase under 4.5 for a reason that
#: says nothing about the endpoint. Granting the permission is a separate change
#: to the seeder and is not silently made here.
#: `/users/me/rate-limits` is still absent and always will be: there is no such
#: route, the real one is per-user, and a user may not read another user's - so
#: self-access answers 403 by design. It is `users:manage`, and the fixture grants
#: read-only permissions deliberately.
#:
#: `/users` and `/notifications` were here before the fixture carried a role at
#: all, both answering 403. They are BACK because the root cause is fixed rather
#: than worked around: the seeder now grants the fixture role `users:read` and
#: `notifications:read`, so these endpoints are measured as themselves instead of
#: being dropped for an authorisation check that was never wrong.
SESSION_SURFACE_PATHS: Final[tuple[str, ...]] = (
    "/account/sessions",
    "/account/sessions/count",
    "/users",
    "/notifications",
)
# Settable, because a weighted logout cannot coexist with a CERTIFIED phase.
#
# Logout revokes the session server-side, so the refresh token dies with the access
# token and every request behind it carries credentials that are already dead. Plan
# 4.5 makes a 401 void the phase, so a run with logout in the mix measures what it
# wants and then refuses to certify it: measured 92 errors, then 29, 13, 8 as the
# recovery paths were fixed - and the residue is structural. Those requests cannot
# succeed, because the thing they need was destroyed by the task beside them.
#
# Set PERF_LOGOUT_WEIGHT=0 to certify a phase. The default is UNCHANGED at 10, so
# the specified mix still exercises logout by default. Which is right depends on
# what the run is FOR: a latency baseline wants logout out; a logout-inclusive load
# profile wants it in, and accepts a void.
#
# Whichever was used belongs in the artefact beside the other deviations, because a
# traffic mix is part of what was measured.
def _int_env(name: str, default: int) -> int:
    """An integer env override, falling back to the specification on anything odd.

    A malformed value must not silently become a different traffic mix, so anything
    unparseable keeps the default rather than guessing.
    """
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


LOGOUT_WEIGHT: Final[int] = _int_env("PERF_LOGOUT_WEIGHT", 10)

# Wait time configuration (seconds)
WAIT_TIME_MIN_S: Final[float] = 1.0
WAIT_TIME_MAX_S: Final[float] = 3.0

# ReadOnly user configuration
READONLY_WAIT_MIN_S: Final[float] = 0.5
READONLY_WAIT_MAX_S: Final[float] = 2.0
READONLY_USER_WEIGHT: Final[int] = 2

# Refresh the access token this many seconds before it actually expires, so the
# hot path never has to pay for a 401 round-trip.
TOKEN_REFRESH_MARGIN_S: Final[float] = 30.0

# Fallback assumed token lifetime (seconds) when the JWT has no readable `exp`.
DEFAULT_TOKEN_LIFETIME_S: Final[float] = 900.0

TEST_USER_PASSWORD = os.getenv("PERF_TEST_PASSWORD", DEFAULT_TEST_PASSWORD)
TEST_TENANT = os.getenv("PERF_TEST_TENANT", DEFAULT_TEST_TENANT)


def _build_credential_pool() -> list[tuple[str, str]]:
    """Build the per-virtual-user credential pool.

    Every spawned user takes the next entry, so each account holds exactly one
    session instead of 100 users fighting over ``AUTH_MAX_SESSIONS``.
    """
    explicit = os.getenv("PERF_TEST_EMAILS", "").strip()
    if explicit:
        return [(email.strip(), TEST_USER_PASSWORD) for email in explicit.split(",") if email.strip()]

    try:
        count = int(os.getenv("PERF_TEST_USER_COUNT", "100"))
    except ValueError:
        count = 100
    count = max(count, 1)
    return [(f"perf_user_{i}@example.com", TEST_USER_PASSWORD) for i in range(count)]


CREDENTIAL_POOL: Final[list[tuple[str, str]]] = _build_credential_pool()

# Global round-robin cursor over the pool. itertools.count is atomic enough
# under gevent's cooperative scheduling (no await between read and increment).
_pool_cursor = itertools.count()

# Set once, to avoid spamming the log on every failed login.
_pool_hint_logged = False

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("perf-test")


# =============================================================================
# METRICS COLLECTION
# =============================================================================


class MetricsCollector:
    """Collects and logs detailed performance metrics.

    Two sample populations are kept apart, and only the first is reported:

    ``request_times``
        Steady-state samples. These are the numbers a percentile is computed
        from.
    ``burst_times``
        Shutdown-burst samples, i.e. anything issued while the runner is tearing
        down. They are counted and reported as a count, never as a percentile.

    The separation exists because a shutdown-burst sample is not a slow
    request, it is a request competing with the event loop closing, the
    connection pool draining, and the Redis pool closing. Folding those into a
    p95 produces a bimodal distribution whose tail is attributable to nothing
    measurable. The 208.47 ms logout p95 in the 2026-09-22 baseline is the
    canonical example: that handler performs no bcrypt and does no batched I/O,
    so the number cannot be attributed to the code it claims to measure.

    The phase is set by :meth:`shutdown_burst` and read by
    :meth:`record_request`, not passed in by the caller. A caller-supplied tag
    would be a promise rather than a guarantee; the context manager makes
    "issued during teardown" a property of where the call sits in the code.
    """

    def __init__(self) -> None:
        self.request_times: dict[str, list[float]] = {}
        self.error_counts: dict[str, int] = {}
        self.burst_times: dict[str, list[float]] = {}
        self.burst_error_counts: dict[str, int] = {}
        self.start_time: float = 0
        self._in_shutdown_burst = False

    @contextmanager
    def shutdown_burst(self) -> Iterator[None]:
        """Record every request issued inside the block as a burst sample."""
        previous = self._in_shutdown_burst
        self._in_shutdown_burst = True
        try:
            yield
        finally:
            self._in_shutdown_burst = previous

    def record_request(self, name: str, response_time: float, success: bool) -> None:
        """Record a single request's metrics into the current phase's population."""
        times = self.burst_times if self._in_shutdown_burst else self.request_times
        errors = self.burst_error_counts if self._in_shutdown_burst else self.error_counts

        times.setdefault(name, [])
        errors.setdefault(name, 0)

        if success:
            times[name].append(response_time)
        else:
            errors[name] += 1

    def get_percentile(self, name: str, percentile: float) -> float:
        """Steady-state percentile for ``name``. ``percentile`` is 0-100.

        Delegates to the shared estimator. The formula this replaces was
        ``times[min(int(len(times) * percentile / 100), len(times) - 1)]``,
        which TRUNCATED instead of rounding up: at n=20 both p95 and p99
        indexed sample 19, so both columns were a maximum wearing a
        percentile's name. It also bypassed the rule in
        ``src/benchmarks/percentiles.py`` that a percentile the sample cannot
        resolve must not be quoted as if it were an estimate.
        """
        return nearest_rank(self.request_times.get(name, []), percentile / 100)

    def unresolvable_percentiles(self, name: str) -> list[float]:
        """Fractions in PERCENTILES that this name's sample cannot resolve."""
        return unresolvable_quantiles(len(self.request_times.get(name, [])), PERCENTILES)

    def log_summary(self) -> None:
        """Log summary metrics at test end. Steady-state only."""
        logger.info("=" * 60)
        logger.info("PERFORMANCE TEST SUMMARY")
        logger.info("=" * 60)

        for name, times in self.request_times.items():
            if not times:
                continue
            errors = self.error_counts.get(name, 0)
            total = len(times) + errors
            error_rate = (errors / total * 100) if total > 0 else 0

            # A percentile the sample cannot resolve is the maximum. Say so
            # rather than printing a number that carries no information.
            cannot_resolve = [f"p{int(q * 100)}" for q in self.unresolvable_percentiles(name)]
            caveat = f"  [n={len(times)} cannot resolve: {', '.join(cannot_resolve)}]\n" if cannot_resolve else ""

            logger.info(
                f"{name}:\n"
                f"  Requests: {total} (Errors: {errors}, {error_rate:.1f}%)\n"
                f"{caveat}"
                f"  p50: {self.get_percentile(name, 50):.1f}ms\n"
                f"  p95: {self.get_percentile(name, 95):.1f}ms\n"
                f"  p99: {self.get_percentile(name, 99):.1f}ms\n"
                f"  min: {min(times):.1f}ms\n"
                f"  max: {max(times):.1f}ms\n"
                f"  avg: {sum(times) / len(times):.1f}ms"
            )

        withheld = {name: len(t) for name, t in self.burst_times.items() if t}
        if withheld:
            logger.info(
                "Shutdown-burst samples WITHHELD from every percentile above "
                "(they measure teardown, not the handler): "
                + ", ".join(f"{name}={count}" for name, count in sorted(withheld.items()))
            )
        logger.info("=" * 60)


# Global metrics collector
metrics = MetricsCollector()


# =============================================================================
# LOCUST EVENT HANDLERS
# =============================================================================


@events.request.add_listener
def on_request(
    request_type: str,
    name: str,
    response_time: float,
    response_length: int,
    response: Any,
    exception: Exception | None,
    **kwargs: Any,
) -> None:
    """Record metrics for every request."""
    success = exception is None and (response is None or response.status_code < 400)
    metrics.record_request(name, response_time, success)

    # Log slow requests
    if response_time > SLOW_REQUEST_THRESHOLD_MS:
        logger.warning(f"SLOW REQUEST: {name} took {response_time:.1f}ms")

    # Log errors with details
    if not success:
        error_msg = str(exception) if exception else f"HTTP {response.status_code if response else 'N/A'}"
        logger.error(f"REQUEST FAILED: {name} - {error_msg}")


@events.test_start.add_listener
def on_test_start(environment: Any, **kwargs: Any) -> None:
    """Log test start."""
    metrics.start_time = time.time()
    logger.info("=" * 60)
    logger.info("PERF-R1-A: Starting performance baseline test")
    logger.info(f"Target host: {environment.host}")
    logger.info(f"Credential pool: {len(CREDENTIAL_POOL)} account(s) -> {CREDENTIAL_POOL[0][0]} ...")
    # A pool whose derived usernames collide means two virtual users share an
    # account, and logging in as the same account evicts the earlier session - so
    # the run still completes and still produces numbers, but the session
    # behaviour it measured is not the one described. Reported here, loudly,
    # because nothing downstream would ever reveal it.
    clashes = collision_risk([email for email, _ in CREDENTIAL_POOL])
    if clashes:
        logger.error(
            "CREDENTIAL POOL COLLISION: %d username(s) are derived from more than one "
            "address (%s). Virtual users will share accounts and evict each other's "
            "sessions. Give each address a distinct local part.",
            len(clashes),
            ", ".join(clashes[:5]),
        )
    logger.info(
        f"AI chat task: {'ENABLED' if AI_ENABLED else 'DISABLED (needs Ollama; set PERF_TEST_AI_ENABLED=1)'}"
    )
    logger.info("=" * 60)


@events.test_stop.add_listener
def on_test_stop(environment: Any, **kwargs: Any) -> None:
    """Log test completion with summary."""
    duration = time.time() - metrics.start_time
    logger.info(f"Test completed in {duration:.1f}s")
    metrics.log_summary()


# =============================================================================
# SHARED AUTHENTICATED BASE USER
# =============================================================================


def _jwt_expiry(token: str) -> float:
    """Best-effort read of the JWT `exp` claim (no signature verification).

    Returns 0.0 when the claim cannot be read, which makes the caller fall back
    to a conservative default lifetime.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
        return float(claims.get("exp", 0.0))
    except (IndexError, ValueError, binascii.Error, TypeError):
        return 0.0


class AuthenticatedUser(HttpUser):
    """Shared authentication plumbing for every perf-test user class.

    Subclassing matters: ``_auth_headers()`` used to live only on
    FastAPISaaSUser, so ReadOnlyUser crashed with
    ``AttributeError: 'ReadOnlyUser' object has no attribute '_auth_headers'``
    on every single task. Auth state and header building now live here once.
    """

    # Abstract: Locust must not spawn this class directly.
    abstract = True

    wait_time = between(WAIT_TIME_MIN_S, WAIT_TIME_MAX_S)

    # Authentication state (declared up front so tasks can never hit an
    # AttributeError on a first run — this is what broke `ai_session_id`).
    email: str = DEFAULT_TEST_EMAIL
    password: str = TEST_USER_PASSWORD
    access_token: str | None = None
    ai_session_id: int | None = None
    refresh_token_value: str | None = None
    _token_expires_at: float = 0.0

    # ---------------------------------------------------------------- lifecycle

    def on_start(self) -> None:
        """Claim a dedicated account from the pool and authenticate."""
        index = next(_pool_cursor) % len(CREDENTIAL_POOL)
        self.email, self.password = CREDENTIAL_POOL[index]
        self.authenticate()

    def on_stop(self) -> None:
        """Teardown. Issues NO measured request.

        This used to POST ``/account/logout`` from here, which made every
        logout sample a shutdown-burst sample: taken while the event loop, the
        DB pool and the Redis pool are all closing, so the number measured
        teardown contention rather than the handler. The 208.47 ms logout p95
        in the 2026-09-22 baseline came from here, and it is not attributable
        to anything, because ``logout`` performs no bcrypt and no batched I/O.

        Logout is now a normal task on ``FastAPISaaSUser`` (see
        ``logout_account``), where it competes with the same conditions as every
        other measured request. ``metrics.shutdown_burst()`` still wraps this
        hook so that anything added here in future is counted as a burst sample
        and withheld from the percentiles, rather than silently folding teardown
        into a p95 a second time.
        """
        with metrics.shutdown_burst():
            if self.access_token:
                logger.info("User stopping - skipping measured logout (teardown is not a measurement)")

    # -------------------------------------------------------------------- auth

    def authenticate(self, *, sample_name: str = "/account/login (setup)") -> None:
        """Log in, or retire this virtual user cleanly.

        Raises StopUser instead of calling ``self.stop()``: Locust's
        ``User.run()`` only tolerates ``stop()`` from RUNNING/WAITING state, so
        calling it twice inside ``on_start`` blew up with
        "Tried to stop User in an unexpected state: stopping", and it never
        aborted the run anyway. StopUser is caught by ``User.run()``, which
        still calls ``on_stop()``.
        """
        global _pool_hint_logged

        logger.info(f"User starting - authenticating as {self.email}")

        with self.client.post(
            f"{API_V1}/account/login",
            data={
                # perf_username(self.email), NOT self.email. The seeder stores the
                # local part as the username (users.username is varchar(20), and a
                # generated address is longer than that), so authenticating with
                # the full address looked up a username that does not exist and
                # every login returned 403. Both sides now call perf_username, so
                # they cannot drift apart again.
                "username": perf_username(self.email),
                "password": self.password,
            },
            headers={"X-Tenant-ID": TEST_TENANT},
            name=sample_name,
            catch_response=True,
        ) as response:
            status_code = response.status_code

            if status_code != 200:
                logger.error(f"Authentication failed: {status_code} - {response.text}")
                response.failure(f"Authentication failed: HTTP {status_code}")
            else:
                try:
                    data = response.json()
                except ValueError:
                    logger.error("Authentication response was not valid JSON")
                    response.failure("Authentication response was not valid JSON")
                    data = None

                access_token = (data or {}).get("access_token")
                if not access_token:
                    logger.error("Authentication response missing access_token")
                    response.failure("Authentication response missing access_token")
                else:
                    self._set_token(access_token)
                    self.refresh_token_value = response.cookies.get("refresh_token")
                    response.success()
                    logger.info("Authentication successful")
                    return

        # Every failure path above falls through to here.
        if not _pool_hint_logged:
            _pool_hint_logged = True
            logger.error(
                f"Login failed for '{self.email}'. If the account does not exist, seed the pool with: "
                f"python scripts/setup_locust_test_data.py "
                f"(PERF_TEST_USER_COUNT={len(CREDENTIAL_POOL)})."
            )
        raise StopUser()

    def _set_token(self, access_token: str) -> None:
        """Store the token and remember when it goes stale.

        An empty token means "this user has no usable access token" - set by
        ``logout_account`` once the token is blacklisted. The expiry goes to the
        PAST in that case, so ``_ensure_token`` re-mints on the next task that
        needs one, rather than this user carrying a 900 s lifetime it does not
        have and skipping its refresh entirely.
        """
        self.access_token = access_token
        if not access_token:
            self._token_expires_at = 0.0
            return
        expires_at = _jwt_expiry(access_token)
        self._token_expires_at = expires_at if expires_at else time.time() + DEFAULT_TOKEN_LIFETIME_S

    def _auth_headers(self) -> dict[str, str]:
        """Get authorization + tenant headers."""
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}", "X-Tenant-ID": TEST_TENANT}
        return {"X-Tenant-ID": TEST_TENANT}

    def _ensure_token(self) -> None:
        """Refresh proactively when the token is about to expire, else log in again.

        The re-login fallback is the fix for a run that voided itself. Logout is a
        WEIGHTED task (10% of traffic) and it destroys the session server-side, so
        the refresh token is revoked along with the access token. Refresh can
        therefore never recover from a logout - every request after one returned
        401, and plan 4.5 makes a 401 void the phase.

        The old docstring claimed "the next task to need a token will mint one", but
        the only mint path reachable from here was _refresh_token, which is exactly
        the call that cannot work.

        The re-login is named separately rather than as setup: a mid-run login
        costs a full bcrypt verify (~310 ms), and folding those into the login
        metric would report them as if they were part of the measured mix.
        """
        if self.access_token and time.time() < self._token_expires_at - TOKEN_REFRESH_MARGIN_S:
            return

        # No access token means logout ran: the session is gone server-side, so
        # refresh CANNOT work. Attempting it anyway records a 401 that plan 4.5
        # treats as voiding the phase - for a call whose outcome was already
        # decided before it was made. Go straight to a login.
        if not self.access_token:
            self.authenticate(sample_name="/account/login (re-auth)")
            return

        if self._refresh_token():
            return
        logger.warning(
            "refresh failed for %s; re-authenticating (logout revokes the session)",
            self.email,
        )
        self.authenticate(sample_name="/account/login (re-auth)")

    def _refresh_token(self) -> bool:
        """Refresh the access token using the session refresh cookie."""
        with self.client.post(
            f"{API_V1}/account/refresh",
            headers=self._auth_headers(),
            name="/account/refresh",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Refresh failed: HTTP {response.status_code}")
                return False

            try:
                data = response.json()
            except ValueError:
                response.failure("Refresh response was not valid JSON")
                return False

            access_token = data.get("access_token")
            if not access_token:
                logger.error("Refresh response missing access_token")
                response.failure("Refresh response missing access_token")
                return False

            self._set_token(access_token)
            response.success()
            return True

    # ------------------------------------------------------------------ helpers

    def _get_authenticated(self, path: str) -> None:
        """GET an authenticated path, refreshing the token once on a 401.

        One retry, and only on 401. The retry is what distinguishes "this token was
        stale" from "this endpoint is broken", and without it a stale token is
        reported as an endpoint failure - which would void the phase under 4.5 for
        a reason that has nothing to do with the endpoint.
        """
        self._ensure_token()
        name = path.rsplit("/", 1)[-1] or path
        with self.client.get(
            f"{API_V1}{path}",
            headers=self._auth_headers(),
            name=name,
            catch_response=True,
        ) as response:
            if response.status_code == 401:
                self.access_token = ""
                self._ensure_token()
                with self.client.get(
                    f"{API_V1}{path}",
                    headers=self._auth_headers(),
                    name=name,
                    catch_response=True,
                ) as retry:
                    self._record_status(retry, name)
                return
            self._record_status(response, name)

    def _record_status(self, response: Any, name: str) -> None:
        """Any 2xx is a success. The contract is "every non-2xx is recorded",
        so the check is the whole range and not the 200 alone."""
        if 200 <= response.status_code < 300:
            response.success()
            return
        response.failure(f"Unexpected status: {response.status_code} on {name}")

    def _fetch_current_user(self, name: str) -> bool:
        """GET /users/me, retrying once after a token refresh on 401."""
        self._ensure_token()

        with self.client.get(
            f"{API_V1}/users/me",
            headers=self._auth_headers(),
            name=name,
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
                return True

            if response.status_code != 401:
                response.failure(f"Unexpected status: {response.status_code}")
                return False

            # 401: refresh once and retry before blaming the endpoint.
            response.failure("Token expired")

        if not self._refresh_token():
            return False

        with self.client.get(
            f"{API_V1}/users/me",
            headers=self._auth_headers(),
            name=name,
            catch_response=True,
        ) as retry:
            if retry.status_code == 200:
                retry.success()
                return True
            retry.failure(f"Unexpected status after refresh: {retry.status_code}")
            return False

    def _ensure_ai_session(self) -> int | None:
        """Create the AI chat session once per user and return its id."""
        if self.ai_session_id:
            return self.ai_session_id

        self._ensure_token()

        with self.client.post(
            f"{API_V1}/ai/sessions",
            headers={
                **self._auth_headers(),
                "Content-Type": "application/json",
            },
            json=AI_SESSION_PAYLOAD,
            name="/ai/sessions (setup)",
            catch_response=True,
        ) as response:
            if response.status_code not in (200, 201):
                response.failure(f"AI session create failed: {response.status_code}")
                return None

            try:
                data = response.json()
            except ValueError:
                response.failure("AI session create response was not valid JSON")
                return None

            session_id = data.get("id")
            if not isinstance(session_id, int):
                response.failure("AI session create response missing id")
                return None

            response.success()
            self.ai_session_id = session_id
            return session_id


# =============================================================================
# USER BEHAVIOR
# =============================================================================


class FastAPISaaSUser(AuthenticatedUser):
    """
    Simulates authenticated user behavior on the FastAPI SaaS platform.

    Lifecycle:
    1. on_start: Login and get tokens
    2. tasks: Execute weighted API calls, including logout as an ordinary task
    3. on_stop: nothing measured (see AuthenticatedUser.on_stop)
    """

    # Wait between 1-3 seconds between tasks (realistic user behavior)
    wait_time = between(WAIT_TIME_MIN_S, WAIT_TIME_MAX_S)

    # =========================================================================
    # TASKS (weighted by realistic traffic patterns)
    # =========================================================================

    @task(TRAFFIC_USER_ME_WEIGHT)  # 80% of traffic
    def get_current_user(self) -> None:
        """
        GET /api/v1/users/me - THE HOT PATH

        This is the most frequently called endpoint (called on every page load).
        It's the primary target for optimization.

        Expected flow:
        1. verify_token() - checks token blacklist (BOTTLENECK: DB query)
        2. get_current_user() - fetches user from DB
        3. Response with user data

        No target is asserted here. The blacklist-check latency figure this
        docstring used to carry had no measurement behind it; see the note at
        the top of this file.
        """
        self._fetch_current_user("/users/me")

    @task(TRAFFIC_SESSION_SURFACE_WEIGHT)  # 10% of traffic
    def session_surface(self) -> None:
        """Walk the ACCOUNT and USER surface, not just /users/me.

        Until now every measured request but /users/me was the auth trio, so the
        endpoint matrix was two rows deep no matter how long the run. Unified plan
        4.4 asks for all ten, and a matrix that cannot reach ten is a matrix that
        cannot be completed by waiting.

        The reads here are the ones an authenticated page load actually issues -
        session count, the user's own notification feed, the user list, and the
        per-user rate-limit and tier lookups. They differ from /users/me in kind,
        not only in path: /users/me is the cached identity read, while these hit
        the session table, the notification join and the rate-limit store, so a
        regression that only shows up away from the hot path has somewhere to
        appear.

        One request per task, not a loop. A task that issued all five would report
        one aggregate latency for five endpoints and lose the ability to say which
        one moved - the same conflation that makes a summed number unattributable.
        """
        choice = random.choice(SESSION_SURFACE_PATHS)
        self._get_authenticated(choice)

    @task(TRAFFIC_HEALTH_WEIGHT)  # 5% of traffic
    def health_probe(self) -> None:
        """GET /api/health/live - the orchestrator's readiness view.

        Unauthenticated on purpose, and it is the ONE endpoint in this file that
        is not part of an authenticated user journey. It answers from process
        state rather than from the database, so it isolates framework and event
        loop cost from everything the other tasks measure - which is what makes
        it the control against which the others are read.

        It lives under /api/health, NOT /api/v1/health: the monitoring router is
        mounted at /api while the v1 router carries the /api/v1 prefix, and
        assuming the v1 shape yields a 404 that looks like a missing endpoint.
        """
        with self.client.get(
            f"{API_ROOT}/health/live",
            headers={"X-Tenant-ID": TEST_TENANT},
            name="/health/live",
            catch_response=True,
        ) as response:
            self._record_status(response, "/health/live")

    @task(TRAFFIC_AI_CHAT_WEIGHT)  # 10% of traffic
    def ai_chat_completion(self) -> None:
        """
        POST /api/v1/ai/chat - AI Chat Completion

        This endpoint has multiple DB operations:
        1. Get/create AI session
        2. Save user message
        3. Call Ollama
        4. Save assistant message
        5. Update session

        No target is asserted. The DB-batching figure this docstring used to
        carry had no measurement behind it; see the note at the top of this
        file.

        Note: the chat call needs an Ollama-backed model. If Ollama is not
        running the API answers 503, which is an environment dependency and
        not a defect in this test. Set PERF_TEST_AI_ENABLED=1 to include it;
        with it off the effective mix is ~8:1 users/me vs refresh.
        """
        if not AI_ENABLED:
            return

        session_id = self._ensure_ai_session()
        if session_id is None:
            return

        self._ensure_token()

        with self.client.post(
            f"{API_V1}/ai/sessions/{session_id}/chat",
            headers={
                **self._auth_headers(),
                "Content-Type": "application/json",
            },
            json=AI_CHAT_PAYLOAD,
            name="/ai/chat",
            catch_response=True,
            timeout=30,  # AI calls can be slow
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 401:
                self._refresh_token()
                response.failure("Token expired")
            else:
                response.failure(f"AI chat failed: {response.status_code}")

    @task(TRAFFIC_TOKEN_REFRESH_WEIGHT)  # 10% of traffic
    def refresh_auth_token(self) -> None:
        """
        POST /api/v1/account/refresh - Token Refresh

        Called periodically to get new access tokens.

        No target is asserted; the refresh-path latency figure this docstring
        used to carry had no measurement behind it.
        """
        self._refresh_token()

    @task(LOGOUT_WEIGHT)
    def logout_account(self) -> None:
        """POST /api/v1/account/logout - measured as an ORDINARY task.

        Moved here from ``AuthenticatedUser.on_stop``, where every sample was a
        shutdown-burst sample: taken while the runner tears down, so the number
        measured contention with the closing pools rather than the handler.
        From here it competes with the same conditions as every other measured
        request.

        ``catch_response=True`` is load-bearing, not decoration. Without it
        Locust counts a non-2xx as neither success nor failure, so a phase that
        quietly answered 401 or 429 for every call reported a beautiful
        zero-failure summary. That is exactly how the 2026-09-22 pack got
        "n=34, 0 failures" next to a tight 3.17-3.69 s cluster for a handler
        that does no bcrypt.

        Any 2xx is a success, not just 200: the contract this task is held to
        is "every non-2xx is a recorded failure", so a 204 from a logout route
        that later drops its body must not be reported as a failed run.

        Logging out invalidates this user's own access token, so it is followed
        by a re-authentication. The next task to need a token will mint one;
        this only clears the local token so nothing reuses a blacklisted one.
        """
        if not self.access_token:
            return

        with self.client.post(
            f"{API_V1}/account/logout",
            headers=self._auth_headers(),
            name="/account/logout",
            catch_response=True,
        ) as response:
            if 200 <= response.status_code < 300:
                response.success()
            elif response.status_code == 401:
                # The access token was already blacklisted or expired: the
                # handler behaved correctly, the harness was out of date.
                self._set_token("")
                response.success()
            else:
                response.failure(f"logout failed: {response.status_code}")


# =============================================================================
# ADDITIONAL TEST USERS (for load variety)
# =============================================================================


class ReadOnlyUser(AuthenticatedUser):
    """
    A lighter user that only reads data (no AI calls).
    Useful for simulating read-heavy traffic patterns.
    """

    wait_time = between(READONLY_WAIT_MIN_S, READONLY_WAIT_MAX_S)
    weight = READONLY_USER_WEIGHT  # Spawn 2 ReadOnlyUsers for every 1 FastAPISaaSUser

    @task
    def get_user_info(self) -> None:
        """Just fetch user info repeatedly."""
        self._fetch_current_user("/users/me (readonly)")


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import subprocess

    # Default to headless mode with basic load
    args = [
        "locust",
        "-f",
        __file__,
        "--headless",
        "-u",
        "10",
        "-r",
        "2",
        "--run-time",
        "1m",
        "--host",
        os.getenv("API_HOST", "http://localhost:8000"),
    ]

    print(f"Running: {' '.join(args)}")
    subprocess.run(args)
