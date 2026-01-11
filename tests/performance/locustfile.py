"""
PERF-R1-A: Locust Load Test for FastAPI SaaS Hot Path
======================================================

Purpose: Establish baseline performance metrics before optimization.

Traffic Distribution (simulating realistic production patterns):
- 80% GET /api/v1/users/me (authenticated user info - THE HOT PATH)
- 10% POST /api/v1/ai/chat (AI completions)
- 10% POST /api/v1/auth/refresh (token refresh)

Target Metrics:
| Endpoint        | Current p95 | Target p95 |
|-----------------|-------------|------------|
| /users/me       | ~50ms       | <15ms      |
| /ai/chat        | ~200ms+     | <100ms     |
| /auth/refresh   | ~30ms       | <10ms      |

Usage:
    # Basic run (10 users, 2 min):
    locust -f tests/performance/locustfile.py --headless -u 10 -r 2 --run-time 2m

    # Full baseline (100 users, 5 min):
    locust -f tests/performance/locustfile.py --headless -u 100 -r 10 --run-time 5m --csv=baseline

    # Web UI mode:
    locust -f tests/performance/locustfile.py
    # Then open http://localhost:8089

Requirements:
    pip install locust
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from locust import HttpUser, between, events, task

# =============================================================================
# CONFIGURATION
# =============================================================================

# API base path
API_V1 = "/api/v1"

# Test user credentials (should exist in test database)
TEST_USER_EMAIL = os.getenv("PERF_TEST_EMAIL", "perftest@example.com")
TEST_USER_PASSWORD = os.getenv("PERF_TEST_PASSWORD", "TestPassword123!")

# AI Chat test payload
AI_CHAT_PAYLOAD = {
    "session_id": None,  # Will be set dynamically
    "message": "Hello, this is a performance test message.",
    "model": "llama3.2",  # Fastest model for testing
}

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
                f"  avg: {sum(times)/len(times):.1f}ms"
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
    if response_time > 100:
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
    logger.info("=" * 60)


@events.test_stop.add_listener
def on_test_stop(environment: Any, **kwargs: Any) -> None:
    """Log test completion with summary."""
    duration = time.time() - metrics.start_time
    logger.info(f"Test completed in {duration:.1f}s")
    metrics.log_summary()


# =============================================================================
# USER BEHAVIOR
# =============================================================================

class FastAPISaaSUser(HttpUser):
    """
    Simulates authenticated user behavior on the FastAPI SaaS platform.

    Lifecycle:
    1. on_start: Login and get tokens
    2. tasks: Execute weighted API calls
    3. on_stop: Cleanup (logout)
    """

    # Wait between 1-3 seconds between tasks (realistic user behavior)
    wait_time = between(1, 3)

    # Store authentication tokens
    access_token: str | None = None
    refresh_token: str | None = None
    ai_session_id: str | None = None

    def on_start(self) -> None:
        """Authenticate user before running tasks."""
        logger.info(f"User starting - authenticating as {TEST_USER_EMAIL}")

        # Login to get tokens
        response = self.client.post(
            f"{API_V1}/auth/login",
            data={
                "username": TEST_USER_EMAIL,
                "password": TEST_USER_PASSWORD,
            },
            name="/auth/login (setup)",
        )

        if response.status_code == 200:
            data = response.json()
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token")
            logger.info("Authentication successful")
        else:
            logger.error(f"Authentication failed: {response.status_code} - {response.text}")
            # Don't fail completely - some endpoints might work without auth

    def on_stop(self) -> None:
        """Cleanup on user stop."""
        if self.access_token:
            logger.info("User stopping - logging out")
            self.client.post(
                f"{API_V1}/auth/logout",
                headers=self._auth_headers(),
                name="/auth/logout (cleanup)",
            )

    def _auth_headers(self) -> dict[str, str]:
        """Get authorization headers."""
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}

    # =========================================================================
    # TASKS (weighted by realistic traffic patterns)
    # =========================================================================

    @task(80)  # 80% of traffic
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
        with self.client.get(
            f"{API_V1}/users/me",
            headers=self._auth_headers(),
            name="/users/me",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 401:
                # Token expired - refresh it
                logger.debug("Token expired, refreshing...")
                self._refresh_token()
                response.failure("Token expired")
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(10)  # 10% of traffic
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
        """
        payload = AI_CHAT_PAYLOAD.copy()

        # Use existing session or let API create one
        if self.ai_session_id:
            payload["session_id"] = self.ai_session_id

        with self.client.post(
            f"{API_V1}/ai/chat/completions",
            headers={
                **self._auth_headers(),
                "Content-Type": "application/json",
            },
            json=payload,
            name="/ai/chat",
            catch_response=True,
            timeout=30,  # AI calls can be slow
        ) as response:
            if response.status_code == 200:
                data = response.json()
                # Cache session ID for subsequent calls
                self.ai_session_id = data.get("session_id")
                response.success()
            elif response.status_code == 401:
                self._refresh_token()
                response.failure("Token expired")
            else:
                response.failure(f"AI chat failed: {response.status_code}")

    @task(10)  # 10% of traffic
    def refresh_auth_token(self) -> None:
        """
        POST /api/v1/auth/refresh - Token Refresh

        Called periodically to get new access tokens.

        Target: p95 < 10ms (currently ~30ms)
        """
        self._refresh_token()

    def _refresh_token(self) -> None:
        """Internal method to refresh the access token."""
        if not self.refresh_token:
            logger.warning("No refresh token available")
            return

        with self.client.post(
            f"{API_V1}/auth/refresh",
            headers={"Authorization": f"Bearer {self.refresh_token}"},
            name="/auth/refresh",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token", self.refresh_token)
                response.success()
            else:
                response.failure(f"Refresh failed: {response.status_code}")


# =============================================================================
# ADDITIONAL TEST USERS (for load variety)
# =============================================================================

class ReadOnlyUser(HttpUser):
    """
    A lighter user that only reads data (no AI calls).
    Useful for simulating read-heavy traffic patterns.
    """

    wait_time = between(0.5, 2)
    weight = 2  # Spawn 2 ReadOnlyUsers for every 1 FastAPISaaSUser

    access_token: str | None = None

    def on_start(self) -> None:
        """Authenticate."""
        response = self.client.post(
            f"{API_V1}/auth/login",
            data={
                "username": TEST_USER_EMAIL,
                "password": TEST_USER_PASSWORD,
            },
            name="/auth/login (setup)",
        )
        if response.status_code == 200:
            self.access_token = response.json().get("access_token")

    @task
    def get_user_info(self) -> None:
        """Just fetch user info repeatedly."""
        if self.access_token:
            self.client.get(
                f"{API_V1}/users/me",
                headers={"Authorization": f"Bearer {self.access_token}"},
                name="/users/me (readonly)",
            )


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import subprocess

    # Default to headless mode with basic load
    args = [
        "locust",
        "-f", __file__,
        "--headless",
        "-u", "10",
        "-r", "2",
        "--run-time", "1m",
        "--host", os.getenv("API_HOST", "http://localhost:8000"),
    ]

    print(f"Running: {' '.join(args)}")
    subprocess.run(args)
