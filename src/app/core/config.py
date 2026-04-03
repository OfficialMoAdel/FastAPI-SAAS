import os
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, SecretStr, ValidationInfo, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    APP_NAME: str = "FastAPI app"
    APP_DESCRIPTION: str | None = None
    APP_VERSION: str | None = None
    LICENSE_NAME: str | None = None
    CONTACT_NAME: str | None = None
    CONTACT_EMAIL: str | None = None
    FRONTEND_URL: str = "http://localhost:3000"  # Frontend URL for magic links, etc.
    # DATABASE: Control table creation (set to False in production)
    CREATE_TABLES_ON_STARTUP: bool = True

    # ISSUE 18: Configurable max body size (bytes) - default 10MB
    MAX_BODY_SIZE: int = Field(default=10 * 1024 * 1024, ge=1024 * 1024, le=100 * 1024 * 1024)

    # ISSUE 6: System-reserved usernames to prevent phishing/confusion
    RESERVED_USERNAMES: frozenset[str] = frozenset(
        {
            "admin",
            "administrator",
            "root",
            "api",
            "www",
            "mail",
            "email",
            "support",
            "help",
            "info",
            "contact",
            "sales",
            "billing",
            "security",
            "secure",
            "auth",
            "login",
            "logout",
            "signup",
            "register",
            "user",
            "users",
            "account",
            "accounts",
            "system",
            "service",
            "services",
            "host",
            "master",
            "webmaster",
            "postmaster",
            "hostmaster",
            "abuse",
            "noc",
            "team",
            "staff",
            "null",
            "undefined",
            "test",
            "testing",
            "demo",
            "example",
        }
    )

    # L2: Centralized pagination defaults for API consistency
    PAGINATION_DEFAULT_PER_PAGE: int = Field(default=20, ge=1, le=100, description="Default items per page")
    PAGINATION_MAX_PER_PAGE: int = Field(default=100, ge=1, le=1000, description="Maximum items per page")


class CryptSettings(BaseSettings):
    SECRET_KEY: SecretStr
    ALGORITHM: str = "HS256"  # Options: HS256 (symmetric), RS256 (asymmetric)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # SECURITY FIX: Maximum absolute session duration to prevent indefinite refresh
    MAX_SESSION_DURATION_DAYS: int = Field(
        default=30, ge=1, le=365, description="Maximum absolute session duration before forcing re-auth"
    )
    # Bcrypt work factor: 8=30ms(dev), 10=100ms, 12=250ms(default), 14=1000ms
    BCRYPT_ROUNDS: int = Field(default=12, ge=8, le=14)
    # L3: Configurable bcrypt thread pool size for password hashing
    BCRYPT_THREAD_POOL_SIZE: int = Field(default=4, ge=1, le=32, description="Thread pool size for bcrypt operations")
    # Key for Fernet encryption. If None, webhooks encryption might fail or use a dev key.
    ENCRYPTION_KEY: SecretStr | None = None
    # SECURITY: Set unique salt per deployment for key derivation
    ENCRYPTION_SALT: SecretStr | None = None
    # RS256 asymmetric keys (optional, required if ALGORITHM=RS256)
    JWT_PRIVATE_KEY: SecretStr | None = None  # PEM-encoded private key
    JWT_PUBLIC_KEY: str | None = None  # PEM-encoded public key
    # SECURITY FIX: Made JWT audience configurable instead of hardcoded
    JWT_AUDIENCE: str = Field(default="fastapi-saas-api", description="JWT token audience claim")

    @model_validator(mode="after")
    def validate_secrets(self) -> "CryptSettings":
        """Validate required secrets are set and meet security requirements."""
        key = self.SECRET_KEY.get_secret_value()
        if not key:
            raise ValueError("SECRET_KEY must be set and non-empty")

        if self.ALGORITHM == "RS256":
            # For RS256, we need both private and public keys
            if not self.JWT_PRIVATE_KEY or not self.JWT_PUBLIC_KEY:
                raise ValueError(
                    "RS256 algorithm requires JWT_PRIVATE_KEY and JWT_PUBLIC_KEY. "
                    "Generate with: openssl genrsa -out private.pem 2048 && "
                    "openssl rsa -in private.pem -pubout -out public.pem"
                )
        else:
            # For HS256, validate SECRET_KEY length
            MIN_SECRET_KEY_LENGTH = 32
            if len(key) < MIN_SECRET_KEY_LENGTH:
                raise ValueError(
                    f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters for HS256. "
                    "Generate with: openssl rand -hex 32"
                )
        return self

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "CryptSettings":
        """Validate encryption keys are set to prevent runtime failures."""
        import logging

        # ENCRYPTION_KEY and ENCRYPTION_SALT should be validated if using features that require them
        if not self.ENCRYPTION_KEY or not self.ENCRYPTION_SALT:
            logging.getLogger("app.core.config").warning(
                "SECURITY: Encryption keys (ENCRYPTION_KEY, ENCRYPTION_SALT) are missing. "
                "Features relying on encryption (e.g., webhooks, sensitive data) may fail."
            )

        return self


