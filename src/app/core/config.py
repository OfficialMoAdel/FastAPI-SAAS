import os
from enum import Enum
from typing import Any, Literal

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
    SECRET_KEY: SecretStr | None
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
        if not self.SECRET_KEY:
            raise ValueError("SECRET_KEY must be set and non-empty")
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
            import os

            if os.getenv("ENVIRONMENT", "development").lower() == "production":
                raise ValueError("SECURITY: ENCRYPTION_KEY and ENCRYPTION_SALT must be set in production.")
            import os

            if os.getenv("ENVIRONMENT", "development").lower() == "production":
                raise ValueError("SECURITY: ENCRYPTION_KEY and ENCRYPTION_SALT must be set in production.")
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
    MYSQL_PASSWORD: SecretStr | None
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
    POSTGRES_PASSWORD: SecretStr | None
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
    ADMIN_PASSWORD: SecretStr | None = Field(..., min_length=8)


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
    BAD_BOT_USER_AGENTS: list[str] | None = Field(
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
    TRUSTED_PROXY_IPS: list[str] | None = Field(
        default=["127.0.0.1", "::1"],
        description="List of trusted proxy IP addresses",
    )
    TRUSTED_PROXY_NETWORKS: list[str] | None = Field(
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

        env = os.getenv("ENVIRONMENT", "local")
        has_wildcard = "*" in self.CORS_ORIGINS

        if has_wildcard and env != "local":
            raise ValueError(
                f"SECURITY ERROR: CORS_ORIGINS contains '*' wildcard in {env} environment. "
                "This allows any website to access your API. "
                "Specify explicit origins for production."
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

    TOKEN_NEGATIVE_CACHE_TTL: int = 60
    """TTL in seconds for token blacklist negative cache (LOW-2).
    Reduced to 60 seconds to prevent delay in invalidation.
    """

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

    @property
    def APP_NAME(self) -> str:
        return self.app.APP_NAME

    @APP_NAME.setter
    def APP_NAME(self, value: str) -> None:
        self.app.APP_NAME = value

    @property
    def APP_DESCRIPTION(self) -> str | None:
        return self.app.APP_DESCRIPTION

    @APP_DESCRIPTION.setter
    def APP_DESCRIPTION(self, value: str | None) -> None:
        self.app.APP_DESCRIPTION = value

    @property
    def APP_VERSION(self) -> str | None:
        return self.app.APP_VERSION

    @APP_VERSION.setter
    def APP_VERSION(self, value: str | None) -> None:
        self.app.APP_VERSION = value

    @property
    def LICENSE_NAME(self) -> str | None:
        return self.app.LICENSE_NAME

    @LICENSE_NAME.setter
    def LICENSE_NAME(self, value: str | None) -> None:
        self.app.LICENSE_NAME = value

    @property
    def CONTACT_NAME(self) -> str | None:
        return self.app.CONTACT_NAME

    @CONTACT_NAME.setter
    def CONTACT_NAME(self, value: str | None) -> None:
        self.app.CONTACT_NAME = value

    @property
    def CONTACT_EMAIL(self) -> str | None:
        return self.app.CONTACT_EMAIL

    @CONTACT_EMAIL.setter
    def CONTACT_EMAIL(self, value: str | None) -> None:
        self.app.CONTACT_EMAIL = value

    @property
    def FRONTEND_URL(self) -> str:
        return self.app.FRONTEND_URL

    @FRONTEND_URL.setter
    def FRONTEND_URL(self, value: str) -> None:
        self.app.FRONTEND_URL = value

    @property
    def CREATE_TABLES_ON_STARTUP(self) -> bool:
        return self.app.CREATE_TABLES_ON_STARTUP

    @CREATE_TABLES_ON_STARTUP.setter
    def CREATE_TABLES_ON_STARTUP(self, value: bool) -> None:
        self.app.CREATE_TABLES_ON_STARTUP = value

    @property
    def MAX_BODY_SIZE(self) -> int:
        return self.app.MAX_BODY_SIZE

    @MAX_BODY_SIZE.setter
    def MAX_BODY_SIZE(self, value: int) -> None:
        self.app.MAX_BODY_SIZE = value

    @property
    def RESERVED_USERNAMES(self) -> frozenset[str]:
        return self.app.RESERVED_USERNAMES

    @RESERVED_USERNAMES.setter
    def RESERVED_USERNAMES(self, value: frozenset[str]) -> None:
        self.app.RESERVED_USERNAMES = value

    @property
    def PAGINATION_DEFAULT_PER_PAGE(self) -> int:
        return self.app.PAGINATION_DEFAULT_PER_PAGE

    @PAGINATION_DEFAULT_PER_PAGE.setter
    def PAGINATION_DEFAULT_PER_PAGE(self, value: int) -> None:
        self.app.PAGINATION_DEFAULT_PER_PAGE = value

    @property
    def PAGINATION_MAX_PER_PAGE(self) -> int:
        return self.app.PAGINATION_MAX_PER_PAGE

    @PAGINATION_MAX_PER_PAGE.setter
    def PAGINATION_MAX_PER_PAGE(self, value: int) -> None:
        self.app.PAGINATION_MAX_PER_PAGE = value

    @property
    def model_extra(self) -> dict[str, Any] | None:
        return self.app.model_extra

    @property
    def model_fields_set(self) -> set:
        return self.app.model_fields_set

    @property
    def DB_WORKERS(self) -> int:
        return self.sqlite.DB_WORKERS

    @DB_WORKERS.setter
    def DB_WORKERS(self, value: int) -> None:
        self.sqlite.DB_WORKERS = value

    @property
    def DB_MAX_OVERFLOW(self) -> int:
        return self.sqlite.DB_MAX_OVERFLOW

    @DB_MAX_OVERFLOW.setter
    def DB_MAX_OVERFLOW(self, value: int) -> None:
        self.sqlite.DB_MAX_OVERFLOW = value

    @property
    def DB_POOL_TIMEOUT(self) -> int:
        return self.sqlite.DB_POOL_TIMEOUT

    @DB_POOL_TIMEOUT.setter
    def DB_POOL_TIMEOUT(self, value: int) -> None:
        self.sqlite.DB_POOL_TIMEOUT = value

    @property
    def POSTGRES_MAX_CONNECTIONS(self) -> int:
        return self.sqlite.POSTGRES_MAX_CONNECTIONS

    @POSTGRES_MAX_CONNECTIONS.setter
    def POSTGRES_MAX_CONNECTIONS(self, value: int) -> None:
        self.sqlite.POSTGRES_MAX_CONNECTIONS = value

    @property
    def DB_POOL_SIZE(self) -> int:
        return self.sqlite.DB_POOL_SIZE

    @DB_POOL_SIZE.setter
    def DB_POOL_SIZE(self, value: int) -> None:
        self.sqlite.DB_POOL_SIZE = value

    @property
    def SQLITE_URI(self) -> str:
        return self.sqlite.SQLITE_URI

    @SQLITE_URI.setter
    def SQLITE_URI(self, value: str) -> None:
        self.sqlite.SQLITE_URI = value

    @property
    def SQLITE_SYNC_PREFIX(self) -> str:
        return self.sqlite.SQLITE_SYNC_PREFIX

    @SQLITE_SYNC_PREFIX.setter
    def SQLITE_SYNC_PREFIX(self, value: str) -> None:
        self.sqlite.SQLITE_SYNC_PREFIX = value

    @property
    def SQLITE_ASYNC_PREFIX(self) -> str:
        return self.sqlite.SQLITE_ASYNC_PREFIX

    @SQLITE_ASYNC_PREFIX.setter
    def SQLITE_ASYNC_PREFIX(self, value: str) -> None:
        self.sqlite.SQLITE_ASYNC_PREFIX = value

    @property
    def USE_SQLITE(self) -> bool:
        return self.sqlite.USE_SQLITE

    @USE_SQLITE.setter
    def USE_SQLITE(self, value: bool) -> None:
        self.sqlite.USE_SQLITE = value

    @property
    def EFFECTIVE_POOL_SIZE(self) -> int:
        return self.sqlite.EFFECTIVE_POOL_SIZE

    @property
    def POSTGRES_USER(self) -> str:
        return self.postgres.POSTGRES_USER

    @POSTGRES_USER.setter
    def POSTGRES_USER(self, value: str) -> None:
        self.postgres.POSTGRES_USER = value

    @property
    def POSTGRES_PASSWORD(self) -> SecretStr:
        assert self.postgres.POSTGRES_PASSWORD is not None, "POSTGRES_PASSWORD must be set"
        return self.postgres.POSTGRES_PASSWORD

    @POSTGRES_PASSWORD.setter
    def POSTGRES_PASSWORD(self, value: SecretStr | None) -> None:
        self.postgres.POSTGRES_PASSWORD = value

    @property
    def POSTGRES_SERVER(self) -> str:
        return self.postgres.POSTGRES_SERVER

    @POSTGRES_SERVER.setter
    def POSTGRES_SERVER(self, value: str) -> None:
        self.postgres.POSTGRES_SERVER = value

    @property
    def POSTGRES_PORT(self) -> int:
        return self.postgres.POSTGRES_PORT

    @POSTGRES_PORT.setter
    def POSTGRES_PORT(self, value: int) -> None:
        self.postgres.POSTGRES_PORT = value

    @property
    def POSTGRES_DB(self) -> str:
        return self.postgres.POSTGRES_DB

    @POSTGRES_DB.setter
    def POSTGRES_DB(self, value: str) -> None:
        self.postgres.POSTGRES_DB = value

    @property
    def POSTGRES_SYNC_PREFIX(self) -> str:
        return self.postgres.POSTGRES_SYNC_PREFIX

    @POSTGRES_SYNC_PREFIX.setter
    def POSTGRES_SYNC_PREFIX(self, value: str) -> None:
        self.postgres.POSTGRES_SYNC_PREFIX = value

    @property
    def POSTGRES_ASYNC_PREFIX(self) -> str:
        return self.postgres.POSTGRES_ASYNC_PREFIX

    @POSTGRES_ASYNC_PREFIX.setter
    def POSTGRES_ASYNC_PREFIX(self, value: str) -> None:
        self.postgres.POSTGRES_ASYNC_PREFIX = value

    @property
    def POSTGRES_URL(self) -> str | None:
        return self.postgres.POSTGRES_URL

    @POSTGRES_URL.setter
    def POSTGRES_URL(self, value: str | None) -> None:
        self.postgres.POSTGRES_URL = value

    @property
    def POSTGRES_URI(self) -> str:
        return self.postgres.POSTGRES_URI

    @property
    def SECRET_KEY(self) -> SecretStr:
        assert self.crypt.SECRET_KEY is not None, "SECRET_KEY must be set"
        return self.crypt.SECRET_KEY

    @SECRET_KEY.setter
    def SECRET_KEY(self, value: SecretStr | None) -> None:
        self.crypt.SECRET_KEY = value

    @property
    def ALGORITHM(self) -> str:
        return self.crypt.ALGORITHM

    @ALGORITHM.setter
    def ALGORITHM(self, value: str) -> None:
        self.crypt.ALGORITHM = value

    @property
    def ACCESS_TOKEN_EXPIRE_MINUTES(self) -> int:
        return self.crypt.ACCESS_TOKEN_EXPIRE_MINUTES

    @ACCESS_TOKEN_EXPIRE_MINUTES.setter
    def ACCESS_TOKEN_EXPIRE_MINUTES(self, value: int) -> None:
        self.crypt.ACCESS_TOKEN_EXPIRE_MINUTES = value

    @property
    def REFRESH_TOKEN_EXPIRE_DAYS(self) -> int:
        return self.crypt.REFRESH_TOKEN_EXPIRE_DAYS

    @REFRESH_TOKEN_EXPIRE_DAYS.setter
    def REFRESH_TOKEN_EXPIRE_DAYS(self, value: int) -> None:
        self.crypt.REFRESH_TOKEN_EXPIRE_DAYS = value

    @property
    def MAX_SESSION_DURATION_DAYS(self) -> int:
        return self.crypt.MAX_SESSION_DURATION_DAYS

    @MAX_SESSION_DURATION_DAYS.setter
    def MAX_SESSION_DURATION_DAYS(self, value: int) -> None:
        self.crypt.MAX_SESSION_DURATION_DAYS = value

    @property
    def BCRYPT_ROUNDS(self) -> int:
        return self.crypt.BCRYPT_ROUNDS

    @BCRYPT_ROUNDS.setter
    def BCRYPT_ROUNDS(self, value: int) -> None:
        self.crypt.BCRYPT_ROUNDS = value

    @property
    def BCRYPT_THREAD_POOL_SIZE(self) -> int:
        return self.crypt.BCRYPT_THREAD_POOL_SIZE

    @BCRYPT_THREAD_POOL_SIZE.setter
    def BCRYPT_THREAD_POOL_SIZE(self, value: int) -> None:
        self.crypt.BCRYPT_THREAD_POOL_SIZE = value

    @property
    def ENCRYPTION_KEY(self) -> SecretStr | None:
        return self.crypt.ENCRYPTION_KEY

    @ENCRYPTION_KEY.setter
    def ENCRYPTION_KEY(self, value: SecretStr | None) -> None:
        self.crypt.ENCRYPTION_KEY = value

    @property
    def ENCRYPTION_SALT(self) -> SecretStr | None:
        return self.crypt.ENCRYPTION_SALT

    @ENCRYPTION_SALT.setter
    def ENCRYPTION_SALT(self, value: SecretStr | None) -> None:
        self.crypt.ENCRYPTION_SALT = value

    @property
    def JWT_PRIVATE_KEY(self) -> SecretStr | None:
        return self.crypt.JWT_PRIVATE_KEY

    @JWT_PRIVATE_KEY.setter
    def JWT_PRIVATE_KEY(self, value: Any) -> None:
        self.crypt.JWT_PRIVATE_KEY = value

    @property
    def JWT_PUBLIC_KEY(self) -> str | None:
        return self.crypt.JWT_PUBLIC_KEY

    @JWT_PUBLIC_KEY.setter
    def JWT_PUBLIC_KEY(self, value: str | None) -> None:
        self.crypt.JWT_PUBLIC_KEY = value

    @property
    def JWT_AUDIENCE(self) -> str:
        return self.crypt.JWT_AUDIENCE

    @JWT_AUDIENCE.setter
    def JWT_AUDIENCE(self, value: str) -> None:
        self.crypt.JWT_AUDIENCE = value

    @property
    def ADMIN_NAME(self) -> str:
        return self.first_user.ADMIN_NAME

    @ADMIN_NAME.setter
    def ADMIN_NAME(self, value: str) -> None:
        self.first_user.ADMIN_NAME = value

    @property
    def ADMIN_EMAIL(self) -> str:
        return self.first_user.ADMIN_EMAIL

    @ADMIN_EMAIL.setter
    def ADMIN_EMAIL(self, value: str) -> None:
        self.first_user.ADMIN_EMAIL = value

    @property
    def ADMIN_USERNAME(self) -> str:
        return self.first_user.ADMIN_USERNAME

    @ADMIN_USERNAME.setter
    def ADMIN_USERNAME(self, value: str) -> None:
        self.first_user.ADMIN_USERNAME = value

    @property
    def ADMIN_PASSWORD(self) -> SecretStr:
        assert self.first_user.ADMIN_PASSWORD is not None, "ADMIN_PASSWORD must be set"
        return self.first_user.ADMIN_PASSWORD

    @ADMIN_PASSWORD.setter
    def ADMIN_PASSWORD(self, value: SecretStr | None) -> None:
        self.first_user.ADMIN_PASSWORD = value

    @property
    def AUTH_MAX_LOGIN_ATTEMPTS(self) -> int:
        return self.auth.AUTH_MAX_LOGIN_ATTEMPTS

    @AUTH_MAX_LOGIN_ATTEMPTS.setter
    def AUTH_MAX_LOGIN_ATTEMPTS(self, value: int) -> None:
        self.auth.AUTH_MAX_LOGIN_ATTEMPTS = value

    @property
    def AUTH_LOCKOUT_DURATION_SECONDS(self) -> int:
        return self.auth.AUTH_LOCKOUT_DURATION_SECONDS

    @AUTH_LOCKOUT_DURATION_SECONDS.setter
    def AUTH_LOCKOUT_DURATION_SECONDS(self, value: int) -> None:
        self.auth.AUTH_LOCKOUT_DURATION_SECONDS = value

    @property
    def AUDIT_LOG_FAILURE_MODE(self) -> Literal["fail_open", "fail_closed"]:
        return self.auth.AUDIT_LOG_FAILURE_MODE

    @AUDIT_LOG_FAILURE_MODE.setter
    def AUDIT_LOG_FAILURE_MODE(self, value: Any) -> None:
        self.auth.AUDIT_LOG_FAILURE_MODE = value

    @property
    def MFA_PENDING_EXPIRY_SECONDS(self) -> int:
        return self.auth.MFA_PENDING_EXPIRY_SECONDS

    @MFA_PENDING_EXPIRY_SECONDS.setter
    def MFA_PENDING_EXPIRY_SECONDS(self, value: int) -> None:
        self.auth.MFA_PENDING_EXPIRY_SECONDS = value

    @property
    def REDIS_CACHE_ENABLED(self) -> bool:
        return self.redis_cache.REDIS_CACHE_ENABLED

    @REDIS_CACHE_ENABLED.setter
    def REDIS_CACHE_ENABLED(self, value: bool) -> None:
        self.redis_cache.REDIS_CACHE_ENABLED = value

    @property
    def REDIS_CACHE_HOST(self) -> str:
        return self.redis_cache.REDIS_CACHE_HOST

    @REDIS_CACHE_HOST.setter
    def REDIS_CACHE_HOST(self, value: str) -> None:
        self.redis_cache.REDIS_CACHE_HOST = value

    @property
    def REDIS_CACHE_PORT(self) -> int:
        return self.redis_cache.REDIS_CACHE_PORT

    @REDIS_CACHE_PORT.setter
    def REDIS_CACHE_PORT(self, value: int) -> None:
        self.redis_cache.REDIS_CACHE_PORT = value

    @property
    def REDIS_CACHE_PASSWORD(self) -> SecretStr | None:
        return self.redis_cache.REDIS_CACHE_PASSWORD

    @REDIS_CACHE_PASSWORD.setter
    def REDIS_CACHE_PASSWORD(self, value: SecretStr | None) -> None:
        self.redis_cache.REDIS_CACHE_PASSWORD = value

    @property
    def REDIS_CACHE_MAX_CONNECTIONS(self) -> int:
        return self.redis_cache.REDIS_CACHE_MAX_CONNECTIONS

    @REDIS_CACHE_MAX_CONNECTIONS.setter
    def REDIS_CACHE_MAX_CONNECTIONS(self, value: int) -> None:
        self.redis_cache.REDIS_CACHE_MAX_CONNECTIONS = value

    @property
    def REDIS_CACHE_SOCKET_TIMEOUT(self) -> float:
        return self.redis_cache.REDIS_CACHE_SOCKET_TIMEOUT

    @REDIS_CACHE_SOCKET_TIMEOUT.setter
    def REDIS_CACHE_SOCKET_TIMEOUT(self, value: float) -> None:
        self.redis_cache.REDIS_CACHE_SOCKET_TIMEOUT = value

    @property
    def REDIS_CACHE_SOCKET_CONNECT_TIMEOUT(self) -> float:
        return self.redis_cache.REDIS_CACHE_SOCKET_CONNECT_TIMEOUT

    @REDIS_CACHE_SOCKET_CONNECT_TIMEOUT.setter
    def REDIS_CACHE_SOCKET_CONNECT_TIMEOUT(self, value: float) -> None:
        self.redis_cache.REDIS_CACHE_SOCKET_CONNECT_TIMEOUT = value

    @property
    def TENANT_NEGATIVE_CACHE_TTL(self) -> int:
        return self.redis_cache.TENANT_NEGATIVE_CACHE_TTL

    @TENANT_NEGATIVE_CACHE_TTL.setter
    def TENANT_NEGATIVE_CACHE_TTL(self, value: int) -> None:
        self.redis_cache.TENANT_NEGATIVE_CACHE_TTL = value

    @property
    def REDIS_CACHE_URL(self) -> str:
        return self.redis_cache.REDIS_CACHE_URL

    @property
    def CLIENT_CACHE_MAX_AGE(self) -> int:
        return self.client_cache.CLIENT_CACHE_MAX_AGE

    @CLIENT_CACHE_MAX_AGE.setter
    def CLIENT_CACHE_MAX_AGE(self, value: int) -> None:
        self.client_cache.CLIENT_CACHE_MAX_AGE = value

    @property
    def BLOOM_FILTER_USE_XXHASH(self) -> bool:
        return self.security.BLOOM_FILTER_USE_XXHASH

    @BLOOM_FILTER_USE_XXHASH.setter
    def BLOOM_FILTER_USE_XXHASH(self, value: bool) -> None:
        self.security.BLOOM_FILTER_USE_XXHASH = value

    @property
    def IP_WHITELIST_ENABLED(self) -> bool:
        return self.security.IP_WHITELIST_ENABLED

    @IP_WHITELIST_ENABLED.setter
    def IP_WHITELIST_ENABLED(self, value: bool) -> None:
        self.security.IP_WHITELIST_ENABLED = value

    @property
    def IP_BLACKLIST_ENABLED(self) -> bool:
        return self.security.IP_BLACKLIST_ENABLED

    @IP_BLACKLIST_ENABLED.setter
    def IP_BLACKLIST_ENABLED(self, value: bool) -> None:
        self.security.IP_BLACKLIST_ENABLED = value

    @property
    def IP_WHITELIST_BYPASS_RATE_LIMIT(self) -> bool:
        return self.security.IP_WHITELIST_BYPASS_RATE_LIMIT

    @IP_WHITELIST_BYPASS_RATE_LIMIT.setter
    def IP_WHITELIST_BYPASS_RATE_LIMIT(self, value: bool) -> None:
        self.security.IP_WHITELIST_BYPASS_RATE_LIMIT = value

    @property
    def AUTO_BAN_ENABLED(self) -> bool:
        return self.security.AUTO_BAN_ENABLED

    @AUTO_BAN_ENABLED.setter
    def AUTO_BAN_ENABLED(self, value: bool) -> None:
        self.security.AUTO_BAN_ENABLED = value

    @property
    def AUTO_BAN_THRESHOLD(self) -> int:
        return self.security.AUTO_BAN_THRESHOLD

    @AUTO_BAN_THRESHOLD.setter
    def AUTO_BAN_THRESHOLD(self, value: int) -> None:
        self.security.AUTO_BAN_THRESHOLD = value

    @property
    def AUTO_BAN_DURATION(self) -> int:
        return self.security.AUTO_BAN_DURATION

    @AUTO_BAN_DURATION.setter
    def AUTO_BAN_DURATION(self, value: int) -> None:
        self.security.AUTO_BAN_DURATION = value

    @property
    def AUTO_BAN_ESCALATION(self) -> bool:
        return self.security.AUTO_BAN_ESCALATION

    @AUTO_BAN_ESCALATION.setter
    def AUTO_BAN_ESCALATION(self, value: bool) -> None:
        self.security.AUTO_BAN_ESCALATION = value

    @property
    def AUTO_BAN_VIOLATION_WINDOW(self) -> int:
        return self.security.AUTO_BAN_VIOLATION_WINDOW

    @AUTO_BAN_VIOLATION_WINDOW.setter
    def AUTO_BAN_VIOLATION_WINDOW(self, value: int) -> None:
        self.security.AUTO_BAN_VIOLATION_WINDOW = value

    @property
    def BAD_BOT_FILTER_ENABLED(self) -> bool:
        return self.security.BAD_BOT_FILTER_ENABLED

    @BAD_BOT_FILTER_ENABLED.setter
    def BAD_BOT_FILTER_ENABLED(self, value: bool) -> None:
        self.security.BAD_BOT_FILTER_ENABLED = value

    @property
    def BAD_BOT_USER_AGENTS(self) -> list[str] | None:
        return self.security.BAD_BOT_USER_AGENTS

    @BAD_BOT_USER_AGENTS.setter
    def BAD_BOT_USER_AGENTS(self, value: list[str] | None) -> None:
        self.security.BAD_BOT_USER_AGENTS = value

    @property
    def TRUSTED_PROXY_IPS(self) -> list[str] | None:
        return self.security.TRUSTED_PROXY_IPS

    @TRUSTED_PROXY_IPS.setter
    def TRUSTED_PROXY_IPS(self, value: list[str] | None) -> None:
        self.security.TRUSTED_PROXY_IPS = value

    @property
    def TRUSTED_PROXY_NETWORKS(self) -> list[str] | None:
        return self.security.TRUSTED_PROXY_NETWORKS

    @TRUSTED_PROXY_NETWORKS.setter
    def TRUSTED_PROXY_NETWORKS(self, value: list[str] | None) -> None:
        self.security.TRUSTED_PROXY_NETWORKS = value

    @property
    def ATTACK_DETECTION_ENABLED(self) -> bool:
        return self.attack_detection.ATTACK_DETECTION_ENABLED

    @ATTACK_DETECTION_ENABLED.setter
    def ATTACK_DETECTION_ENABLED(self, value: bool) -> None:
        self.attack_detection.ATTACK_DETECTION_ENABLED = value

    @property
    def ATTACK_DETECTION_MODE(self) -> Literal["log", "block", "ban"]:
        return self.attack_detection.ATTACK_DETECTION_MODE

    @ATTACK_DETECTION_MODE.setter
    def ATTACK_DETECTION_MODE(self, value: Any) -> None:
        self.attack_detection.ATTACK_DETECTION_MODE = value

    @property
    def ATTACK_DETECTION_LOG_FULL_REQUEST(self) -> bool:
        return self.attack_detection.ATTACK_DETECTION_LOG_FULL_REQUEST

    @ATTACK_DETECTION_LOG_FULL_REQUEST.setter
    def ATTACK_DETECTION_LOG_FULL_REQUEST(self, value: bool) -> None:
        self.attack_detection.ATTACK_DETECTION_LOG_FULL_REQUEST = value

    @property
    def REDIS_QUEUE_ENABLED(self) -> bool:
        return self.redis_queue.REDIS_QUEUE_ENABLED

    @REDIS_QUEUE_ENABLED.setter
    def REDIS_QUEUE_ENABLED(self, value: bool) -> None:
        self.redis_queue.REDIS_QUEUE_ENABLED = value

    @property
    def REDIS_QUEUE_HOST(self) -> str:
        return self.redis_queue.REDIS_QUEUE_HOST

    @REDIS_QUEUE_HOST.setter
    def REDIS_QUEUE_HOST(self, value: str) -> None:
        self.redis_queue.REDIS_QUEUE_HOST = value

    @property
    def REDIS_QUEUE_PORT(self) -> int:
        return self.redis_queue.REDIS_QUEUE_PORT

    @REDIS_QUEUE_PORT.setter
    def REDIS_QUEUE_PORT(self, value: int) -> None:
        self.redis_queue.REDIS_QUEUE_PORT = value

    @property
    def REDIS_QUEUE_PASSWORD(self) -> SecretStr | None:
        return self.redis_queue.REDIS_QUEUE_PASSWORD

    @REDIS_QUEUE_PASSWORD.setter
    def REDIS_QUEUE_PASSWORD(self, value: SecretStr | None) -> None:
        self.redis_queue.REDIS_QUEUE_PASSWORD = value

    @property
    def REDIS_QUEUE_MAX_CONNECTIONS(self) -> int:
        return self.redis_queue.REDIS_QUEUE_MAX_CONNECTIONS

    @REDIS_QUEUE_MAX_CONNECTIONS.setter
    def REDIS_QUEUE_MAX_CONNECTIONS(self, value: int) -> None:
        self.redis_queue.REDIS_QUEUE_MAX_CONNECTIONS = value

    @property
    def REDIS_QUEUE_CONN_TIMEOUT(self) -> int:
        return self.redis_queue.REDIS_QUEUE_CONN_TIMEOUT

    @REDIS_QUEUE_CONN_TIMEOUT.setter
    def REDIS_QUEUE_CONN_TIMEOUT(self, value: int) -> None:
        self.redis_queue.REDIS_QUEUE_CONN_TIMEOUT = value

    @property
    def REDIS_QUEUE_CONN_RETRIES(self) -> int:
        return self.redis_queue.REDIS_QUEUE_CONN_RETRIES

    @REDIS_QUEUE_CONN_RETRIES.setter
    def REDIS_QUEUE_CONN_RETRIES(self, value: int) -> None:
        self.redis_queue.REDIS_QUEUE_CONN_RETRIES = value

    @property
    def DEFAULT_RATE_LIMIT_LIMIT(self) -> int:
        return self.rate_limit.DEFAULT_RATE_LIMIT_LIMIT

    @DEFAULT_RATE_LIMIT_LIMIT.setter
    def DEFAULT_RATE_LIMIT_LIMIT(self, value: int) -> None:
        self.rate_limit.DEFAULT_RATE_LIMIT_LIMIT = value

    @property
    def DEFAULT_RATE_LIMIT_PERIOD(self) -> int:
        return self.rate_limit.DEFAULT_RATE_LIMIT_PERIOD

    @DEFAULT_RATE_LIMIT_PERIOD.setter
    def DEFAULT_RATE_LIMIT_PERIOD(self, value: int) -> None:
        self.rate_limit.DEFAULT_RATE_LIMIT_PERIOD = value

    @property
    def RATE_LIMIT_FAIL_OPEN(self) -> bool:
        return self.rate_limit.RATE_LIMIT_FAIL_OPEN

    @RATE_LIMIT_FAIL_OPEN.setter
    def RATE_LIMIT_FAIL_OPEN(self, value: bool) -> None:
        self.rate_limit.RATE_LIMIT_FAIL_OPEN = value

    @property
    def RATE_LIMIT_ALGORITHM(self) -> str:
        return self.rate_limit.RATE_LIMIT_ALGORITHM

    @RATE_LIMIT_ALGORITHM.setter
    def RATE_LIMIT_ALGORITHM(self, value: str) -> None:
        self.rate_limit.RATE_LIMIT_ALGORITHM = value

    @property
    def CRUD_ADMIN_ENABLED(self) -> bool:
        return self.admin.CRUD_ADMIN_ENABLED

    @CRUD_ADMIN_ENABLED.setter
    def CRUD_ADMIN_ENABLED(self, value: bool) -> None:
        self.admin.CRUD_ADMIN_ENABLED = value

    @property
    def CRUD_ADMIN_MOUNT_PATH(self) -> str:
        return self.admin.CRUD_ADMIN_MOUNT_PATH

    @CRUD_ADMIN_MOUNT_PATH.setter
    def CRUD_ADMIN_MOUNT_PATH(self, value: str) -> None:
        self.admin.CRUD_ADMIN_MOUNT_PATH = value

    @property
    def CRUD_ADMIN_ALLOWED_IPS_LIST(self) -> list[str] | None:
        return self.admin.CRUD_ADMIN_ALLOWED_IPS_LIST

    @CRUD_ADMIN_ALLOWED_IPS_LIST.setter
    def CRUD_ADMIN_ALLOWED_IPS_LIST(self, value: list[str] | None) -> None:
        self.admin.CRUD_ADMIN_ALLOWED_IPS_LIST = value

    @property
    def CRUD_ADMIN_ALLOWED_NETWORKS_LIST(self) -> list[str] | None:
        return self.admin.CRUD_ADMIN_ALLOWED_NETWORKS_LIST

    @CRUD_ADMIN_ALLOWED_NETWORKS_LIST.setter
    def CRUD_ADMIN_ALLOWED_NETWORKS_LIST(self, value: list[str] | None) -> None:
        self.admin.CRUD_ADMIN_ALLOWED_NETWORKS_LIST = value

    @property
    def CRUD_ADMIN_MAX_SESSIONS(self) -> int:
        return self.admin.CRUD_ADMIN_MAX_SESSIONS

    @CRUD_ADMIN_MAX_SESSIONS.setter
    def CRUD_ADMIN_MAX_SESSIONS(self, value: int) -> None:
        self.admin.CRUD_ADMIN_MAX_SESSIONS = value

    @property
    def CRUD_ADMIN_SESSION_TIMEOUT(self) -> int:
        return self.admin.CRUD_ADMIN_SESSION_TIMEOUT

    @CRUD_ADMIN_SESSION_TIMEOUT.setter
    def CRUD_ADMIN_SESSION_TIMEOUT(self, value: int) -> None:
        self.admin.CRUD_ADMIN_SESSION_TIMEOUT = value

    @property
    def SESSION_SECURE_COOKIES(self) -> bool:
        return self.admin.SESSION_SECURE_COOKIES

    @SESSION_SECURE_COOKIES.setter
    def SESSION_SECURE_COOKIES(self, value: bool) -> None:
        self.admin.SESSION_SECURE_COOKIES = value

    @property
    def CRUD_ADMIN_TRACK_EVENTS(self) -> bool:
        return self.admin.CRUD_ADMIN_TRACK_EVENTS

    @CRUD_ADMIN_TRACK_EVENTS.setter
    def CRUD_ADMIN_TRACK_EVENTS(self, value: bool) -> None:
        self.admin.CRUD_ADMIN_TRACK_EVENTS = value

    @property
    def CRUD_ADMIN_TRACK_SESSIONS(self) -> bool:
        return self.admin.CRUD_ADMIN_TRACK_SESSIONS

    @CRUD_ADMIN_TRACK_SESSIONS.setter
    def CRUD_ADMIN_TRACK_SESSIONS(self, value: bool) -> None:
        self.admin.CRUD_ADMIN_TRACK_SESSIONS = value

    @property
    def CRUD_ADMIN_REDIS_ENABLED(self) -> bool:
        return self.admin.CRUD_ADMIN_REDIS_ENABLED

    @CRUD_ADMIN_REDIS_ENABLED.setter
    def CRUD_ADMIN_REDIS_ENABLED(self, value: bool) -> None:
        self.admin.CRUD_ADMIN_REDIS_ENABLED = value

    @property
    def CRUD_ADMIN_REDIS_HOST(self) -> str:
        return self.admin.CRUD_ADMIN_REDIS_HOST

    @CRUD_ADMIN_REDIS_HOST.setter
    def CRUD_ADMIN_REDIS_HOST(self, value: str) -> None:
        self.admin.CRUD_ADMIN_REDIS_HOST = value

    @property
    def CRUD_ADMIN_REDIS_PORT(self) -> int:
        return self.admin.CRUD_ADMIN_REDIS_PORT

    @CRUD_ADMIN_REDIS_PORT.setter
    def CRUD_ADMIN_REDIS_PORT(self, value: int) -> None:
        self.admin.CRUD_ADMIN_REDIS_PORT = value

    @property
    def CRUD_ADMIN_REDIS_DB(self) -> int:
        return self.admin.CRUD_ADMIN_REDIS_DB

    @CRUD_ADMIN_REDIS_DB.setter
    def CRUD_ADMIN_REDIS_DB(self, value: int) -> None:
        self.admin.CRUD_ADMIN_REDIS_DB = value

    @property
    def CRUD_ADMIN_REDIS_PASSWORD(self) -> SecretStr | None:
        return self.admin.CRUD_ADMIN_REDIS_PASSWORD

    @CRUD_ADMIN_REDIS_PASSWORD.setter
    def CRUD_ADMIN_REDIS_PASSWORD(self, value: SecretStr | None) -> None:
        self.admin.CRUD_ADMIN_REDIS_PASSWORD = value

    @property
    def CRUD_ADMIN_REDIS_SSL(self) -> bool:
        return self.admin.CRUD_ADMIN_REDIS_SSL

    @CRUD_ADMIN_REDIS_SSL.setter
    def CRUD_ADMIN_REDIS_SSL(self, value: bool) -> None:
        self.admin.CRUD_ADMIN_REDIS_SSL = value

    @property
    def EMAIL_FROM_NAME(self) -> str:
        return self.email.EMAIL_FROM_NAME

    @EMAIL_FROM_NAME.setter
    def EMAIL_FROM_NAME(self, value: str) -> None:
        self.email.EMAIL_FROM_NAME = value

    @property
    def EMAIL_FROM_ADDRESS(self) -> str:
        return self.email.EMAIL_FROM_ADDRESS

    @EMAIL_FROM_ADDRESS.setter
    def EMAIL_FROM_ADDRESS(self, value: str) -> None:
        self.email.EMAIL_FROM_ADDRESS = value

    @property
    def EMAIL_REPLY_TO(self) -> str | None:
        return self.email.EMAIL_REPLY_TO

    @EMAIL_REPLY_TO.setter
    def EMAIL_REPLY_TO(self, value: str | None) -> None:
        self.email.EMAIL_REPLY_TO = value

    @property
    def SMTP_HOST(self) -> str:
        return self.email.SMTP_HOST

    @SMTP_HOST.setter
    def SMTP_HOST(self, value: str) -> None:
        self.email.SMTP_HOST = value

    @property
    def SMTP_PORT(self) -> int:
        return self.email.SMTP_PORT

    @SMTP_PORT.setter
    def SMTP_PORT(self, value: int) -> None:
        self.email.SMTP_PORT = value

    @property
    def SMTP_USER(self) -> str | None:
        return self.email.SMTP_USER

    @SMTP_USER.setter
    def SMTP_USER(self, value: str | None) -> None:
        self.email.SMTP_USER = value

    @property
    def SMTP_PASSWORD(self) -> SecretStr | None:
        return self.email.SMTP_PASSWORD

    @SMTP_PASSWORD.setter
    def SMTP_PASSWORD(self, value: SecretStr | None) -> None:
        self.email.SMTP_PASSWORD = value

    @property
    def SMTP_USE_TLS(self) -> bool:
        return self.email.SMTP_USE_TLS

    @SMTP_USE_TLS.setter
    def SMTP_USE_TLS(self, value: bool) -> None:
        self.email.SMTP_USE_TLS = value

    @property
    def EMAIL_TEMPLATES_DIR(self) -> str:
        return self.email.EMAIL_TEMPLATES_DIR

    @EMAIL_TEMPLATES_DIR.setter
    def EMAIL_TEMPLATES_DIR(self, value: str) -> None:
        self.email.EMAIL_TEMPLATES_DIR = value

    @property
    def WEBHOOK_MAX_RETRIES(self) -> int:
        return self.webhooks.WEBHOOK_MAX_RETRIES

    @WEBHOOK_MAX_RETRIES.setter
    def WEBHOOK_MAX_RETRIES(self, value: int) -> None:
        self.webhooks.WEBHOOK_MAX_RETRIES = value

    @property
    def WEBHOOK_RETRY_DELAY_SECONDS(self) -> int:
        return self.webhooks.WEBHOOK_RETRY_DELAY_SECONDS

    @WEBHOOK_RETRY_DELAY_SECONDS.setter
    def WEBHOOK_RETRY_DELAY_SECONDS(self, value: int) -> None:
        self.webhooks.WEBHOOK_RETRY_DELAY_SECONDS = value

    @property
    def WEBHOOK_TIMEOUT_SECONDS(self) -> int:
        return self.webhooks.WEBHOOK_TIMEOUT_SECONDS

    @WEBHOOK_TIMEOUT_SECONDS.setter
    def WEBHOOK_TIMEOUT_SECONDS(self, value: int) -> None:
        self.webhooks.WEBHOOK_TIMEOUT_SECONDS = value

    @property
    def WEBHOOK_SIGNATURE_HEADER(self) -> str:
        return self.webhooks.WEBHOOK_SIGNATURE_HEADER

    @WEBHOOK_SIGNATURE_HEADER.setter
    def WEBHOOK_SIGNATURE_HEADER(self, value: str) -> None:
        self.webhooks.WEBHOOK_SIGNATURE_HEADER = value

    @property
    def WEBHOOK_SIGNATURE_ALGORITHM(self) -> str:
        return self.webhooks.WEBHOOK_SIGNATURE_ALGORITHM

    @WEBHOOK_SIGNATURE_ALGORITHM.setter
    def WEBHOOK_SIGNATURE_ALGORITHM(self, value: str) -> None:
        self.webhooks.WEBHOOK_SIGNATURE_ALGORITHM = value

    @property
    def ENVIRONMENT(self) -> EnvironmentOption:
        return self.env.ENVIRONMENT

    @ENVIRONMENT.setter
    def ENVIRONMENT(self, value: EnvironmentOption) -> None:
        self.env.ENVIRONMENT = value

    @property
    def STORAGE_PROVIDER(self) -> str:
        return self.storage.STORAGE_PROVIDER

    @STORAGE_PROVIDER.setter
    def STORAGE_PROVIDER(self, value: str) -> None:
        self.storage.STORAGE_PROVIDER = value

    @property
    def STORAGE_BUCKET(self) -> str:
        return self.storage.STORAGE_BUCKET

    @STORAGE_BUCKET.setter
    def STORAGE_BUCKET(self, value: str) -> None:
        self.storage.STORAGE_BUCKET = value

    @property
    def STORAGE_REGION(self) -> str:
        return self.storage.STORAGE_REGION

    @STORAGE_REGION.setter
    def STORAGE_REGION(self, value: str) -> None:
        self.storage.STORAGE_REGION = value

    @property
    def AWS_ACCESS_KEY_ID(self) -> str | None:
        return self.storage.AWS_ACCESS_KEY_ID

    @AWS_ACCESS_KEY_ID.setter
    def AWS_ACCESS_KEY_ID(self, value: str | None) -> None:
        self.storage.AWS_ACCESS_KEY_ID = value

    @property
    def AWS_SECRET_ACCESS_KEY(self) -> SecretStr | None:
        return self.storage.AWS_SECRET_ACCESS_KEY

    @AWS_SECRET_ACCESS_KEY.setter
    def AWS_SECRET_ACCESS_KEY(self, value: Any) -> None:
        self.storage.AWS_SECRET_ACCESS_KEY = value

    @property
    def AWS_ENDPOINT_URL(self) -> str | None:
        return self.storage.AWS_ENDPOINT_URL

    @AWS_ENDPOINT_URL.setter
    def AWS_ENDPOINT_URL(self, value: str | None) -> None:
        self.storage.AWS_ENDPOINT_URL = value

    @property
    def STRIPE_SECRET_KEY(self) -> SecretStr | None:
        return self.billing.STRIPE_SECRET_KEY

    @STRIPE_SECRET_KEY.setter
    def STRIPE_SECRET_KEY(self, value: SecretStr | None) -> None:
        self.billing.STRIPE_SECRET_KEY = value

    @property
    def STRIPE_PUBLISHABLE_KEY(self) -> str | None:
        return self.billing.STRIPE_PUBLISHABLE_KEY

    @STRIPE_PUBLISHABLE_KEY.setter
    def STRIPE_PUBLISHABLE_KEY(self, value: str | None) -> None:
        self.billing.STRIPE_PUBLISHABLE_KEY = value

    @property
    def STRIPE_WEBHOOK_SECRET(self) -> SecretStr | None:
        return self.billing.STRIPE_WEBHOOK_SECRET

    @STRIPE_WEBHOOK_SECRET.setter
    def STRIPE_WEBHOOK_SECRET(self, value: SecretStr | None) -> None:
        self.billing.STRIPE_WEBHOOK_SECRET = value

    @property
    def OLLAMA_BASE_URL(self) -> str:
        return self.ai.OLLAMA_BASE_URL

    @OLLAMA_BASE_URL.setter
    def OLLAMA_BASE_URL(self, value: str) -> None:
        self.ai.OLLAMA_BASE_URL = value

    @property
    def OLLAMA_TIMEOUT(self) -> float:
        return self.ai.OLLAMA_TIMEOUT

    @OLLAMA_TIMEOUT.setter
    def OLLAMA_TIMEOUT(self, value: float) -> None:
        self.ai.OLLAMA_TIMEOUT = value

    @property
    def OLLAMA_DEFAULT_MODEL(self) -> str:
        return self.ai.OLLAMA_DEFAULT_MODEL

    @OLLAMA_DEFAULT_MODEL.setter
    def OLLAMA_DEFAULT_MODEL(self, value: str) -> None:
        self.ai.OLLAMA_DEFAULT_MODEL = value

    @property
    def OLLAMA_MAX_CONNECTIONS(self) -> int:
        return self.ai.OLLAMA_MAX_CONNECTIONS

    @OLLAMA_MAX_CONNECTIONS.setter
    def OLLAMA_MAX_CONNECTIONS(self, value: int) -> None:
        self.ai.OLLAMA_MAX_CONNECTIONS = value

    @property
    def OLLAMA_MAX_KEEPALIVE(self) -> int:
        return self.ai.OLLAMA_MAX_KEEPALIVE

    @OLLAMA_MAX_KEEPALIVE.setter
    def OLLAMA_MAX_KEEPALIVE(self, value: int) -> None:
        self.ai.OLLAMA_MAX_KEEPALIVE = value

    @property
    def OLLAMA_KEEPALIVE_EXPIRY(self) -> float:
        return self.ai.OLLAMA_KEEPALIVE_EXPIRY

    @OLLAMA_KEEPALIVE_EXPIRY.setter
    def OLLAMA_KEEPALIVE_EXPIRY(self, value: float) -> None:
        self.ai.OLLAMA_KEEPALIVE_EXPIRY = value

    @property
    def EVENT_HANDLER_TIMEOUT_SECONDS(self) -> float:
        return self.events.EVENT_HANDLER_TIMEOUT_SECONDS

    @EVENT_HANDLER_TIMEOUT_SECONDS.setter
    def EVENT_HANDLER_TIMEOUT_SECONDS(self, value: float) -> None:
        self.events.EVENT_HANDLER_TIMEOUT_SECONDS = value

    @property
    def EVENT_HANDLER_TIMEOUT_CRITICAL(self) -> float:
        return self.events.EVENT_HANDLER_TIMEOUT_CRITICAL

    @EVENT_HANDLER_TIMEOUT_CRITICAL.setter
    def EVENT_HANDLER_TIMEOUT_CRITICAL(self, value: float) -> None:
        self.events.EVENT_HANDLER_TIMEOUT_CRITICAL = value

    @property
    def EVENT_BUS_CONCURRENCY(self) -> int:
        return self.events.EVENT_BUS_CONCURRENCY

    @EVENT_BUS_CONCURRENCY.setter
    def EVENT_BUS_CONCURRENCY(self, value: int) -> None:
        self.events.EVENT_BUS_CONCURRENCY = value

    @property
    def CORS_ORIGINS(self) -> list[str]:
        return self.cors.CORS_ORIGINS

    @CORS_ORIGINS.setter
    def CORS_ORIGINS(self, value: list[str]) -> None:
        self.cors.CORS_ORIGINS = value

    @property
    def CORS_METHODS(self) -> list[str]:
        return self.cors.CORS_METHODS

    @CORS_METHODS.setter
    def CORS_METHODS(self, value: list[str]) -> None:
        self.cors.CORS_METHODS = value

    @property
    def CORS_HEADERS(self) -> list[str]:
        return self.cors.CORS_HEADERS

    @CORS_HEADERS.setter
    def CORS_HEADERS(self, value: list[str]) -> None:
        self.cors.CORS_HEADERS = value

    @property
    def ARQ_ENABLED(self) -> bool:
        return self.arq.ARQ_ENABLED

    @ARQ_ENABLED.setter
    def ARQ_ENABLED(self, value: bool) -> None:
        self.arq.ARQ_ENABLED = value

    @property
    def ARQ_WORKERS(self) -> int:
        return self.arq.ARQ_WORKERS

    @ARQ_WORKERS.setter
    def ARQ_WORKERS(self, value: int) -> None:
        self.arq.ARQ_WORKERS = value

    @property
    def ARQ_MAX_JOBS(self) -> int:
        return self.arq.ARQ_MAX_JOBS

    @ARQ_MAX_JOBS.setter
    def ARQ_MAX_JOBS(self, value: int) -> None:
        self.arq.ARQ_MAX_JOBS = value

    @property
    def ARQ_JOB_TIMEOUT(self) -> int:
        return self.arq.ARQ_JOB_TIMEOUT

    @ARQ_JOB_TIMEOUT.setter
    def ARQ_JOB_TIMEOUT(self, value: int) -> None:
        self.arq.ARQ_JOB_TIMEOUT = value

    @property
    def ARQ_KEEP_RESULT(self) -> int:
        return self.arq.ARQ_KEEP_RESULT

    @ARQ_KEEP_RESULT.setter
    def ARQ_KEEP_RESULT(self, value: int) -> None:
        self.arq.ARQ_KEEP_RESULT = value

    @property
    def ARQ_MAX_TRIES(self) -> int:
        return self.arq.ARQ_MAX_TRIES

    @ARQ_MAX_TRIES.setter
    def ARQ_MAX_TRIES(self, value: int) -> None:
        self.arq.ARQ_MAX_TRIES = value

    @property
    def ARQ_QUEUE_MAX_SIZE(self) -> int:
        return self.arq.ARQ_QUEUE_MAX_SIZE

    @ARQ_QUEUE_MAX_SIZE.setter
    def ARQ_QUEUE_MAX_SIZE(self, value: int) -> None:
        self.arq.ARQ_QUEUE_MAX_SIZE = value

    @property
    def EVENT_BUS_USE_LUA_IDEMPOTENCY(self) -> bool:
        return self.r1_unified.EVENT_BUS_USE_LUA_IDEMPOTENCY

    @EVENT_BUS_USE_LUA_IDEMPOTENCY.setter
    def EVENT_BUS_USE_LUA_IDEMPOTENCY(self, value: bool) -> None:
        self.r1_unified.EVENT_BUS_USE_LUA_IDEMPOTENCY = value

    @property
    def EVENT_BUS_USE_BATCH_ENQUEUE(self) -> bool:
        return self.r1_unified.EVENT_BUS_USE_BATCH_ENQUEUE

    @EVENT_BUS_USE_BATCH_ENQUEUE.setter
    def EVENT_BUS_USE_BATCH_ENQUEUE(self, value: bool) -> None:
        self.r1_unified.EVENT_BUS_USE_BATCH_ENQUEUE = value

    @property
    def SESSION_USE_PIPELINE_EVICTION(self) -> bool:
        return self.r1_unified.SESSION_USE_PIPELINE_EVICTION

    @SESSION_USE_PIPELINE_EVICTION.setter
    def SESSION_USE_PIPELINE_EVICTION(self, value: bool) -> None:
        self.r1_unified.SESSION_USE_PIPELINE_EVICTION = value

    @property
    def XFETCH_USE_LUA_SCRIPT(self) -> bool:
        return self.r1_unified.XFETCH_USE_LUA_SCRIPT

    @XFETCH_USE_LUA_SCRIPT.setter
    def XFETCH_USE_LUA_SCRIPT(self, value: bool) -> None:
        self.r1_unified.XFETCH_USE_LUA_SCRIPT = value

    @property
    def RBAC_USE_REDIS_CACHE(self) -> bool:
        return self.r1_unified.RBAC_USE_REDIS_CACHE

    @RBAC_USE_REDIS_CACHE.setter
    def RBAC_USE_REDIS_CACHE(self, value: bool) -> None:
        self.r1_unified.RBAC_USE_REDIS_CACHE = value

    @property
    def TOKEN_NEGATIVE_CACHE_TTL(self) -> int:
        return self.r1_unified.TOKEN_NEGATIVE_CACHE_TTL

    @TOKEN_NEGATIVE_CACHE_TTL.setter
    def TOKEN_NEGATIVE_CACHE_TTL(self, value: int) -> None:
        self.r1_unified.TOKEN_NEGATIVE_CACHE_TTL = value

    @property
    def RBAC_CACHE_TTL_SECONDS(self) -> int:
        return self.r1_unified.RBAC_CACHE_TTL_SECONDS

    @RBAC_CACHE_TTL_SECONDS.setter
    def RBAC_CACHE_TTL_SECONDS(self, value: int) -> None:
        self.r1_unified.RBAC_CACHE_TTL_SECONDS = value

    @property
    def CIRCUIT_BREAKER_USE_DOUBLE_CHECK(self) -> bool:
        return self.r1_unified.CIRCUIT_BREAKER_USE_DOUBLE_CHECK

    @CIRCUIT_BREAKER_USE_DOUBLE_CHECK.setter
    def CIRCUIT_BREAKER_USE_DOUBLE_CHECK(self, value: bool) -> None:
        self.r1_unified.CIRCUIT_BREAKER_USE_DOUBLE_CHECK = value

    @property
    def DLQ_USE_BOUNDED_SIZE(self) -> bool:
        return self.r1_unified.DLQ_USE_BOUNDED_SIZE

    @DLQ_USE_BOUNDED_SIZE.setter
    def DLQ_USE_BOUNDED_SIZE(self, value: bool) -> None:
        self.r1_unified.DLQ_USE_BOUNDED_SIZE = value

    @property
    def DLQ_MAX_SIZE(self) -> int:
        return self.r1_unified.DLQ_MAX_SIZE

    @DLQ_MAX_SIZE.setter
    def DLQ_MAX_SIZE(self, value: int) -> None:
        self.r1_unified.DLQ_MAX_SIZE = value

    @property
    def DLQ_ALERT_THRESHOLD_PCT(self) -> float:
        return self.r1_unified.DLQ_ALERT_THRESHOLD_PCT

    @DLQ_ALERT_THRESHOLD_PCT.setter
    def DLQ_ALERT_THRESHOLD_PCT(self, value: float) -> None:
        self.r1_unified.DLQ_ALERT_THRESHOLD_PCT = value

    @property
    def SESSION_MAX_EVICTION_BATCH_SIZE(self) -> int:
        return self.r1_unified.SESSION_MAX_EVICTION_BATCH_SIZE

    @SESSION_MAX_EVICTION_BATCH_SIZE.setter
    def SESSION_MAX_EVICTION_BATCH_SIZE(self, value: int) -> None:
        self.r1_unified.SESSION_MAX_EVICTION_BATCH_SIZE = value

    @property
    def PASSKEY_DECRYPT_CONCURRENCY_LIMIT(self) -> int:
        return self.r1_unified.PASSKEY_DECRYPT_CONCURRENCY_LIMIT

    @PASSKEY_DECRYPT_CONCURRENCY_LIMIT.setter
    def PASSKEY_DECRYPT_CONCURRENCY_LIMIT(self, value: int) -> None:
        self.r1_unified.PASSKEY_DECRYPT_CONCURRENCY_LIMIT = value

    @property
    def PASSKEY_USE_COLUMN_SELECT(self) -> bool:
        return self.r1_unified.PASSKEY_USE_COLUMN_SELECT

    @PASSKEY_USE_COLUMN_SELECT.setter
    def PASSKEY_USE_COLUMN_SELECT(self, value: bool) -> None:
        self.r1_unified.PASSKEY_USE_COLUMN_SELECT = value

    @property
    def PASSKEY_MAX_CREDENTIALS_PER_USER(self) -> int:
        return self.r1_unified.PASSKEY_MAX_CREDENTIALS_PER_USER

    @PASSKEY_MAX_CREDENTIALS_PER_USER.setter
    def PASSKEY_MAX_CREDENTIALS_PER_USER(self, value: int) -> None:
        self.r1_unified.PASSKEY_MAX_CREDENTIALS_PER_USER = value

    @property
    def FEATURE_CONTEXTVAR_OPTIMIZATION(self) -> bool:
        return self.performance.FEATURE_CONTEXTVAR_OPTIMIZATION

    @FEATURE_CONTEXTVAR_OPTIMIZATION.setter
    def FEATURE_CONTEXTVAR_OPTIMIZATION(self, value: bool) -> None:
        self.performance.FEATURE_CONTEXTVAR_OPTIMIZATION = value

    @property
    def FEATURE_STREAMING_UPLOADS(self) -> bool:
        return self.performance.FEATURE_STREAMING_UPLOADS

    @FEATURE_STREAMING_UPLOADS.setter
    def FEATURE_STREAMING_UPLOADS(self, value: bool) -> None:
        self.performance.FEATURE_STREAMING_UPLOADS = value

    @property
    def FEATURE_EAGER_LOADING(self) -> bool:
        return self.performance.FEATURE_EAGER_LOADING

    @FEATURE_EAGER_LOADING.setter
    def FEATURE_EAGER_LOADING(self, value: bool) -> None:
        self.performance.FEATURE_EAGER_LOADING = value

    @property
    def FEATURE_REMOVE_WASTED_CONNECTION(self) -> bool:
        return self.performance.FEATURE_REMOVE_WASTED_CONNECTION

    @FEATURE_REMOVE_WASTED_CONNECTION.setter
    def FEATURE_REMOVE_WASTED_CONNECTION(self, value: bool) -> None:
        self.performance.FEATURE_REMOVE_WASTED_CONNECTION = value

    @property
    def QUERY_PROFILER_ENABLED(self) -> bool:
        return self.query_profiler.QUERY_PROFILER_ENABLED

    @QUERY_PROFILER_ENABLED.setter
    def QUERY_PROFILER_ENABLED(self, value: bool) -> None:
        self.query_profiler.QUERY_PROFILER_ENABLED = value

    @property
    def QUERY_PROFILER_SLOW_THRESHOLD_MS(self) -> int:
        return self.query_profiler.QUERY_PROFILER_SLOW_THRESHOLD_MS

    @QUERY_PROFILER_SLOW_THRESHOLD_MS.setter
    def QUERY_PROFILER_SLOW_THRESHOLD_MS(self, value: int) -> None:
        self.query_profiler.QUERY_PROFILER_SLOW_THRESHOLD_MS = value

    @property
    def QUERY_PROFILER_N_PLUS_1_THRESHOLD(self) -> int:
        return self.query_profiler.QUERY_PROFILER_N_PLUS_1_THRESHOLD

    @QUERY_PROFILER_N_PLUS_1_THRESHOLD.setter
    def QUERY_PROFILER_N_PLUS_1_THRESHOLD(self, value: int) -> None:
        self.query_profiler.QUERY_PROFILER_N_PLUS_1_THRESHOLD = value

    @property
    def QUERY_PROFILER_DETAILED_LOGS(self) -> bool:
        return self.query_profiler.QUERY_PROFILER_DETAILED_LOGS

    @QUERY_PROFILER_DETAILED_LOGS.setter
    def QUERY_PROFILER_DETAILED_LOGS(self, value: bool) -> None:
        self.query_profiler.QUERY_PROFILER_DETAILED_LOGS = value


# Initialize settings without arguments - Pydantic will populate from env
settings = Settings()  # type: ignore[call-arg]
