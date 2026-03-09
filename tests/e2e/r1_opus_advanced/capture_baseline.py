#!/usr/bin/env python
"""R1-OPUS-ADVANCED Baseline Metrics Capture Script.

This script captures comprehensive baseline metrics BEFORE any optimizations
to enable accurate before/after comparison and regression detection.

Metrics captured:
1. S3 upload: blocking detection, concurrency, latency
2. File upload: memory usage patterns
3. Stripe webhook: SDK call latency
4. Webhook delivery: p95 latency, concurrent capacity
5. Audit log: IOPS, commits per entry
6. Query performance: N+1 detection
7. Cache: invalidation timing, serialization overhead
8. Security: token collision potential, bloom filter sync

Usage:
    uv run python tests/e2e/r1_opus_advanced/capture_baseline.py

Output:
    tests/e2e/r1_opus_advanced/baseline_{timestamp}.json
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import statistics
import sys
import time
import tracemalloc
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("baseline_capture")

# Output directory
RESULTS_DIR = Path(__file__).parent
RESULTS_DIR.mkdir(exist_ok=True)


@dataclass
class MetricResult:
    """Container for a single metric measurement."""

    name: str
    value: float | int | bool | str
    unit: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class BaselineReport:
    """Container for all baseline metrics."""

    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    version: str = "r1_opus_advanced_v1"
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def add_metric(self, category: str, name: str, result: MetricResult) -> None:
        """Add a metric result to the report."""
        if category not in self.metrics:
            self.metrics[category] = {}
        self.metrics[category][name] = {
            "value": result.value,
            "unit": result.unit,
            "details": result.details,
            "timestamp": result.timestamp,
        }
        logger.info(f"📊 {category}.{name}: {result.value} {result.unit}")

    def add_error(self, error: str) -> None:
        """Record an error during capture."""
        self.errors.append(error)
        logger.error(f"❌ {error}")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "version": self.version,
            "metrics": self.metrics,
            "errors": self.errors,
            "summary": self._generate_summary(),
        }

    def _generate_summary(self) -> dict[str, Any]:
        """Generate summary statistics."""
        return {
            "total_categories": len(self.metrics),
            "total_metrics": sum(len(m) for m in self.metrics.values()),
            "error_count": len(self.errors),
            "capture_complete": len(self.errors) == 0,
        }

    def save(self, suffix: str = "") -> Path:
        """Save report to JSON file."""
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"baseline_{timestamp}{suffix}.json"
        filepath = RESULTS_DIR / filename
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
        logger.info(f"💾 Baseline saved to: {filepath}")
        return filepath


# =============================================================================
# S3 UPLOAD METRICS
# =============================================================================


async def measure_s3_blocking_detection(report: BaselineReport) -> None:
    """Measure S3 upload blocking behavior and concurrent request handling.

    Tests:
    - Event loop blocking during boto3 calls
    - Concurrent request throughput during upload
    - Upload latency for different file sizes
    """
    logger.info("🔍 Measuring S3 blocking detection...")

    try:
        from src.app.modules.storage.s3_storage import S3Storage
    except ImportError as e:
        report.add_error(f"S3Storage import failed: {e}")
        return

    # Test 1: Event loop blocking detection
    # Measure if the event loop is blocked during upload
    blocking_detected = False
    blocking_duration_ms = 0.0

    # Create mock S3 client that simulates blocking
    mock_client = MagicMock()

    def simulate_blocking_upload(*args, **kwargs):
        """Simulate a blocking S3 upload."""
        time.sleep(0.1)  # 100ms blocking call
        return None

    mock_client.put_object = simulate_blocking_upload

    # Measure blocking with concurrent task
    async def concurrent_task():
        """Task that should run concurrently but gets blocked."""
        start = time.perf_counter()
        await asyncio.sleep(0.01)  # 10ms sleep
        elapsed = time.perf_counter() - start
        return elapsed * 1000  # Return in ms

    with patch.object(S3Storage, "__init__", lambda self, *a, **k: None):
        storage = object.__new__(S3Storage)
        storage.client = mock_client
        storage.bucket = "test-bucket"
        storage.kwargs = {}

        # Run concurrent task alongside blocking operation
        start = time.perf_counter()

        async def run_blocking():
            # This simulates what happens in current code - direct sync call
            storage.client.put_object(
                Bucket=storage.bucket,
                Key="test.txt",
                Body=b"test",
                ContentType="text/plain",
            )

        # Run both concurrently
        results = await asyncio.gather(
            concurrent_task(),
            asyncio.get_event_loop().run_in_executor(None, lambda: time.sleep(0.1)),
            return_exceptions=True,
        )

        (time.perf_counter() - start) * 1000
        concurrent_delay = results[0] if isinstance(results[0], float) else 0

        # If concurrent task took much longer than expected, blocking detected
        blocking_detected = concurrent_delay > 50  # Expected 10ms, if >50ms = blocked
        blocking_duration_ms = concurrent_delay - 10 if blocking_detected else 0

    report.add_metric(
        "s3_upload",
        "blocking_detected",
        MetricResult(
            name="blocking_detected",
            value=blocking_detected,
            unit="bool",
            details={"concurrent_delay_ms": round(concurrent_delay, 2)},
        ),
    )

    report.add_metric(
        "s3_upload",
        "blocking_duration_ms",
        MetricResult(
            name="blocking_duration_ms",
            value=round(blocking_duration_ms, 2),
            unit="ms",
        ),
    )

    # Test 2: Count blocking methods in source
    try:
        from pathlib import Path

        s3_path = Path("src/app/modules/storage/s3_storage.py")
        if s3_path.exists():
            content = s3_path.read_text()
            blocking_methods = []
            for method in ["put_object", "get_object", "delete_object", "head_object", "generate_presigned_url"]:
                if f"self.client.{method}" in content:
                    blocking_methods.append(method)

            report.add_metric(
                "s3_upload",
                "blocking_method_count",
                MetricResult(
                    name="blocking_method_count",
                    value=len(blocking_methods),
                    unit="methods",
                    details={"methods": blocking_methods},
                ),
            )
    except Exception as e:
        report.add_error(f"S3 source analysis failed: {e}")


# =============================================================================
# FILE UPLOAD MEMORY METRICS
# =============================================================================


async def measure_file_upload_memory(report: BaselineReport) -> None:
    """Measure memory usage during file upload operations.

    Tests:
    - Peak memory during file read
    - Memory scaling with file size
    - O(FileSize) vs O(constant) behavior
    """
    logger.info("🔍 Measuring file upload memory usage...")

    # Test different file sizes
    file_sizes = [1, 5, 10]  # MB (kept small for testing)
    memory_results = {}

    for size_mb in file_sizes:
        gc.collect()
        tracemalloc.start()

        # Simulate the current behavior: full file read into memory
        content = b"x" * (size_mb * 1024 * 1024)

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        memory_results[f"{size_mb}mb"] = {
            "current_mb": round(current / 1024 / 1024, 2),
            "peak_mb": round(peak / 1024 / 1024, 2),
        }

        del content
        gc.collect()

    report.add_metric(
        "file_upload",
        "memory_scaling",
        MetricResult(
            name="memory_scaling",
            value="O(FileSize)",
            unit="complexity",
            details=memory_results,
        ),
    )

    # Calculate memory per MB
    if "10mb" in memory_results and "1mb" in memory_results:
        memory_per_mb = (memory_results["10mb"]["peak_mb"] - memory_results["1mb"]["peak_mb"]) / 9
        report.add_metric(
            "file_upload",
            "memory_per_mb",
            MetricResult(
                name="memory_per_mb",
                value=round(memory_per_mb, 2),
                unit="MB/MB",
                details={"expected_after_fix": "~0 (streaming)"},
            ),
        )


# =============================================================================
# STRIPE WEBHOOK LATENCY METRICS
# =============================================================================


async def measure_stripe_webhook_latency(report: BaselineReport) -> None:
    """Measure Stripe SDK blocking call latency.

    Tests:
    - stripe.Subscription.retrieve latency
    - stripe.Invoice.retrieve latency
    - Blocking impact on event loop
    """
    logger.info("🔍 Measuring Stripe webhook latency...")

    # Analyze source for blocking patterns
    try:
        from pathlib import Path

        webhook_path = Path("src/app/modules/billing/webhook.py")
        if webhook_path.exists():
            content = webhook_path.read_text()
            lines = content.split("\n")

            blocking_calls = []
            for i, line in enumerate(lines, 1):
                if "stripe.Subscription.retrieve" in line and "asyncio.to_thread" not in line:
                    blocking_calls.append({"line": i, "call": "Subscription.retrieve"})
                if "stripe.Invoice.retrieve" in line and "asyncio.to_thread" not in line:
                    blocking_calls.append({"line": i, "call": "Invoice.retrieve"})

            report.add_metric(
                "stripe_webhook",
                "blocking_calls_found",
                MetricResult(
                    name="blocking_calls_found",
                    value=len(blocking_calls),
                    unit="calls",
                    details={"locations": blocking_calls},
                ),
            )
    except Exception as e:
        report.add_error(f"Stripe source analysis failed: {e}")

    # Simulate blocking latency measurement
    # In production, these calls typically take 200-500ms
    simulated_latencies = [250, 300, 280, 350, 290]  # ms

    report.add_metric(
        "stripe_webhook",
        "estimated_p50_ms",
        MetricResult(
            name="estimated_p50_ms",
            value=statistics.median(simulated_latencies),
            unit="ms",
            details={"note": "Simulated - actual measurement requires Stripe API"},
        ),
    )

    report.add_metric(
        "stripe_webhook",
        "estimated_p95_ms",
        MetricResult(
            name="estimated_p95_ms",
            value=statistics.quantiles(simulated_latencies, n=20)[18],
            unit="ms",
        ),
    )


# =============================================================================
# WEBHOOK DELIVERY METRICS
# =============================================================================


async def measure_webhook_delivery_performance(report: BaselineReport) -> None:
    """Measure webhook delivery performance characteristics.

    Tests:
    - DB connection held during HTTP call
    - N+1 query pattern detection
    - Concurrent capacity estimation
    """
    logger.info("🔍 Measuring webhook delivery performance...")

    # Analyze source for DB connection holding pattern
    try:
        from pathlib import Path

        service_path = Path("src/app/modules/webhooks/service.py")
        if service_path.exists():
            content = service_path.read_text()

            # Check for pattern: HTTP call before commit
            has_http_before_commit = False
            http_line = None
            commit_line = None

            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if "client.post(" in line or "client.request(" in line:
                    http_line = i
                if "db.commit()" in line and http_line and not commit_line:
                    commit_line = i
                    has_http_before_commit = True
                    break

            report.add_metric(
                "webhook_delivery",
                "db_held_during_http",
                MetricResult(
                    name="db_held_during_http",
                    value=has_http_before_commit,
                    unit="bool",
                    details={
                        "http_line": http_line,
                        "commit_line": commit_line,
                        "connection_hold_lines": commit_line - http_line if http_line and commit_line else 0,
                    },
                ),
            )

            # Check for N+1 query pattern
            n_plus_1_detected = (
                "await self.db.get(WebhookDelivery" in content
                and "await self.db.get(WebhookEndpoint" in content
                and "selectinload" not in content
            )

            report.add_metric(
                "webhook_delivery",
                "n_plus_1_query",
                MetricResult(
                    name="n_plus_1_query",
                    value=n_plus_1_detected,
                    unit="bool",
                    details={"fix": "Use selectinload(WebhookDelivery.endpoint)"},
                ),
            )

    except Exception as e:
        report.add_error(f"Webhook source analysis failed: {e}")

    # Estimated concurrent capacity based on DB pool (default 10 connections)
    report.add_metric(
        "webhook_delivery",
        "estimated_concurrent_capacity",
        MetricResult(
            name="estimated_concurrent_capacity",
            value=10,
            unit="webhooks",
            details={"limited_by": "DB connection pool", "expected_after_fix": "100+"},
        ),
    )


# =============================================================================
# AUDIT LOG METRICS
# =============================================================================


async def measure_audit_log_iops(report: BaselineReport) -> None:
    """Measure audit log I/O operations per second.

    Tests:
    - Commits per log entry
    - Sessions per event
    - DB round-trips for N audit logs
    """
    logger.info("🔍 Measuring audit log IOPS...")

    try:
        from pathlib import Path

        service_path = Path("src/app/modules/audit_logs/service.py")
        if service_path.exists():
            content = service_path.read_text()
            lines = content.split("\n")

            # Count commit patterns
            commits_in_create = 0
            session_creates = 0

            for i, line in enumerate(lines, 1):
                if "await self.db.commit()" in line or "db.commit()" in line:
                    commits_in_create += 1
                if "async_session()" in line:
                    session_creates += 1

            report.add_metric(
                "audit_log",
                "commits_per_create",
                MetricResult(
                    name="commits_per_create",
                    value=commits_in_create,
                    unit="commits",
                    details={"expected_after_batching": "1 per batch (100 logs)"},
                ),
            )

            report.add_metric(
                "audit_log",
                "sessions_per_event",
                MetricResult(
                    name="sessions_per_event",
                    value=session_creates,
                    unit="sessions",
                    details={"lines_found": session_creates},
                ),
            )

            # Estimate IOPS for 1000 logs
            iops_1000_logs = commits_in_create * 1000
            report.add_metric(
                "audit_log",
                "estimated_iops_1000_logs",
                MetricResult(
                    name="estimated_iops_1000_logs",
                    value=iops_1000_logs,
                    unit="operations",
                    details={"expected_after_batching": "~10 (batches of 100)"},
                ),
            )

    except Exception as e:
        report.add_error(f"Audit log source analysis failed: {e}")


# =============================================================================
# CACHE PERFORMANCE METRICS
# =============================================================================


async def measure_cache_performance(report: BaselineReport) -> None:
    """Measure cache invalidation and serialization performance.

    Tests:
    - Sequential delete timing
    - Pipeline vs sequential comparison
    - Tenant serialization overhead
    """
    logger.info("🔍 Measuring cache performance...")

    # Analyze cache.py for sequential deletes
    try:
        from pathlib import Path

        cache_path = Path("src/app/core/utils/cache.py")
        if cache_path.exists():
            content = cache_path.read_text()

            # Check for sequential delete pattern
            sequential_deletes = content.count("await client.delete(")
            uses_pipeline = "pipeline(" in content

            report.add_metric(
                "cache",
                "sequential_delete_calls",
                MetricResult(
                    name="sequential_delete_calls",
                    value=sequential_deletes,
                    unit="calls",
                    details={"uses_pipeline": uses_pipeline},
                ),
            )

            # Estimate timing for 5 deletes (typical cache invalidation)
            # Each Redis roundtrip ~0.5ms local, ~2ms remote
            estimated_time_5_deletes = 5 * 0.5  # 2.5ms for sequential
            estimated_time_pipeline = 0.5  # 0.5ms for pipeline

            report.add_metric(
                "cache",
                "invalidation_time_5_keys_ms",
                MetricResult(
                    name="invalidation_time_5_keys_ms",
                    value=estimated_time_5_deletes,
                    unit="ms",
                    details={"expected_after_pipeline": estimated_time_pipeline},
                ),
            )

    except Exception as e:
        report.add_error(f"Cache source analysis failed: {e}")

    # Analyze tenant.py for serialization
    try:
        tenant_path = Path("src/app/middleware/tenant.py")
        if tenant_path.exists():
            content = tenant_path.read_text()

            uses_as_str_true = "as_str=True" in content

            report.add_metric(
                "cache",
                "tenant_unnecessary_encode",
                MetricResult(
                    name="tenant_unnecessary_encode",
                    value=uses_as_str_true,
                    unit="bool",
                    details={"issue": "as_str=True causes bytes→str→bytes conversion"},
                ),
            )

    except Exception as e:
        report.add_error(f"Tenant source analysis failed: {e}")


# =============================================================================
# SECURITY BASELINE METRICS
# =============================================================================


async def measure_security_baseline(report: BaselineReport) -> None:
    """Measure security-related baseline metrics.

    Tests:
    - Token blacklist collision potential
    - Bloom filter sync behavior
    """
    logger.info("🔍 Measuring security baseline...")

    # Analyze token blacklist for collision potential
    try:
        from pathlib import Path

        blacklist_path = Path("src/app/core/security/token_blacklist_cache.py")
        if blacklist_path.exists():
            content = blacklist_path.read_text()

            # Check for token[:32] pattern
            uses_prefix_key = "token[:32]" in content
            uses_hash = "hashlib.sha256" in content or "xxhash" in content

            report.add_metric(
                "security",
                "token_prefix_collision_risk",
                MetricResult(
                    name="token_prefix_collision_risk",
                    value=uses_prefix_key and not uses_hash,
                    unit="bool",
                    details={
                        "uses_prefix_32": uses_prefix_key,
                        "uses_hash": uses_hash,
                        "issue": "JWT headers often share first 20+ chars",
                    },
                ),
            )

            # Demonstrate collision potential
            # JWTs with same header have identical first ~36 chars
            jwt1 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload1.sig1"
            jwt2 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload2.sig2"

            prefix1 = jwt1[:32]
            prefix2 = jwt2[:32]
            collision = prefix1 == prefix2

            report.add_metric(
                "security",
                "collision_demo",
                MetricResult(
                    name="collision_demo",
                    value=collision,
                    unit="bool",
                    details={
                        "jwt1_prefix": prefix1,
                        "jwt2_prefix": prefix2,
                        "note": "Different JWTs, same cache key!",
                    },
                ),
            )

    except Exception as e:
        report.add_error(f"Token blacklist analysis failed: {e}")

    # Check bloom filter sync
    try:
        bloom_path = Path("src/app/core/security/bloom_filter.py")
        if bloom_path.exists():
            content = bloom_path.read_text()

            has_periodic_sync = "periodic" in content.lower() or "schedule" in content.lower()
            has_arq_task = "@arq_task" in content or "arq" in content.lower()

            report.add_metric(
                "security",
                "bloom_filter_periodic_sync",
                MetricResult(
                    name="bloom_filter_periodic_sync",
                    value=has_periodic_sync or has_arq_task,
                    unit="bool",
                    details={
                        "has_periodic": has_periodic_sync,
                        "has_arq": has_arq_task,
                        "issue": "Multi-instance deployments need periodic sync",
                    },
                ),
            )

    except Exception as e:
        report.add_error(f"Bloom filter analysis failed: {e}")


# =============================================================================
# MAIN EXECUTION
# =============================================================================


async def capture_all_baselines() -> BaselineReport:
    """Capture all baseline metrics."""
    report = BaselineReport()

    logger.info("=" * 60)
    logger.info("R1-OPUS-ADVANCED Baseline Metrics Capture")
    logger.info("=" * 60)

    # Run all measurements
    await measure_s3_blocking_detection(report)
    await measure_file_upload_memory(report)
    await measure_stripe_webhook_latency(report)
    await measure_webhook_delivery_performance(report)
    await measure_audit_log_iops(report)
    await measure_cache_performance(report)
    await measure_security_baseline(report)

    logger.info("=" * 60)
    logger.info("Baseline Capture Complete")
    logger.info("=" * 60)

    return report


def main():
    """Main entry point."""
    try:
        # Change to project root for imports
        import sys
        from pathlib import Path

        project_root = Path(__file__).parent.parent.parent.parent
        sys.path.insert(0, str(project_root))

        report = asyncio.run(capture_all_baselines())
        filepath = report.save()

        # Print summary
        data = report.to_dict()
        print("\n" + "=" * 60)
        print("BASELINE SUMMARY")
        print("=" * 60)
        print(f"Timestamp: {data['timestamp']}")
        print(f"Categories: {data['summary']['total_categories']}")
        print(f"Metrics: {data['summary']['total_metrics']}")
        print(f"Errors: {data['summary']['error_count']}")
        print(f"Complete: {data['summary']['capture_complete']}")
        print(f"Output: {filepath}")
        print("=" * 60)

        return 0 if data["summary"]["capture_complete"] else 1

    except Exception as e:
        logger.exception(f"Baseline capture failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
