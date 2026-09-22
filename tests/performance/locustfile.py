"""
PERF-R1-A: Locust Load Test for FastAPI SaaS Hot Path
======================================================

Purpose: Establish baseline performance metrics before optimization.

Traffic Distribution (simulating realistic production patterns):
- 80% GET /api/v1/users/me (authenticated user info - THE HOT PATH)
- 10% POST /api/v1/ai/chat (AI completions)
- 10% POST /api/v1/account/refresh (token refresh)

Target Metrics:
| Endpoint        | Current p95 | Target p95 |
|-----------------|-------------|------------|
| /users/me       | ~50ms       | <15ms      |
| /ai/chat        | ~200ms+     | <100ms     |
| /account/refresh   | ~30ms       | <10ms      |

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
import time
from typing import Any, Final

from locust import HttpUser, between, events, task
from locust.exception import StopUser

# =============================================================================
# CONFIGURATION
# =============================================================================

# API base path
API_V1: Final[str] = "/api/v1"

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
TRAFFIC_USER_ME_WEIGHT: Final[int] = 80
TRAFFIC_AI_CHAT_WEIGHT: Final[int] = 10
TRAFFIC_TOKEN_REFRESH_WEIGHT: Final[int] = 10

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
    """Collects and logs detailed performance metrics."""

    def __init__(self) -> None:
        self.request_times: dict[str, list[float]] = {}
        self.error_counts: dict[str, int] = {}
        self.start_time: float = 0

    def record_request(self, name: str, response_time: float, success: bool) -> None:
        """Record a single request's metrics."""
        if name not in self.request_times:
            self.request_times[name] = []
            self.error_counts[name] = 0

        if success:
            self.request_times[name].append(response_time)
        else:
            self.error_counts[name] += 1

    def get_percentile(self, name: str, percentile: float) -> float:
        """Calculate percentile for a given endpoint."""
        times = sorted(self.request_times.get(name, []))
        if not times:
            return 0.0
        index = int(len(times) * percentile / 100)
        return times[min(index, len(times) - 1)]

    def log_summary(self) -> None:
        """Log summary metrics at test end."""
        logger.info("=" * 60)
        logger.info("PERFORMANCE TEST SUMMARY")
        logger.info("=" * 60)

        for name, times in self.request_times.items():
            if not times:
                continue
            errors = self.error_counts.get(name, 0)
            total = len(times) + errors
            error_rate = (errors / total * 100) if total > 0 else 0

            logger.info(
                f"{name}:\n"
                f"  Requests: {total} (Errors: {errors}, {error_rate:.1f}%)\n"
                f"  p50: {self.get_percentile(name, 50):.1f}ms\n"
                f"  p95: {self.get_percentile(name, 95):.1f}ms\n"
                f"  p99: {self.get_percentile(name, 99):.1f}ms\n"
                f"  min: {min(times):.1f}ms\n"
                f"  max: {max(times):.1f}ms\n"
                f"  avg: {sum(times) / len(times):.1f}ms"
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
        """Cleanup on user stop."""
        if self.access_token:
            logger.info("User stopping - logging out")
            self.client.post(
                f"{API_V1}/account/logout",
                headers=self._auth_headers(),
                name="/account/logout (cleanup)",
            )

    # -------------------------------------------------------------------- auth

    def authenticate(self) -> None:
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
                "username": self.email,
                "password": self.password,
            },
            headers={"X-Tenant-ID": TEST_TENANT},
            name="/account/login (setup)",
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
        """Store the token and remember when it goes stale."""
        self.access_token = access_token
        expires_at = _jwt_expiry(access_token)
        self._token_expires_at = expires_at if expires_at else time.time() + DEFAULT_TOKEN_LIFETIME_S

    def _auth_headers(self) -> dict[str, str]:
        """Get authorization + tenant headers."""
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}", "X-Tenant-ID": TEST_TENANT}
        return {"X-Tenant-ID": TEST_TENANT}

    def _ensure_token(self) -> None:
        """Refresh proactively when the token is about to expire."""
        if self.access_token and time.time() < self._token_expires_at - TOKEN_REFRESH_MARGIN_S:
            return
        self._refresh_token()

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
    2. tasks: Execute weighted API calls
    3. on_stop: Cleanup (logout)
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

        Target: p95 < 15ms (currently ~50ms due to blacklist check)
        """
        self._fetch_current_user("/users/me")

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

        Target: Reduce DB portion from ~200ms to <100ms through batching

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

        Target: p95 < 10ms (currently ~30ms)
        """
        self._refresh_token()


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
