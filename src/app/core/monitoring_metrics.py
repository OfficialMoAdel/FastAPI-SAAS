"""R1-UNIFIED Performance Metrics (Legacy Import Path).

This module re-exports from src.app.core.monitoring for backward compatibility
with test files and other code that imports from this path.

New code should use:
    from src.app.core.monitoring import ...

Instead of:
    from src.app.core.monitoring_metrics import ...
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
    update_dlq_size,
)

__all__ = [
    "R1UnifiedMetrics",
    "get_r1_metrics",
    "reset_r1_metrics",
    "measure_latency",
    "record_circuit_breaker_transition",
    "record_event_idempotency",
    "record_fail_open",
    "record_lua_script_execution",
    "record_passkey_query",
    "record_rbac_cache_hit",
    "record_rbac_cache_miss",
    "record_rbac_query_latency",
    "record_session_eviction",
    "record_xfetch_latency",
    "update_dlq_size",
    "record_dlq_push",
]
