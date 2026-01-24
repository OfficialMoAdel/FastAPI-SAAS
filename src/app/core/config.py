import os
from enum import Enum

from pydantic import Field, SecretStr, computed_field, field_validator, model_validator
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


class CryptSettings(BaseSettings):
    SECRET_KEY: SecretStr  # REQUIRED: Must be set via environment variable
    ALGORITHM: str = "HS256"  # Options: HS256 (symmetric), RS256 (asymmetric)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # Bcrypt work factor: 8=30ms(dev), 10=100ms, 12=250ms(default), 14=1000ms
    BCRYPT_ROUNDS: int = Field(default=12, ge=8, le=14)
    # Key for Fernet encryption. If None, webhooks encryption might fail or use a dev key.
    ENCRYPTION_KEY: SecretStr | None = None
    # SECURITY: Set unique salt per deployment for key derivation
    ENCRYPTION_SALT: SecretStr | None = None
    # RS256 asymmetric keys (optional, required if ALGORITHM=RS256)
    JWT_PRIVATE_KEY: SecretStr | None = None  # PEM-encoded private key
    JWT_PUBLIC_KEY: str | None = None  # PEM-encoded public key

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


class DatabaseSettings(BaseSettings):
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = Field(default=30, ge=5, le=120)


class SQLiteSettings(DatabaseSettings):
    SQLITE_URI: str = "./sql_app.db"
    SQLITE_SYNC_PREFIX: str = "sqlite:///"
    SQLITE_ASYNC_PREFIX: str = "sqlite+aiosqlite:///"


class MySQLSettings(DatabaseSettings):
    MYSQL_USER: str = "username"
    MYSQL_PASSWORD: str = ""  # REQUIRED: No default - must be set via env
    MYSQL_SERVER: str = "localhost"
    MYSQL_PORT: int = 3306  # FIX: Correct MySQL port (was 5432 which is PostgreSQL)
    MYSQL_DB: str = "dbname"
    MYSQL_SYNC_PREFIX: str = "mysql://"
    MYSQL_ASYNC_PREFIX: str = "mysql+aiomysql://"
    MYSQL_URL: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def MYSQL_URI(self) -> str:
        credentials = f"{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
        location = f"{self.MYSQL_SERVER}:{self.MYSQL_PORT}/{self.MYSQL_DB}"
        return f"{credentials}@{location}"


class PostgresSettings(DatabaseSettings):
    POSTGRES_USER: str  # REQUIRED: No defaults
    POSTGRES_PASSWORD: str  # REQUIRED: No defaults
    POSTGRES_SERVER: str = "localhost"  # Override via POSTGRES_SERVER in .env
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "postgres"
    POSTGRES_SYNC_PREFIX: str = "postgresql://"
    POSTGRES_ASYNC_PREFIX: str = "postgresql+asyncpg://"
    POSTGRES_URL: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def POSTGRES_URI(self) -> str:
        credentials = f"{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
        location = f"{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        return f"{credentials}@{location}"


class FirstUserSettings(BaseSettings):
    ADMIN_NAME: str = "admin"
    ADMIN_EMAIL: str = "admin@admin.com"
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str  # REQUIRED: Must be set via environment variable


class AuthSettings(BaseSettings):
    """Authentication security settings."""

    # Account lockout configuration
    AUTH_MAX_LOGIN_ATTEMPTS: int = 5
    AUTH_LOCKOUT_DURATION_SECONDS: int = 900  # 15 minutes


class TestSettings(BaseSettings): ...


class RedisCacheSettings(BaseSettings):
    REDIS_CACHE_ENABLED: bool = True
    REDIS_CACHE_HOST: str = "localhost"  # Override via REDIS_CACHE_HOST in .env
    REDIS_CACHE_PORT: int = 6379
    REDIS_CACHE_PASSWORD: str | None = None
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


class RedisQueueSettings(BaseSettings):
    REDIS_QUEUE_ENABLED: bool = True
    REDIS_QUEUE_HOST: str = "localhost"  # Override via REDIS_QUEUE_HOST in .env
    REDIS_QUEUE_PORT: int = 6379
    REDIS_QUEUE_PASSWORD: str | None = None
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
    CRUD_ADMIN_REDIS_PASSWORD: str | None = None
    CRUD_ADMIN_REDIS_SSL: bool = False


