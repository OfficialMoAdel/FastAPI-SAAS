"""
E2E Testing Utilities - Shared components for all E2E scripts.

Based on MCP docs patterns (Jan 2026):
- httpx AsyncClient with granular timeouts
- Metrics collection with p95/p99 percentiles
- Structured logging format
- CLI argument handling
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

# =============================================================================
# Constants
# =============================================================================

DEFAULT_HOST = "http://localhost:8000"
DEFAULT_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 5.0
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# =============================================================================
# Enums
# =============================================================================


class TestStatus(str, Enum):
    """Test execution status."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class LogLevel(str, Enum):
    """Log level options."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class RequestMetrics:
    """Metrics collected from a batch of requests."""

    total: int = 0
    successful: int = 0
    failed: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.total == 0:
            return 0.0
        return (self.successful / self.total) * 100

    @property
    def avg_latency_ms(self) -> float:
        """Calculate average latency."""
        if not self.latencies_ms:
            return 0.0
        return statistics.mean(self.latencies_ms)

    @property
    def p50_latency_ms(self) -> float:
        """Calculate median (p50) latency."""
        if not self.latencies_ms:
            return 0.0
        return statistics.median(self.latencies_ms)

    @property
    def p95_latency_ms(self) -> float:
        """Calculate p95 latency."""
        if len(self.latencies_ms) < 20:
            return max(self.latencies_ms) if self.latencies_ms else 0.0
        return statistics.quantiles(self.latencies_ms, n=20)[18]

    @property
    def p99_latency_ms(self) -> float:
        """Calculate p99 latency."""
        if len(self.latencies_ms) < 100:
            return max(self.latencies_ms) if self.latencies_ms else 0.0
        return statistics.quantiles(self.latencies_ms, n=100)[98]

    @property
    def requests_per_second(self) -> float:
        """Calculate RPS based on total time."""
        if not self.latencies_ms:
            return 0.0
        total_time_s = sum(self.latencies_ms) / 1000
        if total_time_s == 0:
            return 0.0
        return self.total / total_time_s

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "total": self.total,
            "successful": self.successful,
            "failed": self.failed,
            "success_rate_percent": round(self.success_rate, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
        }


@dataclass
class TestResult:
    """Result of a single test case."""

    name: str
    status: TestStatus
    duration_ms: float
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "status": self.status.value,
            "duration_ms": round(self.duration_ms, 2),
            "message": self.message,
            "details": self.details,
        }


@dataclass
class E2EReport:
    """Complete E2E test run report."""

    script_name: str
    started_at: datetime
    ended_at: datetime | None = None
    results: list[TestResult] = field(default_factory=list)
    metrics: RequestMetrics | None = None
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        """Calculate total duration in seconds."""
        if not self.ended_at:
            return 0.0
        return (self.ended_at - self.started_at).total_seconds()

    @property
    def passed_count(self) -> int:
        """Count passed tests."""
        return sum(1 for r in self.results if r.status == TestStatus.PASSED)

    @property
    def failed_count(self) -> int:
        """Count failed tests."""
        return sum(1 for r in self.results if r.status == TestStatus.FAILED)

    @property
    def all_passed(self) -> bool:
        """Check if all tests passed."""
        return self.failed_count == 0 and len(self.results) > 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "script_name": self.script_name,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_s": round(self.duration_s, 2),
            "summary": {
                "total": len(self.results),
                "passed": self.passed_count,
                "failed": self.failed_count,
                "all_passed": self.all_passed,
            },
            "results": [r.to_dict() for r in self.results],
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "config": self.config,
        }

    def save_json(self, path: Path) -> None:
        """Save report to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


# =============================================================================
# HTTP Client
# =============================================================================


def create_client(
    base_url: str = DEFAULT_HOST,
    timeout: float = DEFAULT_TIMEOUT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
) -> httpx.AsyncClient:
    """
    Create configured httpx AsyncClient.

    Uses granular timeout control per MCP httpx docs.
    """
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(
            timeout,
            connect=connect_timeout,
            read=timeout,
            write=timeout,
            pool=connect_timeout,
        ),
        follow_redirects=True,
        headers={"User-Agent": "E2E-Test-Suite/1.0"},
    )


async def timed_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> tuple[httpx.Response | None, float, str | None]:
    """
    Make a timed HTTP request.

    Returns: (response, latency_ms, error_message)
    """
    start = time.perf_counter()
    try:
        response = await client.request(method, url, **kwargs)
        latency_ms = (time.perf_counter() - start) * 1000
        return response, latency_ms, None
    except httpx.TimeoutException as e:
        latency_ms = (time.perf_counter() - start) * 1000
        return None, latency_ms, f"Timeout: {e}"
    except httpx.HTTPError as e:
        latency_ms = (time.perf_counter() - start) * 1000
        return None, latency_ms, f"HTTP Error: {e}"
    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000
        return None, latency_ms, f"Error: {e}"


