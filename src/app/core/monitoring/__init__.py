"""Monitoring: application metrics and the Phase-0 telemetry instruments (T-1..T-8).

Two distinct jobs live here and the split matters.

``r1_unified_metrics`` is the APPLICATION's metrics: Prometheus-shaped counters,
gauges and histograms that a running deployment scrapes. They describe the
application continuously and are always on.

The ``telemetry`` package is the Phase-0 MEASUREMENT instruments. Each is
default-off, each is additive, each declares an absolute cost ceiling, and each
exports a JSON-serialisable snapshot into a performance artefact. They are not
scraped and they are not always on, because their consumer is a benchmark run and
not an operator.

The two are kept apart deliberately. Reusing the Prometheus registry for a time
series would give back cumulative counters, and a cumulative counter cannot
express "the event loop was blocked for 740 ms at t=12.3 s" - which is the
question the loop-lag instrument exists to answer.
"""

from __future__ import annotations

from src.app.core.monitoring.app_config_manifest import (
    MANIFEST_SCHEMA,
    REQUIRED_FIELDS,
    ManifestIncomplete,
    assert_manifest_complete,
    assert_no_credentials,
    assert_runtime_identity,
    build_app_config_manifest,
    collect_app_config,
    write_app_config_manifest,
)
from src.app.core.monitoring.gc_pause import (
    GCPauseObserver,
    assert_observer_cost,
    assert_series_are_disjoint,
    measure_observer_cost,
)
from src.app.core.monitoring.loop_lag import (
    MINIMUM_RATE_HZ,
    SAMPLE_INTERVAL_SECONDS,
    LoopLagSampler,
    assert_sampler_cost,
    assert_validated_on_production_loop,
    measure_sampler_cost,
)
from src.app.core.monitoring.process_series import (
    MINIMUM_WINDOW_SECONDS,
    ProcessSeriesSampler,
    assert_host_recorded,
    assert_tracemalloc_is_attribution_only,
    assert_window_long_enough,
)
from src.app.core.monitoring.process_series import (
    measure_sampler_cost as measure_process_sampler_cost,
)
from src.app.core.monitoring.r1_unified_metrics import (
    R1UnifiedMetrics,
    get_r1_metrics,
    record_circuit_breaker_transition,
    record_event_idempotency,
    record_fail_open,
    record_passkey_query,
    record_rbac_cache_hit,
    record_rbac_cache_miss,
    record_session_eviction,
    record_tenant_l1_publish_failed,
    record_xfetch_latency,
    reset_r1_metrics,
    update_circuit_breaker_state,
    update_dlq_size,
)
from src.app.core.monitoring.redis_commands import (
    RedisCommandCounts,
    RedisCommandShim,
    assert_counts_agree,
    assert_pipeline_aware,
    commandstats_total,
    measure_shim_cost,
)
from src.app.core.monitoring.request_timer import (
    RESPONSE_TIME_HEADER,
    RequestTimer,
    assert_excluded_from_own_timing,
    assert_series_not_merged,
)
from src.app.core.monitoring.request_timer import (
    measure_disabled_cost as measure_timer_disabled_cost,
)
from src.app.core.monitoring.request_timer import (
    measure_enabled_cost as measure_timer_enabled_cost,
)
from src.app.core.monitoring.telemetry import (
    Ceiling,
    CeilingExceeded,
    Counter,
    Instrument,
    SeriesStore,
    TelemetryRegistry,
    assert_cost_under_ceiling,
    measure_call_cost,
    telemetry_enabled_flags,
)
from src.app.core.monitoring.void_conditions import (
    SecurityEventCounter,
    StatusDistribution,
    assert_counter_cost,
    assert_phase_admissible,
    assert_run_saw_zero_security_events,
    phase_is_void,
)
from src.app.core.monitoring.void_conditions import (
    measure_disabled_cost as measure_counters_disabled_cost,
)
from src.app.core.monitoring.void_conditions import (
    measure_enabled_cost as measure_counters_enabled_cost,
)

__all__ = [
    # Application metrics (always on, scraped)
    "R1UnifiedMetrics",
    "get_r1_metrics",
    "record_circuit_breaker_transition",
    "record_event_idempotency",
    "record_fail_open",
    "record_passkey_query",
    "record_rbac_cache_hit",
    "record_rbac_cache_miss",
    "record_session_eviction",
    "record_tenant_l1_publish_failed",
    "record_xfetch_latency",
    "reset_r1_metrics",
    "update_circuit_breaker_state",
    "update_dlq_size",
    # Shared telemetry substrate
    "Ceiling",
    "CeilingExceeded",
    "Counter",
    "Instrument",
    "SeriesStore",
    "TelemetryRegistry",
    "assert_cost_under_ceiling",
    "measure_call_cost",
    "telemetry_enabled_flags",
    # T-1 loop lag
    "MINIMUM_RATE_HZ",
    "SAMPLE_INTERVAL_SECONDS",
    "LoopLagSampler",
    "assert_sampler_cost",
    "assert_validated_on_production_loop",
    "measure_sampler_cost",
    # T-2 GC pause
    "GCPauseObserver",
    "assert_observer_cost",
    "assert_series_are_disjoint",
    "measure_observer_cost",
    # T-3 request timer
    "RESPONSE_TIME_HEADER",
    "RequestTimer",
    "assert_excluded_from_own_timing",
    "assert_series_not_merged",
    "measure_timer_disabled_cost",
    "measure_timer_enabled_cost",
    # T-4 / T-5 void conditions
    "SecurityEventCounter",
    "StatusDistribution",
    "assert_counter_cost",
    "assert_phase_admissible",
    "assert_run_saw_zero_security_events",
    "measure_counters_disabled_cost",
    "measure_counters_enabled_cost",
    "phase_is_void",
    # T-6 redis commands
    "RedisCommandCounts",
    "RedisCommandShim",
    "assert_counts_agree",
    "assert_pipeline_aware",
    "commandstats_total",
    "measure_shim_cost",
    # T-7 process series
    "MINIMUM_WINDOW_SECONDS",
    "ProcessSeriesSampler",
    "assert_host_recorded",
    "assert_tracemalloc_is_attribution_only",
    "assert_window_long_enough",
    "measure_process_sampler_cost",
    # T-8 app config manifest
    "MANIFEST_SCHEMA",
    "REQUIRED_FIELDS",
    "ManifestIncomplete",
    "assert_manifest_complete",
    "assert_no_credentials",
    "assert_runtime_identity",
    "build_app_config_manifest",
    "collect_app_config",
    "write_app_config_manifest",
]