class DatabaseSettings(BaseSettings):
    DB_WORKERS: int = Field(default=1, ge=1, le=32, description="Number of uvicorn workers")
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0, le=50, description="Max overflow connections")
    DB_POOL_TIMEOUT: int = Field(default=30, ge=5, le=120)
    POSTGRES_MAX_CONNECTIONS: int = Field(default=100, ge=20, le=500, description="PostgreSQL max_connections limit")
    # DB_POOL_SIZE must be declared AFTER DB_WORKERS, DB_MAX_OVERFLOW, POSTGRES_MAX_CONNECTIONS
    DB_POOL_SIZE: int = Field(default=20, ge=5, le=200, description="Base pool size (calculated as workers × 10)")

    @field_validator("DB_POOL_SIZE", mode="after")
    @classmethod
    def validate_pool_size(cls, v: int, info: ValidationInfo) -> int:
        workers = info.data.get("DB_WORKERS", 1)
        expected_pool = workers * 10
        max_overflow = info.data.get("DB_MAX_OVERFLOW", 10)
        max_conn = info.data.get("POSTGRES_MAX_CONNECTIONS", 100)

        if v < expected_pool:
            import logging

            logging.getLogger("app.core.config").warning(
                f"DB_POOL_SIZE ({v}) is less than recommended ({expected_pool}) for {workers} workers. "
                f"Recommendation: DB_POOL_SIZE={expected_pool}"
            )

        total_max = v + max_overflow
        if total_max > max_conn:
            import logging

            logging.getLogger("app.core.config").error(
                f"Total DB connections ({total_max} = pool {v} + overflow {max_overflow}) "
                f"exceeds PostgreSQL max_connections ({max_conn}). "
                f"Either reduce pool_size/max_overflow or increase PostgreSQL max_connections."
            )
            raise ValueError(
                f"DB_POOL_SIZE + DB_MAX_OVERFLOW ({total_max}) cannot exceed POSTGRES_MAX_CONNECTIONS ({max_conn})"
            )
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def EFFECTIVE_POOL_SIZE(self) -> int:
        """Calculate effective pool size based on worker count.

        Rule of thumb: pool_size = workers × 10
        - 1 worker: 10-20 connections
        - 4 workers: 40-80 connections
        - 8 workers: 80-160 connections
        """
        # If DB_POOL_SIZE is explicitly set, use it. Otherwise auto-calculate.
        if "DB_POOL_SIZE" in self.model_fields_set:
            return self.DB_POOL_SIZE
        return self.DB_WORKERS * 10


