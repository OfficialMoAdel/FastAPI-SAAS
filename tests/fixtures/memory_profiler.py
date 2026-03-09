"""Memory profiling test utilities for measuring memory usage in tests.

Provides context managers and assertion helpers for measuring memory allocation
during test execution. Uses Python's tracemalloc module to track memory allocations.

Usage:
    from tests.fixtures.memory_profiler import measure_memory, assert_memory_under

    async def test_memory_efficient_operation():
        with measure_memory() as snapshot:
            await perform_operation()

        assert snapshot.peak_mb < 10.0  # Peak under 10MB
"""

from __future__ import annotations

import logging
import tracemalloc
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MemorySnapshot:
    """Snapshot of memory usage at a point in time."""

    current: int  # Current memory in bytes
    peak: int  # Peak memory in bytes
    timestamp: float | None = None
    top_stats: list[str] | None = None

    @property
    def current_mb(self) -> float:
        """Current memory in megabytes."""
        return self.current / 1024 / 1024

    @property
    def peak_mb(self) -> float:
        """Peak memory in megabytes."""
        return self.peak / 1024 / 1024

    def __repr__(self) -> str:
        return f"MemorySnapshot(current={self.current_mb:.2f}MB, peak={self.peak_mb:.2f}MB)"


@dataclass
class MemoryContext:
    """Context object yielded by measure_memory."""

    snapshot: MemorySnapshot | None = None


@contextmanager
def measure_memory(detailed: bool = False, limit: int = 10):
    """Context manager to measure memory usage during operation.

    Args:
        detailed: If True, captures top memory allocations (slower)
        limit: Number of top allocations to capture if detailed=True

    Yields a MemoryContext object which will contain the snapshot after exit.

    Example:
        with measure_memory() as ctx:
            await heavy_operation()
        if ctx.snapshot:
            logger.info(f"Peak memory: {ctx.snapshot.peak_mb:.2f}MB")
    """
    tracemalloc.start()
    ctx = MemoryContext()
    try:
        yield ctx
    finally:
        current, peak = tracemalloc.get_traced_memory()

        top_stats = None
        if detailed:
            snapshot = tracemalloc.take_snapshot()
            top_stats = [str(stat) for stat in snapshot.statistics("lineno")[:limit]]

        tracemalloc.stop()
        ctx.snapshot = MemorySnapshot(current=current, peak=peak, top_stats=top_stats)


@contextmanager
def capture_memory():
    """Context manager that yields a callable to get current memory snapshot.

    Allows capturing memory at multiple points within a test.

    Example:
        with capture_memory() as get_snapshot:
            snapshot1 = get_snapshot()
            await operation()
            snapshot2 = get_snapshot()
        logger.info(f"Memory delta: {snapshot2.current_mb - snapshot1.current_mb:.2f}MB")
    """
    tracemalloc.start()
    try:
        yield lambda: MemorySnapshot(*tracemalloc.get_traced_memory())
    finally:
        tracemalloc.stop()


def assert_memory_under(max_mb: float, operation: Callable[[], Any], message: str | None = None) -> MemorySnapshot:
    """Assert that an operation uses less than max_mb of peak memory.

    Args:
        max_mb: Maximum allowed peak memory in megabytes
        operation: Callable to execute and measure
        message: Optional custom assertion message

    Returns:
        MemorySnapshot with actual memory usage

    Raises:
        AssertionError: If peak memory exceeds max_mb

    Example:
        def my_operation():
            return [i for i in range(1000000)]

        snapshot = assert_memory_under(10.0, my_operation)
        logger.info(f"Actual peak: {snapshot.peak_mb:.2f}MB")
    """
    tracemalloc.start()
    try:
        operation()
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        snapshot = MemorySnapshot(current=current, peak=peak)
        peak_mb = peak / 1024 / 1024

        assert peak_mb < max_mb, message or f"Memory usage {peak_mb:.2f}MB exceeds limit {max_mb}MB"
        return snapshot
    except Exception:
        tracemalloc.stop()
        raise


