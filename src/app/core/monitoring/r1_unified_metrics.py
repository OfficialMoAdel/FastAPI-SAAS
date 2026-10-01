"""
R1-UNIFIED Performance Metrics.

In-memory, Prometheus-compatible metrics for tracking R1-UNIFIED optimization performance.
Provides counters, histograms, and gauges for all 7 optimization areas.

Usage:
    from src.app.core.monitoring import record_event_idempotency, record_xfetch_latency

    # Record metrics
    record_event_idempotency(is_duplicate=False)
    record_xfetch_latency(0.005)  # 5ms
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Any

from src.app.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class HistogramBuckets:
    """Histogram bucket configuration."""

    boundaries: tuple[float, ...] = (
        0.001,  # 1ms
        0.005,  # 5ms
        0.01,  # 10ms
        0.025,  # 25ms
        0.05,  # 50ms
        0.1,  # 100ms
        0.25,  # 250ms
        0.5,  # 500ms
        1.0,  # 1s
    )


class R1UnifiedMetrics:
    """Prometheus-compatible metrics for R1-UNIFIED optimizations.

    This class provides in-memory metric storage that can be exported
    to Prometheus or other monitoring systems. It tracks:

    1. Event Bus Idempotency - duplicate detection rate
    2. Session Eviction - roundtrip counts and latency
    3. XFetch Cache - cache hit/miss and latency
    4. RBAC Permissions - cache effectiveness
    5. DLQ Size - queue depth monitoring
    6. Passkey Query - query performance
    7. Circuit Breaker - state transitions
    """

    def __init__(self) -> None:
        """Initialize metrics storage."""
        # Counters
        self._counters: dict[str, float] = {
            "r1_event_idempotency_checks_total": 0,
            "r1_event_idempotency_duplicates_total": 0,
            "r1_session_evictions_total": 0,
            "r1_xfetch_cache_hits_total": 0,
            "r1_xfetch_cache_misses_total": 0,
            "r1_rbac_cache_hits_total": 0,
            "r1_rbac_cache_misses_total": 0,
            "r1_dlq_pushes_total": 0,
            "r1_dlq_trims_total": 0,
            "r1_passkey_queries_total": 0,
            "r1_circuit_breaker_opens_total": 0,
            "r1_circuit_breaker_closes_total": 0,
            "r1_lua_script_executions_total": 0,
            "r1_lua_script_fallbacks_total": 0,
            "r1_security_fail_open_total": 0,
            "r1_tenant_l1_publish_failed_total": 0,
        }

        # Gauges
        self._gauges: dict[str, float] = {
            "r1_dlq_size": 0,
            "r1_dlq_size_pct": 0,
            "r1_circuit_breaker_state": 0,  # 0=closed, 1=open, 2=half-open
            "r1_session_active_count": 0,
        }

        # Histogram data (sum, count, buckets). F2: each histogram also
        # carries its precomputed ascending ``boundaries`` tuple so
        # observe_histogram matches the cumulative prefix with one
        # O(log B) bisect instead of an O(B log B) sort per observation.
        _latency_boundaries = tuple(sorted(HistogramBuckets().boundaries))
        self._histograms: dict[str, dict[str, Any]] = {
            "r1_session_eviction_roundtrips": {
                "sum": 0.0,
                "count": 0,
                "buckets": {1: 0, 2: 0, 3: 0, 5: 0, 10: 0},
                "boundaries": (1, 2, 3, 5, 10),
            },
            "r1_xfetch_latency_seconds": {
                "sum": 0.0,
                "count": 0,
                "buckets": dict.fromkeys(HistogramBuckets().boundaries, 0),
                "boundaries": _latency_boundaries,
            },
            "r1_rbac_query_latency_seconds": {
                "sum": 0.0,
                "count": 0,
                "buckets": dict.fromkeys(HistogramBuckets().boundaries, 0),
                "boundaries": _latency_boundaries,
            },
            "r1_passkey_query_latency_seconds": {
                "sum": 0.0,
                "count": 0,
                "buckets": dict.fromkeys(HistogramBuckets().boundaries, 0),
                "boundaries": _latency_boundaries,
            },
        }

    # Counter operations
    def inc_counter(self, name: str, value: float = 1.0) -> None:
        """Increment a counter."""
        if name in self._counters:
            self._counters[name] += value
        else:
            logger.warning("unknown_counter", name=name)

    def get_counter(self, name: str) -> float:
        """Get counter value."""
        return self._counters.get(name, 0.0)

    # Gauge operations
    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge value."""
        if name in self._gauges:
            self._gauges[name] = value
        else:
            logger.warning("unknown_gauge", name=name)

    def get_gauge(self, name: str) -> float:
        """Get gauge value."""
        return self._gauges.get(name, 0.0)

    # Histogram operations
    def observe_histogram(self, name: str, value: float) -> None:
        """Record an observation in a histogram.

        Buckets are cumulative (Prometheus ``le`` semantics): an
        observation increments every boundary ``>= value``. F2: the
        ascending boundary tuple is precomputed at registration, so the
        matching suffix size is one bisect (O(log B)) plus O(k)
        increments instead of re-sorting the keys on every call.

        Behavior is byte-identical to the previous sort-per-observation
        implementation across the full float domain — pinned by the
        differential test in
        ``tests/unit/core/test_r1_metrics_histogram_isomorphism.py``:
        - ``bisect_left`` counts boundaries strictly below ``value``, so
          ``len - bisect_left`` is exactly the legacy
          ``value <= boundary`` match count: values at or below the
          smallest boundary (zero, negatives) fire ALL buckets; values
          at a boundary fire it inclusively; +inf fires none.
        - NaN satisfies no ``value <= boundary`` comparison → no bucket
          fires (bisect on NaN is undefined, hence the explicit guard).
        """
        hist = self._histograms.get(name)
        if hist is None:
            logger.warning("unknown_histogram", name=name)
            return

        boundaries: tuple[float, ...] = hist["boundaries"]
        if value != value:  # NaN — no bucket fires
            match_count = 0
        else:
            # count of boundaries >= value (legacy: value <= boundary)
            match_count = len(boundaries) - bisect_left(boundaries, value)

        hist["sum"] += value
        hist["count"] += 1
        buckets = hist["buckets"]
        for boundary in boundaries[len(boundaries) - match_count :]:
            buckets[boundary] += 1

    def get_histogram(self, name: str) -> dict[str, Any]:
        """Get histogram data."""
        return self._histograms.get(name, {})

    # Export for Prometheus
    def export_prometheus(self) -> str:
        """Export all metrics in Prometheus text format."""
        lines = []

        # Counters
        for name, value in self._counters.items():
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        # Gauges
        for name, value in self._gauges.items():
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        # Histograms
        for name, data in self._histograms.items():
            lines.append(f"# TYPE {name} histogram")
            for boundary, count in sorted(data["buckets"].items()):
                lines.append(f'{name}_bucket{{le="{boundary}"}} {count}')
            lines.append(f'{name}_bucket{{le="+Inf"}} {data["count"]}')
            lines.append(f"{name}_sum {data['sum']}")
            lines.append(f"{name}_count {data['count']}")

        return "\n".join(lines)

    def get_all_metrics(self) -> dict[str, Any]:
        """Get all metrics as a dictionary."""
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {
                name: {
                    "sum": data["sum"],
                    "count": data["count"],
                    "mean": data["sum"] / data["count"] if data["count"] > 0 else 0,
                }
                for name, data in self._histograms.items()
            },
        }

    def reset(self) -> None:
        """Reset all metrics (for testing)."""
        for name in self._counters:
            self._counters[name] = 0
        for name in self._gauges:
            self._gauges[name] = 0
        for name in self._histograms:
            self._histograms[name]["sum"] = 0
            self._histograms[name]["count"] = 0
            for boundary in self._histograms[name]["buckets"]:
                self._histograms[name]["buckets"][boundary] = 0