class ArqSettings(BaseSettings):
    """ARQ worker and job queue settings."""

    ARQ_ENABLED: bool = Field(default=True, description="Enable ARQ background task queue")
    ARQ_WORKERS: int = Field(default=1, ge=1, le=8, description="Number of ARQ worker processes")
    ARQ_MAX_JOBS: int = Field(default=10, ge=1, le=100, description="Max concurrent jobs per worker")
    ARQ_JOB_TIMEOUT: int = Field(default=300, ge=10, le=3600, description="Default job timeout (seconds)")
    ARQ_KEEP_RESULT: int = Field(default=3600, ge=0, le=86400, description="Keep job results (seconds)")
    ARQ_MAX_TRIES: int = Field(default=3, ge=0, le=10, description="Default max job retries")
    ARQ_QUEUE_MAX_SIZE: int = Field(default=10000, ge=100, le=100000, description="Max queue size per priority")

    @field_validator("ARQ_MAX_JOBS", mode="after")
    @classmethod
    def validate_max_jobs(cls, v: int, info: ValidationInfo) -> int:
        workers = info.data.get("ARQ_WORKERS", 1)
        max_total_jobs = v * workers
        import logging

        logging.getLogger("app.core.config").info(
            f"ARQ total capacity: {workers} workers × {v} max_jobs = {max_total_jobs} concurrent jobs"
        )
        return v


class SQLiteSettings(DatabaseSettings):
    SQLITE_URI: str = "./sql_app.db"
    SQLITE_SYNC_PREFIX: str = "sqlite:///"
    SQLITE_ASYNC_PREFIX: str = "sqlite+aiosqlite:///"
    USE_SQLITE: bool = True


class MySQLSettings(DatabaseSettings):
    MYSQL_USER: str = "username"
    MYSQL_PASSWORD: SecretStr
    MYSQL_SERVER: str = "localhost"
    MYSQL_PORT: int = 3306  # FIX: Correct MySQL port (was 5432 which is PostgreSQL)
    MYSQL_DB: str = "dbname"
    MYSQL_SYNC_PREFIX: str = "mysql://"
    MYSQL_ASYNC_PREFIX: str = "mysql+aiomysql://"
    MYSQL_URL: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def MYSQL_URI(self) -> str:
        password = self.MYSQL_PASSWORD.get_secret_value() if self.MYSQL_PASSWORD else ""
        credentials = f"{self.MYSQL_USER}:{password}"
        location = f"{self.MYSQL_SERVER}:{self.MYSQL_PORT}/{self.MYSQL_DB}"
        return f"{credentials}@{location}"


class PostgresSettings(DatabaseSettings):
    POSTGRES_USER: str
    POSTGRES_PASSWORD: SecretStr
    POSTGRES_SERVER: str = "localhost"  # Override via POSTGRES_SERVER in .env
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "postgres"
    POSTGRES_SYNC_PREFIX: str = "postgresql://"
    POSTGRES_ASYNC_PREFIX: str = "postgresql+asyncpg://"
    POSTGRES_URL: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def POSTGRES_URI(self) -> str:
        password = self.POSTGRES_PASSWORD.get_secret_value() if self.POSTGRES_PASSWORD else ""
        credentials = f"{self.POSTGRES_USER}:{password}"
        location = f"{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        return f"{credentials}@{location}"


class FirstUserSettings(BaseSettings):
    ADMIN_NAME: str = "admin"
    ADMIN_EMAIL: str = "admin@admin.com"
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: SecretStr = Field(..., min_length=8)


class AuthSettings(BaseSettings):
    """Authentication security settings."""

    # Account lockout configuration
    AUTH_MAX_LOGIN_ATTEMPTS: int = 5
    AUTH_LOCKOUT_DURATION_SECONDS: int = 900  # 15 minutes
    # SECURITY FIX: Make audit log failure mode configurable
    # "fail_open": Log error but allow operation (default, availability focused)
    # "fail_closed": Raise exception and block operation (security focused, compliance)
    # SECURITY FIX: Changed default to 'fail_closed' to ensure mandatory audit trails in production.
    AUDIT_LOG_FAILURE_MODE: Literal["fail_open", "fail_closed"] = Field(
        default="fail_closed",
        description="How to handle audit log failures: 'fail_open' (allow) or 'fail_closed' (block)",
    )
    # Configurable MFA pending token expiry (was previously a hardcoded magic number)
    MFA_PENDING_EXPIRY_SECONDS: int = Field(
        default=300, ge=60, le=3600, description="MFA pending token TTL in seconds (default: 5 minutes)"
    )


