"""R2-03: settings split from core/config.py — performance domain."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EventSettings(BaseSettings):
    """Settings for event handling."""

    EVENT_HANDLER_TIMEOUT_SECONDS: float = 30.0
    # F12: EVENT_HANDLER_TIMEOUT_CRITICAL removed — it had exactly one repo
    # hit (this definition); every consumer used _SECONDS. If per-handler
    # timeouts are ever needed, re-add alongside a consumer (cf. B13 below).
    EVENT_BUS_CONCURRENCY: int = 50  # Max concurrent sync handlers
    EVENT_BUS_RETRY_DELAYS: list[int] = Field(default=[60, 300, 900])
    """M-9: event-bus retry backoff in seconds (was hardcoded [60, 300, 900]).

    Overridable via ``EVENT_BUS_RETRY_DELAYS='[30,120,600]'`` style JSON.
    """
    EVENT_BUS_REQUIRE_STRICT_ORDERING: bool = False
    """Gate per-event sequence numbers behind an explicit opt-in.

    When False (default, preserves current behavior) publish() skips the
    Redis INCR and injects sequence=0; set True to require strict ordering
    via get_next_sequence(). Declared here so the flat settings accessor
    resolves it instead of relying on getattr defaults at the call site.
    """


class R1UnifiedSettings(BaseSettings):
    """R1-UNIFIED performance optimization feature flags and configuration.

    These flags enable gradual rollout and easy rollback of optimizations.
    All flags default to True (optimized behavior) as of R2-HYBRID.
    Set individual flags to False via environment variables to disable.
    """

    model_config = SettingsConfigDict(env_prefix="R1_")

    # Feature flags - enable/disable specific optimizations
    # All flags default to True (optimizations enabled) as of R2-HYBRID
    # Set to False via environment variables to disable if issues arise
    EVENT_BUS_USE_LUA_IDEMPOTENCY: bool = True
    """Use Lua script for atomic idempotency check (P1-1)."""

    EVENT_BUS_USE_BATCH_ENQUEUE: bool = True
    """Use batched Redis pipeline for event handler enqueueing (P3-3)."""

    XFETCH_USE_LUA_SCRIPT: bool = False
    """Use Lua script for atomic GET+TTL in XFetch (P2-2). (Off by default due to performance)"""

    RBAC_USE_REDIS_CACHE: bool = True
    """Use Redis caching for RBAC permission checks (P5)."""

    TOKEN_NEGATIVE_CACHE_TTL: int = 60
    """TTL in seconds for token blacklist negative cache (LOW-2).
    Reduced to 60 seconds to prevent delay in invalidation.
    """

    RBAC_CACHE_TTL_SECONDS: int = 3600
    """TTL in seconds for RBAC permission cache. Default 1 hour (3600 seconds)."""

    # Circuit breaker settings
    CIRCUIT_BREAKER_USE_DOUBLE_CHECK: bool = True
    """Use double-check pattern for TOCTOU race prevention in circuit breaker (P4-1)."""

    # DLQ (Dead Letter Queue) settings
    DLQ_USE_BOUNDED_SIZE: bool = True
    """Use bounded size for DLQ with LTRIM (P3-1)."""

    DLQ_MAX_SIZE: int = 10000
    """Maximum size of the DLQ. Default 10000 entries."""

    DLQ_ALERT_THRESHOLD_PCT: float = 80.0
    """Alert when DLQ reaches this percentage of max size. Default 80%."""

    # Session management settings
    SESSION_MAX_EVICTION_BATCH_SIZE: int = 100
    """Maximum number of sessions to evict in a single batch."""

    # Passkey settings
    PASSKEY_DECRYPT_CONCURRENCY_LIMIT: int = 10
    """Maximum concurrent passkey decryption operations."""

    PASSKEY_USE_COLUMN_SELECT: bool = True
    """Use column-specific SELECT for credential_id only (P3-2)."""

    PASSKEY_MAX_CREDENTIALS_PER_USER: int = 10
    """Maximum number of passkey credentials per user."""


class PerformanceSettings(BaseSettings):
    """Performance optimization feature flags and configuration.

    These flags enable gradual rollout and easy rollback of optimizations.
    """

    model_config = SettingsConfigDict(env_prefix="PERFORMANCE_")

    FEATURE_CONTEXTVAR_OPTIMIZATION: bool = False
    """Use contextvar for tenant context (P0-2)."""

    # B13: FEATURE_STREAMING_UPLOADS removed — its only consumer (the
    # spooled-temporary-file fallback in s3_storage) was deleted in P-M8;
    # streaming is now unconditional. The flag was dead config still
    # exercised by test fixtures, which falsely suggested flag-off behavior
    # was covered.

    FEATURE_REMOVE_WASTED_CONNECTION: bool = True
    """Remove wasted DB connection in tenant middleware (P0-1)."""

    FEATURE_UPLOAD_TXN_SPLIT: bool = True
    """N1: commit the quota read-phase before the storage upload so the pooled
    connection is not held across network I/O; the metadata write runs in a
    fresh short transaction after the upload. Default-on (P2): the flag-on
    path is A/B-verified; pool (20+10) exhausts at ~30 concurrent uploads
    with the connection held across network I/O."""

    LOG_SAMPLING_RATE: float = Field(default=1.0, ge=0.0, le=1.0)
    """P10: keep-probability for INFO/DEBUG structlog events (1.0 = keep all).
    WARNING and above are never sampled. Dropped events skip sanitization and
    rendering entirely."""

    LOG_SAMPLING_RATES: dict[str, float] = Field(default_factory=dict)
    """S-05c: per-EVENT overrides for ``LOG_SAMPLING_RATE``.

    One global rate forces a bad trade-off: set it low to control volume from
    hot paths and you lose the rare-but-critical events; set it high to keep
    those and you drown in noise. An override here pins ONE event name
    (e.g. ``{"payment_failed": 1.0}``) and wins over the global rate, so a
    hot event can be sampled hard while a rare one is never dropped.

    Keys are structlog event names. Unknown names are harmless.
    """


class TelemetrySettings(BaseSettings):
    """Phase-0 telemetry (T-1..T-8) on/off switches.

    Every field DEFAULTS OFF, and that default is a design constraint rather than
    a conservative guess. Each instrument is only useful during a measurement run,
    while its cost is paid by every deployment that imports the module which
    imports it. An instrument on by default is an instrument whose overhead is
    charged to users who will never read its output.

    The enabled state is recorded in the manifest (T-8, acceptance criterion d), so
    an artefact can distinguish "this series was never enabled" from "this series
    recorded nothing" - which are different facts and look identical otherwise.
    """

    model_config = SettingsConfigDict(env_prefix="TELEMETRY_")

    LOOP_LAG_ENABLED: bool = False
    """T-1: event-loop lag sampler at >=100 Hz on the app host."""

    GC_PAUSE_ENABLED: bool = False
    """T-2: GC pause counter and pause-time histogram. A separate series from T-1."""

    REQUEST_TIMER_ENABLED: bool = False
    """T-3: server-side request timer emitting X-Response-Time."""

    STATUS_DISTRIBUTION_ENABLED: bool = False
    """T-4: per-phase status-code distribution; a non-2xx voids the phase."""

    SECURITY_EVENTS_ENABLED: bool = False
    """T-5: rate-limit and auto-ban event counter; zero required for the run."""

    REDIS_COMMANDS_ENABLED: bool = False
    """T-6: pipeline-aware Redis command counting, shim-installed not shipped."""

    PROCESS_SERIES_ENABLED: bool = False
    """T-7: CPU% and RSS series for the whole run, on the app host."""


class QueryProfilerSettings(BaseSettings):
    """Development mode SQL query profiler configuration.

    Only active in non-production environments. Helps identify:
    - Slow queries (> threshold ms)
    - N+1 query issues
    - Inefficient database access patterns
    """

    model_config = SettingsConfigDict(env_prefix="")

    QUERY_PROFILER_ENABLED: bool = Field(
        default=False,
        description="Opt-in query profiler middleware (dev/staging only; never production)",
    )
    """Enable query profiling. Default OFF (P2): profiling is a diagnostic
    tool, not a default-on surface — flip explicitly per environment.
    Auto-disabled in production regardless of this flag."""

    QUERY_PROFILER_SLOW_THRESHOLD_MS: int = Field(
        default=100,
        ge=10,
        le=5000,
        description="Threshold in ms for logging 'slow' queries",
    )
    """Queries taking longer than this are flagged as slow."""

    QUERY_PROFILER_N_PLUS_1_THRESHOLD: int = Field(
        default=3,
        ge=2,
        le=100,
        description="Warn if same query executed more than N times",
    )
    """Detect potential N+1 issues when query repeats."""

    QUERY_PROFILER_DETAILED_LOGS: bool = Field(
        default=False,
        description="Log detailed query analysis including repeated query samples",
    )
    """Enable verbose logging of N+1 detection details."""