# Singleton instance
_metrics: R1UnifiedMetrics | None = None


def get_r1_metrics() -> R1UnifiedMetrics:
    """Get the singleton metrics instance."""
    global _metrics
    if _metrics is None:
        _metrics = R1UnifiedMetrics()
    return _metrics


def reset_r1_metrics() -> None:
    """Reset the singleton metrics (for testing)."""
    global _metrics
    if _metrics is not None:
        _metrics.reset()


# =============================================================================
# Convenience functions for recording metrics
# =============================================================================


def record_event_idempotency(is_duplicate: bool) -> None:
    """Record an event idempotency check.

    Args:
        is_duplicate: Whether the event was a duplicate
    """
    metrics = get_r1_metrics()
    metrics.inc_counter("r1_event_idempotency_checks_total")
    if is_duplicate:
        metrics.inc_counter("r1_event_idempotency_duplicates_total")


def record_session_eviction(roundtrips: int) -> None:
    """Record a session eviction operation.

    Args:
        roundtrips: Number of Redis roundtrips used
    """
    metrics = get_r1_metrics()
    metrics.inc_counter("r1_session_evictions_total")
    metrics.observe_histogram("r1_session_eviction_roundtrips", float(roundtrips))


def record_xfetch_latency(latency_seconds: float, is_hit: bool = True) -> None:
    """Record an XFetch cache operation.

    Args:
        latency_seconds: Operation latency in seconds
        is_hit: Whether it was a cache hit
    """
    metrics = get_r1_metrics()
    if is_hit:
        metrics.inc_counter("r1_xfetch_cache_hits_total")
    else:
        metrics.inc_counter("r1_xfetch_cache_misses_total")
    metrics.observe_histogram("r1_xfetch_latency_seconds", latency_seconds)