class TestSettings(BaseSettings): ...


class RedisCacheSettings(BaseSettings):
    REDIS_CACHE_ENABLED: bool = True
    REDIS_CACHE_HOST: str = "localhost"  # Override via REDIS_CACHE_HOST in .env
    REDIS_CACHE_PORT: int = 6379
    REDIS_CACHE_PASSWORD: SecretStr | None = None
    # P2-2: Redis connection pool configuration for better performance under load
    REDIS_CACHE_MAX_CONNECTIONS: int = Field(default=50, ge=10, le=500, description="Max connections in Redis pool")
    REDIS_CACHE_SOCKET_TIMEOUT: float = Field(default=5.0, ge=1.0, le=30.0, description="Socket timeout in seconds")
    REDIS_CACHE_SOCKET_CONNECT_TIMEOUT: float = Field(
        default=5.0, ge=1.0, le=30.0, description="Connection timeout in seconds"
    )
    TENANT_NEGATIVE_CACHE_TTL: int = Field(
        default=300, ge=60, le=3600, description="TTL for invalid tenant slugs (seconds)"
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def REDIS_CACHE_URL(self) -> str:
        if self.REDIS_CACHE_PASSWORD:
            return f"redis://:{self.REDIS_CACHE_PASSWORD.get_secret_value()}@{self.REDIS_CACHE_HOST}:{self.REDIS_CACHE_PORT}"
        return f"redis://{self.REDIS_CACHE_HOST}:{self.REDIS_CACHE_PORT}"


class ClientSideCacheSettings(BaseSettings):
    CLIENT_CACHE_MAX_AGE: int = 60


class SecuritySettings(BaseSettings):
    """Security and performance optimization settings."""

    # Bloom Filter Hash Function Selection
    # xxhash is 10-20x faster than SHA256 for bloom filter operations
    # Can be disabled for rollback by setting BLOOM_FILTER_USE_XXHASH=false
    BLOOM_FILTER_USE_XXHASH: bool = Field(
        default=True,
        description="Use xxhash for bloom filter (10-20x faster) vs SHA256 (legacy)",
    )

    # IP Filtering Configuration
    # Enable/disable IP whitelist checking. When enabled, only IPs in the whitelist are allowed.
    IP_WHITELIST_ENABLED: bool = Field(
        default=False,
        description="Enable IP whitelist filtering. Only allows requests from whitelisted IPs.",
    )
    # Enable/disable IP blacklist checking. When enabled, IPs in the blacklist are blocked.
    IP_BLACKLIST_ENABLED: bool = Field(
        default=True,
        description="Enable IP blacklist filtering. Blocks requests from blacklisted IPs.",
    )
    # When enabled, whitelisted IPs bypass rate limiting restrictions.
    IP_WHITELIST_BYPASS_RATE_LIMIT: bool = Field(
        default=False,
        description="Allow whitelisted IPs to bypass rate limiting checks.",
    )

    # Auto-ban configuration
    # Automatically ban repeat offenders after threshold violations
    AUTO_BAN_ENABLED: bool = Field(
        default=True,
        description="Enable auto-ban for repeat rate limit offenders",
    )
    AUTO_BAN_THRESHOLD: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Number of violations before auto-ban (1-1000)",
    )
    AUTO_BAN_DURATION: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Ban duration in seconds (60-86400 = 1 min to 24 hours)",
    )
    AUTO_BAN_ESCALATION: bool = Field(
        default=True,
        description="Double ban duration on repeat offenses",
    )
    AUTO_BAN_VIOLATION_WINDOW: int = Field(
        default=86400,
        ge=3600,
        le=604800,
        description="Time window in seconds for counting violations (3600-604800 = 1 hour to 7 days)",
    )

    # Bad Bot Filtering Configuration
    BAD_BOT_FILTER_ENABLED: bool = Field(
        default=True,
        description="Enable blocking of known malicious scanners and bots",
    )
    BAD_BOT_USER_AGENTS: list[str] = Field(
        default=[
            "sqlmap",  # SQL injection tool
            "nikto",  # Web scanner
            "nmap",  # Network scanner
            "masscan",  # Port scanner
            "zgrab",  # Go-based scanner
            "gobuster",  # Directory bruteforcer
            "dirbuster",  # Java directory bruteforcer
            "wpscan",  # WordPress scanner
            "nuclei",  # Vulnerability scanner
            "httpx",  # Probe tool
        ],
        description="List of user-agent substrings to block as bad bots",
    )

    # Trusted proxies for rate limiting and IP processing
    TRUSTED_PROXY_IPS: list[str] = Field(
        default=["127.0.0.1", "::1"],
        description="List of trusted proxy IP addresses",
    )
    TRUSTED_PROXY_NETWORKS: list[str] = Field(
        default=["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        description="List of trusted proxy network CIDRs",
    )


class AttackDetectionSettings(BaseSettings):
    """Attack pattern detection configuration.

    Detects SQL injection, XSS, and path traversal attacks.
    Modes:
    - 'log': Log attack and continue (monitoring mode)
    - 'block': Return 400 Bad Request
    - 'ban': Add to blacklist and return 403
    """

    ATTACK_DETECTION_ENABLED: bool = Field(
        default=True,
        description="Enable attack pattern detection middleware",
    )
    ATTACK_DETECTION_MODE: Literal["log", "block", "ban"] = Field(
        default="log",
        description="Attack detection mode: 'log' (monitor), 'block' (400), 'ban' (403 + blacklist)",
    )
    ATTACK_DETECTION_LOG_FULL_REQUEST: bool = Field(
        default=False,
        description="Log full request URL (True) or just matched pattern (False). Privacy consideration.",
    )


class RedisQueueSettings(BaseSettings):
    REDIS_QUEUE_ENABLED: bool = True
    REDIS_QUEUE_HOST: str = "localhost"  # Override via REDIS_QUEUE_HOST in .env
    REDIS_QUEUE_PORT: int = 6379
    REDIS_QUEUE_PASSWORD: SecretStr | None = None
    # P2-2: Explicit pool configuration matching cache settings
    REDIS_QUEUE_MAX_CONNECTIONS: int = Field(default=50, ge=10, le=500, description="Max connections in queue pool")
    REDIS_QUEUE_CONN_TIMEOUT: int = Field(default=5, ge=1, le=30, description="Connection timeout in seconds")
    REDIS_QUEUE_CONN_RETRIES: int = Field(default=5, ge=1, le=20, description="Number of connection retries")


class DefaultRateLimitSettings(BaseSettings):
    DEFAULT_RATE_LIMIT_LIMIT: int = 10
    DEFAULT_RATE_LIMIT_PERIOD: int = 3600
    # If True, allow requests when Redis is unavailable (fail-open)
    # If False, block requests when Redis is unavailable (fail-closed, more secure)
    RATE_LIMIT_FAIL_OPEN: bool = False
    # Rate limiting algorithm: "fixed_window" or "gcra"
    # GCRA provides smoother limiting without boundary burst issues
    RATE_LIMIT_ALGORITHM: str = "gcra"


class CRUDAdminSettings(BaseSettings):
    CRUD_ADMIN_ENABLED: bool = True
    CRUD_ADMIN_MOUNT_PATH: str = "/admin"

    CRUD_ADMIN_ALLOWED_IPS_LIST: list[str] | None = None
    CRUD_ADMIN_ALLOWED_NETWORKS_LIST: list[str] | None = None
    CRUD_ADMIN_MAX_SESSIONS: int = 10
    CRUD_ADMIN_SESSION_TIMEOUT: int = 1440
    SESSION_SECURE_COOKIES: bool = True

    CRUD_ADMIN_TRACK_EVENTS: bool = True
    CRUD_ADMIN_TRACK_SESSIONS: bool = True

    CRUD_ADMIN_REDIS_ENABLED: bool = True
    CRUD_ADMIN_REDIS_HOST: str = "localhost"  # Override via CRUD_ADMIN_REDIS_HOST in .env
    CRUD_ADMIN_REDIS_PORT: int = 6379
    CRUD_ADMIN_REDIS_DB: int = 0
    CRUD_ADMIN_REDIS_PASSWORD: SecretStr | None = None
    CRUD_ADMIN_REDIS_SSL: bool = False


class EmailSettings(BaseSettings):
    EMAIL_FROM_NAME: str = "FastAPI SaaS"
    EMAIL_FROM_ADDRESS: str = "noreply@fastapi.local"
    EMAIL_REPLY_TO: str | None = None
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: SecretStr | None = None
    SMTP_USE_TLS: bool = True
    EMAIL_TEMPLATES_DIR: str = "src/app/templates"


class WebhookSettings(BaseSettings):
    WEBHOOK_MAX_RETRIES: int = 3
    WEBHOOK_RETRY_DELAY_SECONDS: int = 300
    WEBHOOK_TIMEOUT_SECONDS: int = 10
    WEBHOOK_SIGNATURE_HEADER: str = "X-Webhook-Signature"
    WEBHOOK_SIGNATURE_ALGORITHM: str = "sha256"


class EnvironmentOption(str, Enum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class EnvironmentSettings(BaseSettings):
    ENVIRONMENT: EnvironmentOption = EnvironmentOption.LOCAL


class StorageSettings(BaseSettings):
    STORAGE_PROVIDER: str = "s3"  # Options: s3, local (future)
    STORAGE_BUCKET: str = "fastapi-saas-user-content"
    STORAGE_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: SecretStr | None = None
    AWS_ENDPOINT_URL: str | None = None  # For localstack or MinIO


class CORSSettings(BaseSettings):
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]  # SECURITY: Restrict to specific origins
    CORS_METHODS: list[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    CORS_HEADERS: list[str] = [
        "Content-Type",
        "Authorization",
        "X-Request-ID",
        "X-Webhook-Signature",
        "X-Webhook-Event",
        "X-Webhook-ID",
        "X-Webhook-Timestamp",
        "X-Tenant-ID",
    ]

    @model_validator(mode="after")
    def validate_cors_origins(self) -> "CORSSettings":
        """Validate CORS origins for production safety.

        Warns if wildcard '*' is used in non-local environments.
        """
        import logging

        env = os.getenv("ENVIRONMENT", "local")
        has_wildcard = "*" in self.CORS_ORIGINS

        if has_wildcard and env != "local":
            logging.getLogger(__name__).warning(
                "SECURITY WARNING: CORS_ORIGINS contains '*' wildcard in %s environment. "
                "This allows any website to access your API. "
                "Consider specifying explicit origins for production.",
                env,
            )

        return self


class BillingSettings(BaseSettings):
    """Stripe and Billing settings."""

    STRIPE_SECRET_KEY: SecretStr | None = None
    STRIPE_PUBLISHABLE_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: SecretStr | None = None


class AISettings(BaseSettings):
    """Settings for AI/LLM integration (Ollama)."""

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_TIMEOUT: float = 120.0
    OLLAMA_DEFAULT_MODEL: str = "gemma3"

    # HTTP Client Connection Limits (prevents resource exhaustion under load)
    OLLAMA_MAX_CONNECTIONS: int = 100  # Total concurrent connections to Ollama
    OLLAMA_MAX_KEEPALIVE: int = 20  # Idle connections kept alive for reuse
    OLLAMA_KEEPALIVE_EXPIRY: float = 30.0  # Seconds before idle connection closes


class EventSettings(BaseSettings):
    """Settings for event handling."""

    EVENT_HANDLER_TIMEOUT_SECONDS: float = 30.0
    EVENT_HANDLER_TIMEOUT_CRITICAL: float = 60.0  # For known slow handlers
    EVENT_BUS_CONCURRENCY: int = 50  # Max concurrent sync handlers


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

    XFETCH_USE_LUA_SCRIPT: bool = True
    """Use Lua script for atomic GET+TTL in XFetch (P2-2)."""

    RBAC_USE_REDIS_CACHE: bool = True
    """Use Redis caching for RBAC permission checks (P5)."""

    TOKEN_NEGATIVE_CACHE_TTL: int = 300
    """TTL in seconds for token blacklist negative cache (LOW-2). Default 5 minutes."""

    RBAC_CACHE_TTL_SECONDS: int = 86400
    """TTL in seconds for RBAC permission cache. Default 24 hours (86400 seconds)."""

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

    FEATURE_STREAMING_UPLOADS: bool = True
    """Use streaming multipart uploads for S3 storage (P0-3)."""

    FEATURE_EAGER_LOADING: bool = False
    """Use eager loading for relationships in base service (P0-4)."""

    FEATURE_REMOVE_WASTED_CONNECTION: bool = True
    """Remove wasted DB connection in tenant middleware (P0-1)."""


class QueryProfilerSettings(BaseSettings):
    """Development mode SQL query profiler configuration.

    Only active in non-production environments. Helps identify:
    - Slow queries (> threshold ms)
    - N+1 query issues
    - Inefficient database access patterns
    """

    model_config = SettingsConfigDict(env_prefix="QUERY_PROFILER_")

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


class Settings(BaseSettings):
    """Main application settings using composition.

    R1-UNIFIED P2-5: Refactored from inheritance to composition to reduce
    Pydantic model bloat and improve configuration resolution clarity.
    """

    app: AppSettings = Field(default_factory=AppSettings)
    sqlite: SQLiteSettings = Field(default_factory=SQLiteSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)  # type: ignore[arg-type]
    crypt: CryptSettings = Field(default_factory=CryptSettings)  # type: ignore[arg-type]
    first_user: FirstUserSettings = Field(default_factory=FirstUserSettings)  # type: ignore[arg-type]
    auth: AuthSettings = Field(default_factory=AuthSettings)
    test: TestSettings = Field(default_factory=TestSettings)
    redis_cache: RedisCacheSettings = Field(default_factory=RedisCacheSettings)
    client_cache: ClientSideCacheSettings = Field(default_factory=ClientSideCacheSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    attack_detection: AttackDetectionSettings = Field(default_factory=AttackDetectionSettings)
    redis_queue: RedisQueueSettings = Field(default_factory=RedisQueueSettings)
    rate_limit: DefaultRateLimitSettings = Field(default_factory=DefaultRateLimitSettings)
    admin: CRUDAdminSettings = Field(default_factory=CRUDAdminSettings)
    email: EmailSettings = Field(default_factory=EmailSettings)
    webhooks: WebhookSettings = Field(default_factory=WebhookSettings)
    env: EnvironmentSettings = Field(default_factory=EnvironmentSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    billing: BillingSettings = Field(default_factory=BillingSettings)
    ai: AISettings = Field(default_factory=AISettings)
    events: EventSettings = Field(default_factory=EventSettings)
    cors: CORSSettings = Field(default_factory=CORSSettings)
    arq: ArqSettings = Field(default_factory=ArqSettings)
    r1_unified: R1UnifiedSettings = Field(default_factory=R1UnifiedSettings)
    performance: PerformanceSettings = Field(default_factory=PerformanceSettings)
    query_profiler: QueryProfilerSettings = Field(default_factory=QueryProfilerSettings)

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def __getattr__(self, name: str) -> Any:
        """Proxy attribute access to sub-models for backward compatibility.

        This allows existing code to use `settings.FIELD` while we transition
        to nested access `settings.submodel.FIELD`.
        """
        for sub_model in [
            self.app,
            self.sqlite,
            self.postgres,
            self.crypt,
            self.first_user,
            self.auth,
            self.test,
            self.redis_cache,
            self.client_cache,
            self.security,
            self.attack_detection,
            self.redis_queue,
            self.rate_limit,
            self.admin,
            self.email,
            self.webhooks,
            self.env,
            self.storage,
            self.billing,
            self.ai,
            self.events,
            self.cors,
            self.arq,
            self.r1_unified,
            self.performance,
            self.query_profiler,
        ]:
            if hasattr(sub_model, name):
                return getattr(sub_model, name)

        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        """Proxy attribute assignments for test patching.

        This allows test suites to patch `settings.FIELD = value` without
        crashing Pydantic and preserves test isolation by storing the mock
        on the Settings instance instead of mutating the underlying sub-model.
        """
        if name in self.model_fields:
            super().__setattr__(name, value)
        else:
            object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        """Proxy attribute deletion for test patching teardowns.

        This allows mock.patch to clean up injected test overrides.
        """
        if name in self.model_fields:
            super().__delattr__(name)
        else:
            try:
                object.__delattr__(self, name)
            except AttributeError:
                raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    # Explicit type hints for common fields to satisfy mypy
    # These match the most frequently accessed settings project-wide
    if TYPE_CHECKING:
        SECRET_KEY: SecretStr
        ENVIRONMENT: EnvironmentOption
        USE_SQLITE: bool
        ADMIN_PASSWORD: SecretStr
        APP_VERSION: str
        CREATE_TABLES_ON_STARTUP: bool
        ARQ_ENABLED: bool
        MAX_BODY_SIZE: int
        POSTGRES_URI: str
        POSTGRES_ASYNC_PREFIX: str
        DB_POOL_SIZE: int
        DB_MAX_OVERFLOW: int
        DB_POOL_TIMEOUT: int
        REDIS_CACHE_URL: str
        FRONTEND_URL: str
        ALGORITHM: str
        ACCESS_TOKEN_EXPIRE_MINUTES: int
        REFRESH_TOKEN_EXPIRE_DAYS: int
        JWT_AUDIENCE: str
        JWT_PRIVATE_KEY: SecretStr | None
        JWT_PUBLIC_KEY: str | None
        ENCRYPTION_KEY: SecretStr | None
        ENCRYPTION_SALT: SecretStr | None
        IP_WHITELIST_ENABLED: bool
        IP_BLACKLIST_ENABLED: bool
        IP_WHITELIST_BYPASS_RATE_LIMIT: bool
        AUTO_BAN_ENABLED: bool
        AUTO_BAN_THRESHOLD: int
        AUTO_BAN_DURATION: int
        AUTO_BAN_ESCALATION: bool
        BAD_BOT_FILTER_ENABLED: bool
        BAD_BOT_USER_AGENTS: list[str]
        RBAC_USE_REDIS_CACHE: bool
        RBAC_CACHE_TTL_SECONDS: int
        STRIPE_SECRET_KEY: SecretStr | None
        STRIPE_WEBHOOK_SECRET: SecretStr | None
        OLLAMA_BASE_URL: str
        OLLAMA_TIMEOUT: float
        STORAGE_PROVIDER: str
        STORAGE_BUCKET: str
        STORAGE_REGION: str
        AWS_ACCESS_KEY_ID: str | None
        AWS_SECRET_ACCESS_KEY: SecretStr | None
        AWS_ENDPOINT_URL: str | None
        EVENT_BUS_CONCURRENCY: int
        EVENT_HANDLER_TIMEOUT_SECONDS: float
        EVENT_BUS_USE_BATCH_ENQUEUE: bool
        QUERY_PROFILER_ENABLED: bool
        QUERY_PROFILER_SLOW_THRESHOLD_MS: int
        QUERY_PROFILER_N_PLUS_1_THRESHOLD: int
        QUERY_PROFILER_DETAILED_LOGS: bool


# Initialize settings without arguments - Pydantic will populate from env
settings = Settings()  # type: ignore[call-arg]
