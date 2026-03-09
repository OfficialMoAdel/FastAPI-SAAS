"""R1-UNIFIED Metrics Module.

Provides Prometheus metrics and helper functions for tracking
performance of R1-UNIFIED optimizations.
"""

from __future__ import annotations

from src.app.core.monitoring.r1_unified_metrics import (
    R1UnifiedMetrics,
    get_r1_metrics,
    record_dlq_push,
    record_event_idempotency,
    record_rbac_cache_hit,
    record_rbac_cache_miss,
    record_session_eviction,
    record_xfetch_latency,
    update_circuit_breaker_state,
    update_dlq_size,
)

__all__ = [
    "R1UnifiedMetrics",
    "get_r1_metrics",
    "record_event_idempotency",
    "record_session_eviction",
    "record_xfetch_latency",
    "record_rbac_cache_hit",
    "record_rbac_cache_miss",
    "update_dlq_size",
    "record_dlq_push",
    "update_circuit_breaker_state",
]
