#!/usr/bin/env python3
"""Locust Load Test for Performance Validation.

Usage:
    locust -f load_test.py --headless -u 100 -r 10 --run-time 5m
    locust -f load_test.py  # Web UI mode

Metrics captured:
    - Request latency (p50, p95, p99)
    - Requests per second
    - Error rate
"""

import json
import os

from locust import HttpUser, between, events, task

# Configuration from environment
BASE_URL = os.getenv("LOAD_TEST_BASE_URL", "http://localhost:8000")
TEST_EMAIL = os.getenv("LOAD_TEST_EMAIL", "test@example.com")
TEST_PASSWORD = os.getenv("LOAD_TEST_PASSWORD", "testpassword")


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Log test start."""
    print(f"Load test starting with {environment.parsed_options.num_users} users")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Log test results."""
    stats = environment.stats.total
    print("\n" + "=" * 60)
    print("LOAD TEST RESULTS")
    print("=" * 60)
    print(f"Total Requests: {stats.num_requests}")
    print(f"Failures: {stats.num_failures}")
    print(f"Avg Response Time: {stats.avg_response_time:.2f}ms")
    print(f"p50: {stats.get_response_time_percentile(0.50):.2f}ms")
    print(f"p95: {stats.get_response_time_percentile(0.95):.2f}ms")
    print(f"p99: {stats.get_response_time_percentile(0.99):.2f}ms")
    print(f"RPS: {stats.total_rps:.2f}")
    print("=" * 60)

    # Save results to file
    results = {
        "total_requests": stats.num_requests,
        "failures": stats.num_failures,
        "avg_response_time_ms": stats.avg_response_time,
        "p50_ms": stats.get_response_time_percentile(0.50),
        "p95_ms": stats.get_response_time_percentile(0.95),
        "p99_ms": stats.get_response_time_percentile(0.99),
        "rps": stats.total_rps,
    }

    with open("results/load_test_results.json", "w") as f:
        json.dump(results, f, indent=2)


class SaaSUser(HttpUser):
    """Simulated SaaS user for load testing."""

    wait_time = between(1, 3)  # 1-3 seconds between requests
    host = BASE_URL

    def on_start(self):
        """Login at start of user session."""
        # Try to authenticate
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
            catch_response=True,
        )

        if response.ok:
            data = response.json()
            self.token = data.get("access_token", "")
            self.headers = {"Authorization": f"Bearer {self.token}"}
            response.success()
        else:
            self.token = ""
            self.headers = {}
            response.failure("Login failed")

    @task(10)
    def auth_flow(self):
        """Test authentication endpoints (most common)."""
        self.client.post(
            "/api/v1/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
            name="/auth/login",
        )

    @task(5)
    def tenant_lookup(self):
        """Test tenant resolution (tests slug normalization, negative cache)."""
        self.client.get(
            "/api/v1/tenants/my-tenant",
            headers=self.headers,
            name="/tenants/{slug}",
        )

    @task(3)
    def file_quota(self):
        """Test file quota endpoint (tests quota fusion)."""
        self.client.get(
            "/api/v1/files/quota",
            headers=self.headers,
            name="/files/quota",
        )

    @task(5)
    def user_profile(self):
        """Test user profile endpoint (tests N+1 elimination, projection)."""
        self.client.get(
            "/api/v1/users/me",
            headers=self.headers,
            name="/users/me",
        )

    @task(2)
    def user_with_rate_limits(self):
        """Test user with rate limits (tests joinedload)."""
        self.client.get(
            "/api/v1/users/me/rate-limits",
            headers=self.headers,
            name="/users/me/rate-limits",
        )

    @task(1)
    def health_check(self):
        """Test health endpoint."""
        self.client.get("/health", name="/health")


class HighLoadUser(HttpUser):
    """High-load user for stress testing."""

    wait_time = between(0.1, 0.5)  # More aggressive

    @task
    def rapid_requests(self):
        """Rapid fire requests to test rate limiting and caching."""
        self.client.get("/health")


if __name__ == "__main__":
    import subprocess

    subprocess.run(["locust", "-f", __file__, "--headless", "-u", "50", "-r", "5", "--run-time", "1m"])
