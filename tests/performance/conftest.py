"""
Performance test fixtures using pytest-benchmark patterns.

Based on latest pytest-benchmark docs (Jan 2026):
- Direct benchmark(func, *args) calls preferred
- benchmark.extra_info for metadata
- Hooks for CI comparison
"""

from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

if TYPE_CHECKING:
    pass


# Performance thresholds in milliseconds (p95 targets from task spec)
PERF_THRESHOLDS_MS: dict[str, float] = {
    "users_me": 15.0,  # /users/me p95 < 15ms
    "auth_refresh": 10.0,  # /auth/refresh p95 < 10ms
    "ai_chat_db": 100.0,  # AI chat DB operations p95 < 100ms
    "cache_operations": 5.0,  # Cache operations p95 < 5ms
    "serialization": 0.1,  # 10k serializations/sec = 0.1ms each
}


@pytest.fixture
def perf_thresholds() -> dict[str, float]:
    """Return performance thresholds for assertions."""
    return PERF_THRESHOLDS_MS.copy()


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Mock Redis client for cache benchmarks."""
    redis = AsyncMock()
    redis.get.return_value = b'{"user_id": 1, "email": "test@example.com"}'
    redis.set.return_value = True
    redis.delete.return_value = 1
    redis.exists.return_value = 1
    redis.ttl.return_value = 3600
    redis.incr.return_value = 1
    redis.expire.return_value = True
    # Lua script support
    redis.evalsha.return_value = [1, 3600]
    redis.script_load.return_value = "abc123sha"
    return redis


@pytest.fixture
def benchmark_metadata(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Metadata for benchmark JSON output."""
    return {
        "ci_run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "commit_sha": os.environ.get("GITHUB_SHA", "unknown"),
        "branch": os.environ.get("GITHUB_REF_NAME", "local"),
        "test_name": request.node.name,
    }


def pytest_benchmark_update_json(
    config: Any,
    benchmarks: list[Any],
    output_json: dict[str, Any],
) -> None:
    """Add CI metadata to benchmark JSON output."""
    output_json["ci_metadata"] = {
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "commit": os.environ.get("GITHUB_SHA"),
        "branch": os.environ.get("GITHUB_REF_NAME"),
        "actor": os.environ.get("GITHUB_ACTOR"),
    }


def pytest_benchmark_compare_machine_info(
    config: Any,
    benchmarksession: Any,
    machine_info: dict[str, Any],
    compared_benchmark: dict[str, Any],
) -> None:
    """
    Validate machine info compatibility for CI comparisons.
    Allow comparisons even if machine differs (CI runners vary).
    """
    # In CI, we compare by test name, not machine specs
    pass