def assert_memory_reduces(
    before_operation: Callable[[], Any],
    after_operation: Callable[[], Any],
    expected_reduction_pct: float = 10.0,
    message: str | None = None,
) -> tuple[MemorySnapshot, MemorySnapshot]:
    """Assert that after_operation uses less memory than before_operation.

    Useful for validating memory optimizations.

    Args:
        before_operation: Operation representing the unoptimized version
        after_operation: Operation representing the optimized version
        expected_reduction_pct: Expected percentage reduction (default 10%)
        message: Optional custom assertion message

    Returns:
        Tuple of (before_snapshot, after_snapshot)

    Raises:
        AssertionError: If after_operation doesn't use less memory

    Example:
        def old_way():
            return {i: i for i in range(100000)}

        def new_way():
            return dict.fromkeys(range(100000))

        before, after = assert_memory_reduces(old_way, new_way, 5.0)
    """
    tracemalloc.start()
    try:
        before_operation()
        before_current, before_peak = tracemalloc.get_traced_memory()

        tracemalloc.reset_peak()

        after_operation()
        after_current, after_peak = tracemalloc.get_traced_memory()

        tracemalloc.stop()

        before_snapshot = MemorySnapshot(current=before_current, peak=before_peak)
        after_snapshot = MemorySnapshot(current=after_current, peak=after_peak)

        reduction_pct = ((before_peak - after_peak) / before_peak) * 100

        assert reduction_pct >= expected_reduction_pct, (
            message or f"Memory reduction {reduction_pct:.2f}% below expected {expected_reduction_pct}%"
        )

        return before_snapshot, after_snapshot
    except Exception:
        tracemalloc.stop()
        raise


def log_memory_usage(operation_name: str, snapshot: MemorySnapshot) -> None:
    """Log memory usage metrics for an operation.

    Args:
        operation_name: Name of the operation being logged
        snapshot: MemorySnapshot with metrics

    Example:
        with measure_memory() as snapshot:
            await my_operation()
        log_memory_usage("my_operation", snapshot)
    """
    logger.info(f"[MEMORY] {operation_name}: current={snapshot.current_mb:.2f}MB, peak={snapshot.peak_mb:.2f}MB")


class MemoryProfiler:
    """Helper class for profiling memory usage across multiple operations.

    Useful for comparing memory usage patterns between different approaches.

    Example:
        profiler = MemoryProfiler()

        def approach_a():
            return [i for i in range(10000)]

        def approach_b():
            return list(range(10000))

        snapshot_a = profiler.profile("approach_a", approach_a)
        snapshot_b = profiler.profile("approach_b", approach_b)

        profiler.compare("approach_a", "approach_b")
    """

    def __init__(self):
        self.snapshots: dict[str, MemorySnapshot] = {}

    def profile(self, name: str, operation: Callable[[], Any]) -> MemorySnapshot:
        """Profile an operation and store the snapshot.

        Args:
            name: Name to identify this operation
            operation: Callable to execute and measure

        Returns:
            MemorySnapshot with metrics
        """
        tracemalloc.start()
        try:
            operation()
            current, peak = tracemalloc.get_traced_memory()
            snapshot = MemorySnapshot(current=current, peak=peak)
            self.snapshots[name] = snapshot
            log_memory_usage(name, snapshot)
            return snapshot
        finally:
            tracemalloc.stop()

    def compare(self, name_a: str, name_b: str) -> dict[str, Any]:
        """Compare memory usage between two profiled operations.

        Args:
            name_a: Name of first operation
            name_b: Name of second operation

        Returns:
            Dict with comparison metrics

        Raises:
            KeyError: If either operation hasn't been profiled
        """
        if name_a not in self.snapshots:
            raise KeyError(f"Operation '{name_a}' not profiled")
        if name_b not in self.snapshots:
            raise KeyError(f"Operation '{name_b}' not profiled")

        snap_a = self.snapshots[name_a]
        snap_b = self.snapshots[name_b]

        current_diff = snap_b.current_mb - snap_a.current_mb
        peak_diff = snap_b.peak_mb - snap_a.peak_mb
        peak_pct = ((snap_a.peak - snap_b.peak) / snap_a.peak) * 100

        comparison = {
            "name_a": name_a,
            "name_b": name_b,
            "current_mb_a": snap_a.current_mb,
            "current_mb_b": snap_b.current_mb,
            "current_diff_mb": current_diff,
            "peak_mb_a": snap_a.peak_mb,
            "peak_mb_b": snap_b.peak_mb,
            "peak_diff_mb": peak_diff,
            "peak_reduction_pct": peak_pct,
        }

        logger.info(
            f"[MEMORY COMPARE] {name_a} vs {name_b}: "
            f"peak {snap_a.peak_mb:.2f}MB → {snap_b.peak_mb:.2f}MB "
            f"({peak_pct:+.2f}%)"
        )

        return comparison

    def get_report(self) -> dict[str, dict[str, float]]:
        """Get a report of all profiled operations.

        Returns:
            Dict mapping operation names to memory metrics
        """
        return {name: {"current_mb": snap.current_mb, "peak_mb": snap.peak_mb} for name, snap in self.snapshots.items()}