class EmailSettings(BaseSettings):
    EMAIL_FROM_NAME: str = "FastAPI SaaS"
    EMAIL_FROM_ADDRESS: str = "noreply@fastapi.local"
    EMAIL_REPLY_TO: str | None = None
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
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
    AWS_SECRET_ACCESS_KEY: str | None = None
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
        import os

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

    STRIPE_SECRET_KEY: str | None = None
    STRIPE_PUBLISHABLE_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None


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

    SESSION_USE_PIPELINE_EVICTION: bool = True
    """Use Redis pipeline for session eviction (P2-1)."""

    XFETCH_USE_LUA_SCRIPT: bool = True
    """Use Lua script for atomic GET+TTL in XFetch (P2-2)."""

    RBAC_USE_REDIS_CACHE: bool = True
    """Use Redis caching for RBAC permission checks (P5)."""

    DLQ_USE_BOUNDED_SIZE: bool = True
    """Use bounded DLQ with automatic trimming (P3-1)."""

    PASSKEY_USE_COLUMN_SELECT: bool = True
    """Use column-specific SELECT for passkey queries (P3-2)."""

    CIRCUIT_BREAKER_USE_DOUBLE_CHECK: bool = True
    """Use double-check pattern for circuit breaker (P4-1)."""

    # Configuration parameters with validation
    SESSION_MAX_EVICTION_BATCH_SIZE: int = 50
    """Maximum sessions to evict in a single batch operation."""

    RBAC_CACHE_TTL_SECONDS: int = 3600
    """TTL for RBAC permission cache entries (1 hour default)."""

    DLQ_MAX_SIZE: int = 10000
    """Maximum entries in the Dead-Letter Queue."""

    DLQ_ALERT_THRESHOLD_PCT: int = 80
    """Alert when DLQ reaches this percentage of max size."""

    PASSKEY_MAX_CREDENTIALS_PER_USER: int = 100
    """Maximum passkey credentials per user."""

    PASSKEY_DECRYPT_CONCURRENCY_LIMIT: int = 10
    """Maximum concurrent passkey decrypt operations."""

    IDEMPOTENCY_TTL_SECONDS: int = 300
    """TTL for event idempotency keys (5 minutes default)."""

    TOKEN_NEGATIVE_CACHE_TTL: int = 300
    """Negative cache TTL for token blacklist checks (5 minutes default).
    
    Trade-off:
    - Higher TTL = fewer Redis calls but delayed revocation detection
    - Lower TTL = faster revocation detection but more Redis calls
    
    Risk: Revoked token accepted for up to TTL duration.
    For immediate revocation requirements, set to 60 or lower.
    """

    @field_validator("SESSION_MAX_EVICTION_BATCH_SIZE")
    @classmethod
    def validate_eviction_batch(cls, v: int) -> int:
        if not 1 <= v <= 100:
            raise ValueError("SESSION_MAX_EVICTION_BATCH_SIZE must be 1-100")
        return v

    @field_validator("RBAC_CACHE_TTL_SECONDS")
    @classmethod
    def validate_rbac_ttl(cls, v: int) -> int:
        if not 60 <= v <= 86400:
            raise ValueError("RBAC_CACHE_TTL_SECONDS must be 60-86400 (1min-24hr)")
        return v

    @field_validator("DLQ_MAX_SIZE")
    @classmethod
    def validate_dlq_size(cls, v: int) -> int:
        if not 1000 <= v <= 100000:
            raise ValueError("DLQ_MAX_SIZE must be 1000-100000")
        return v

    @field_validator("DLQ_ALERT_THRESHOLD_PCT")
    @classmethod
    def validate_alert_threshold(cls, v: int) -> int:
        if not 50 <= v <= 99:
            raise ValueError("DLQ_ALERT_THRESHOLD_PCT must be 50-99")
        return v

    @field_validator("PASSKEY_DECRYPT_CONCURRENCY_LIMIT")
    @classmethod
    def validate_decrypt_limit(cls, v: int) -> int:
        if not 1 <= v <= 50:
            raise ValueError("PASSKEY_DECRYPT_CONCURRENCY_LIMIT must be 1-50")
        return v


class Settings(
    AppSettings,
    SQLiteSettings,
    PostgresSettings,
    CryptSettings,
    FirstUserSettings,
    AuthSettings,
    TestSettings,
    RedisCacheSettings,
    ClientSideCacheSettings,
    SecuritySettings,
    RedisQueueSettings,
    DefaultRateLimitSettings,
    CRUDAdminSettings,
    EmailSettings,
    WebhookSettings,
    EnvironmentSettings,
    StorageSettings,
    BillingSettings,
    AISettings,
    EventSettings,
    CORSSettings,
):
    # R1-UNIFIED optimization settings (nested for organization)
    r1_unified: R1UnifiedSettings = R1UnifiedSettings()

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()  # type: ignore
