"""R1-UNIFIED Metrics Module.

Provides Prometheus metrics and helper functions for tracking
performance of R1-UNIFIED optimizations.
"""

from __future__ import annotations

from src.app.core.monitoring.r1_unified_metrics import (
    R1UnifiedMetrics,
    get_r1_metrics,
    measure_latency,
    record_circuit_breaker_transition,
    record_dlq_push,
    record_event_idempotency,
    record_fail_open,
    record_lua_script_execution,
    record_passkey_query,
    record_rbac_cache_hit,
    record_rbac_cache_miss,
    record_rbac_query_latency,
    record_session_eviction,
    record_xfetch_latency,
    reset_r1_metrics,
    update_circuit_breaker_state,
    update_dlq_size,
)

__all__ = [
    "R1UnifiedMetrics",
    "get_r1_metrics",
    "measure_latency",
    "record_circuit_breaker_transition",
    "record_dlq_push",
    "record_event_idempotency",
    "record_fail_open",
    "record_lua_script_execution",
    "record_passkey_query",
    "record_rbac_cache_hit",
    "record_rbac_cache_miss",
    "record_rbac_query_latency",
    "record_session_eviction",
    "record_xfetch_latency",
    "reset_r1_metrics",
    "update_circuit_breaker_state",
    "update_dlq_size",
]