def record_rbac_cache_hit() -> None:
    """Record an RBAC cache hit."""
    get_r1_metrics().inc_counter("r1_rbac_cache_hits_total")


def record_rbac_cache_miss() -> None:
    """Record an RBAC cache miss."""
    get_r1_metrics().inc_counter("r1_rbac_cache_misses_total")



def update_dlq_size(size: int, max_size: int = 10000) -> None:
    """Update DLQ size gauge.

    Args:
        size: Current DLQ size
        max_size: Maximum DLQ size for percentage calculation
    """
    metrics = get_r1_metrics()
    metrics.set_gauge("r1_dlq_size", float(size))
    metrics.set_gauge("r1_dlq_size_pct", (size / max_size) * 100 if max_size > 0 else 0)



def record_passkey_query(latency_seconds: float) -> None:
    """Record a passkey credential query.

    Args:
        latency_seconds: Query latency in seconds
    """
    metrics = get_r1_metrics()
    metrics.inc_counter("r1_passkey_queries_total")
    metrics.observe_histogram("r1_passkey_query_latency_seconds", latency_seconds)


def update_circuit_breaker_state(state: str) -> None:
    """Update circuit breaker state gauge.

    Args:
        state: One of "closed", "open", "half_open"
    """
    state_map = {"closed": 0, "open": 1, "half_open": 2}
    value = state_map.get(state.lower(), 0)
    get_r1_metrics().set_gauge("r1_circuit_breaker_state", float(value))


def record_circuit_breaker_transition(to_state: str) -> None:
    """Record a circuit breaker state transition.

    Args:
        to_state: Target state ("open" or "closed")
    """
    metrics = get_r1_metrics()
    if to_state == "open":
        metrics.inc_counter("r1_circuit_breaker_opens_total")
    elif to_state == "closed":
        metrics.inc_counter("r1_circuit_breaker_closes_total")
    update_circuit_breaker_state(to_state)



def record_tenant_l1_publish_failed() -> None:
    """Record a failed cross-worker tenant L1 invalidation publish.

    The L1 cache stays stale up to its 60s TTL when this fires; the counter
    makes the failure visible during incidents instead of a bare log line.
    """
    get_r1_metrics().inc_counter("r1_tenant_l1_publish_failed_total")


def record_fail_open(operation: str) -> None:
    """Record a fail-open security event.

    Args:
        operation: The operation that failed open (e.g., "session_validation")
    """
    metrics = get_r1_metrics()
    metrics.inc_counter("r1_security_fail_open_total")
    # Operation detail is carried by structured logs; the counter is aggregate.