async def concurrent_requests(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    num_requests: int,
    concurrency: int = 10,
    **kwargs: Any,
) -> RequestMetrics:
    """
    Execute concurrent requests with semaphore-based rate limiting.

    Per MCP docs: Use semaphore for concurrency control.
    """
    semaphore = asyncio.Semaphore(concurrency)
    metrics = RequestMetrics()

    async def bounded_request() -> None:
        async with semaphore:
            response, latency_ms, error = await timed_request(client, method, url, **kwargs)
            metrics.total += 1
            metrics.latencies_ms.append(latency_ms)
            if response and 200 <= response.status_code < 400:
                metrics.successful += 1
            else:
                metrics.failed += 1

    tasks = [bounded_request() for _ in range(num_requests)]
    await asyncio.gather(*tasks, return_exceptions=True)

    return metrics


# =============================================================================
# Logging
# =============================================================================


def setup_logging(
    script_name: str,
    level: LogLevel = LogLevel.INFO,
    log_file: Path | None = None,
) -> logging.Logger:
    """
    Configure logging with standard format.

    Format: [TIMESTAMP] [LEVEL] [SCRIPT] message
    """
    logger = logging.getLogger(script_name)
    logger.setLevel(getattr(logging, level.value.upper()))

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(console)

    # File handler (optional)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(file_handler)

    return logger


# =============================================================================
# CLI Argument Parsing
# =============================================================================


def create_base_parser(description: str) -> argparse.ArgumentParser:
    """Create base argument parser with common options."""
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument(
        "--host",
        type=str,
        default=DEFAULT_HOST,
        help=f"Target host URL (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose/debug logging",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first failure",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output JSON report file path",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )

    return parser


def add_load_test_args(parser: argparse.ArgumentParser) -> None:
    """Add load testing specific arguments."""
    parser.add_argument(
        "--users",
        "-u",
        type=int,
        default=10,
        help="Number of concurrent users (default: 10)",
    )
    parser.add_argument(
        "--duration",
        "-d",
        type=int,
        default=30,
        help="Test duration in seconds (default: 30)",
    )
    parser.add_argument(
        "--ramp-up",
        type=int,
        default=5,
        help="Ramp-up time in seconds (default: 5)",
    )


# =============================================================================
# Test Execution Helpers
# =============================================================================


async def run_test(
    name: str,
    test_func: Any,
    logger: logging.Logger,
    fail_fast: bool = False,
) -> TestResult:
    """
    Execute a test function and capture result.

    Handles exceptions and timing automatically.
    """
    logger.info(f"Running: {name}")
    start = time.perf_counter()

    try:
        result = await test_func()
        duration_ms = (time.perf_counter() - start) * 1000

        if result is True or result is None:
            logger.info(f"  PASSED ({duration_ms:.1f}ms)")
            return TestResult(
                name=name,
                status=TestStatus.PASSED,
                duration_ms=duration_ms,
            )
        else:
            msg = str(result) if result else "Test returned falsy value"
            logger.error(f"  FAILED: {msg} ({duration_ms:.1f}ms)")
            if fail_fast:
                raise SystemExit(1)
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                duration_ms=duration_ms,
                message=msg,
            )

    except AssertionError as e:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.error(f"  FAILED: {e} ({duration_ms:.1f}ms)")
        if fail_fast:
            raise SystemExit(1)
        return TestResult(
            name=name,
            status=TestStatus.FAILED,
            duration_ms=duration_ms,
            message=str(e),
        )

    except Exception as e:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.exception(f"  ERROR: {e} ({duration_ms:.1f}ms)")
        if fail_fast:
            raise SystemExit(1)
        return TestResult(
            name=name,
            status=TestStatus.ERROR,
            duration_ms=duration_ms,
            message=str(e),
        )


def print_summary(report: E2EReport, logger: logging.Logger) -> None:
    """Print test execution summary."""
    logger.info("=" * 60)
    logger.info(f"E2E Test Summary: {report.script_name}")
    logger.info("=" * 60)
    logger.info(f"Duration: {report.duration_s:.2f}s")
    logger.info(f"Total: {len(report.results)}")
    logger.info(f"Passed: {report.passed_count}")
    logger.info(f"Failed: {report.failed_count}")

    if report.metrics:
        logger.info("-" * 40)
        logger.info("Request Metrics:")
        logger.info(f"  Total Requests: {report.metrics.total}")
        logger.info(f"  Success Rate: {report.metrics.success_rate:.1f}%")
        logger.info(f"  Avg Latency: {report.metrics.avg_latency_ms:.1f}ms")
        logger.info(f"  P95 Latency: {report.metrics.p95_latency_ms:.1f}ms")
        logger.info(f"  P99 Latency: {report.metrics.p99_latency_ms:.1f}ms")

    logger.info("=" * 60)
    status = "PASSED" if report.all_passed else "FAILED"
    logger.info(f"Result: {status}")


def get_exit_code(report: E2EReport) -> int:
    """Determine exit code based on test results."""
    return 0 if report.all_passed else 1


def now_utc() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(UTC)
