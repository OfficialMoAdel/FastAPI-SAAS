"""R2-03: settings split from core/config.py — performance domain."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EventSettings(BaseSettings):
    """Settings for event handling."""

    EVENT_HANDLER_TIMEOUT_SECONDS: float = 30.0
    EVENT_HANDLER_TIMEOUT_CRITICAL: float = 60.0  # For known slow handlers
    EVENT_BUS_CONCURRENCY: int = 50  # Max concurrent sync handlers
    EVENT_BUS_RETRY_DELAYS: list[int] = Field(default=[60, 300, 900])
    """M-9: event-bus retry backoff in seconds (was hardcoded [60, 300, 900]).

    Overridable via ``EVENT_BUS_RETRY_DELAYS='[30,120,600]'`` style JSON.
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

    SESSION_USE_PIPELINE_EVICTION: bool = True
    """Use Redis pipeline for session eviction (P2-1)."""

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

    FEATURE_UPLOAD_TXN_SPLIT: bool = False
    """N1: commit the quota read-phase before the storage upload so the pooled
    connection is not held across network I/O; the metadata write runs in a
    fresh short transaction after the upload."""

    LOG_SAMPLING_RATE: float = Field(default=1.0, ge=0.0, le=1.0)
    """P10: keep-probability for INFO/DEBUG structlog events (1.0 = keep all).
    WARNING and above are never sampled. Dropped events skip sanitization and
    rendering entirely."""


class QueryProfilerSettings(BaseSettings):
    """Development mode SQL query profiler configuration.

    Only active in non-production environments. Helps identify:
    - Slow queries (> threshold ms)
    - N+1 query issues
    - Inefficient database access patterns
    """

    model_config = SettingsConfigDict(env_prefix="")

    QUERY_PROFILER_ENABLED: bool = Field(
        default=True,
        description="Enable query profiler middleware in dev/staging",
    )
    """Enable query profiling (auto-disabled in production)."""

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
