import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, ValidationInfo, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    APP_NAME: str = "FastAPI app"
    APP_DESCRIPTION: str | None = None
    APP_VERSION: str | None = None
    LICENSE_NAME: str | None = None
    CONTACT_NAME: str | None = None
    CONTACT_EMAIL: str | None = None
    FRONTEND_URL: str = "http://localhost:3000"  # Frontend URL for magic links, etc.
    CACHE_WARMING_ENABLED: bool = True
    CACHE_WARMING_DELAY_SECONDS: int = 2
    # DATABASE: Control table creation (set to False in production)
    CREATE_TABLES_ON_STARTUP: bool = False

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

    WEBHOOK_ALLOW_INTERNAL_URLS: bool = Field(
        default=False, description="Allow webhooks to target internal IPs (RFC1918 etc)"
    )


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
    # Old key for key rotation, stored securely
    ENCRYPTION_KEY_OLD: SecretStr | None = None
    # RS256 asymmetric keys (optional, required if ALGORITHM=RS256)
    JWT_PRIVATE_KEY: SecretStr | None = None  # PEM-encoded private key
    JWT_PUBLIC_KEY: str | None = None  # PEM-encoded public key
    # SECURITY FIX: Made JWT audience/issuer configurable instead of hardcoded
    JWT_AUDIENCE: str = Field(default="fastapi-saas-api", description="JWT token audience claim")
    JWT_ISSUER: str = Field(default="fastapi-saas", description="JWT token issuer claim")

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
        """Validate encryption keys are set to prevent runtime failures.

        S-1 FIX: ENVIRONMENT may be supplied via ``.env`` (loaded by
        pydantic-settings), in which case it never reaches ``os.environ`` —
        the old lookup silently defaulted to "local" and skipped this check,
        letting production boot with ENCRYPTION_KEY=None. Consult the process
        environment first, then the dotenv file(s) Settings loads.
        """
        import os
        from pathlib import Path

        from dotenv import dotenv_values

        env_value: str | None = os.environ.get("ENVIRONMENT")
        if env_value is None:
            # M-4: use python-dotenv's parser instead of hand-rolled line
            # splitting (handles quoting/escaping consistently with the loader).
            src_dir_env = Path(__file__).resolve().parents[1] / ".env"
            repo_env = Path(".env")
            for candidate in (src_dir_env, repo_env):
                if candidate.is_file():
                    env_value = dotenv_values(candidate).get("ENVIRONMENT")
                    if env_value is not None:
                        break

        env = (env_value or "local").lower()
        if env in ("production", "staging"):
            # ENCRYPTION_KEY and ENCRYPTION_SALT should be validated in production/staging
            if not self.ENCRYPTION_KEY or not self.ENCRYPTION_SALT:
                raise ValueError(
                    "SECURITY: ENCRYPTION_KEY and ENCRYPTION_SALT must be set in the production/staging environment."
                )

        return self


class DatabaseConfigMixin(BaseModel):
    DB_WORKERS: int = Field(default=1, ge=1, le=32, description="Number of uvicorn workers")
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0, le=50, description="Max overflow connections")
    DB_POOL_TIMEOUT: int = Field(default=30, ge=5, le=120)
    POSTGRES_MAX_CONNECTIONS: int = Field(default=100, ge=20, le=500, description="PostgreSQL max_connections limit")
    DB_STATEMENT_CACHE_SIZE: int = Field(default=100, ge=0, description="Size of asyncpg statement cache")
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
    # K10 FIX: arq fails every job when max_tries=0 (job_try starts at 1 > 0),
    # so 0 is not a legal value.
    ARQ_MAX_TRIES: int = Field(default=3, ge=1, le=10, description="Default max job tries (>=1)")
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


class SQLiteSettings(DatabaseConfigMixin, BaseSettings):
    SQLITE_URI: str = "./sql_app.db"
    SQLITE_SYNC_PREFIX: str = "sqlite:///"
    SQLITE_ASYNC_PREFIX: str = "sqlite+aiosqlite:///"
    USE_SQLITE: bool = True


class MySQLSettings(DatabaseConfigMixin, BaseSettings):
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
        if password:
            import urllib.parse

            password = urllib.parse.quote_plus(password)
        credentials = f"{self.MYSQL_USER}:{password}"
        location = f"{self.MYSQL_SERVER}:{self.MYSQL_PORT}/{self.MYSQL_DB}"
        return f"{credentials}@{location}"


class PostgresSettings(DatabaseConfigMixin, BaseSettings):
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
        if password:
            import urllib.parse

            password = urllib.parse.quote_plus(password)
        credentials = f"{self.POSTGRES_USER}:{password}"
        location = f"{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        return f"{credentials}@{location}"


class FirstUserSettings(BaseSettings):
    ADMIN_NAME: str = "admin"
    ADMIN_EMAIL: str = "admin@admin.com"
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: SecretStr | None = Field(..., min_length=16)

    @field_validator("ADMIN_PASSWORD", mode="after")
    @classmethod
    def validate_password_complexity(cls, v: SecretStr | None) -> SecretStr | None:
        if v is None:
            return v
        password = v.get_secret_value()
        if not re.search(r"[a-z]", password):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"[A-Z]", password):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"\d", password):
            raise ValueError("Password must contain at least one digit")
        if not re.search(r"[@$!%*?&]", password):
            raise ValueError("Password must contain at least one special character (@$!%*?&)")
        return v


class AuthSettings(BaseSettings):
    """Authentication security settings."""

    # Account lockout configuration
    AUTH_MAX_LOGIN_ATTEMPTS: int = Field(default=5, ge=1, le=50)
    AUTH_LOCKOUT_DURATION_SECONDS: int = Field(default=900, ge=60, le=86400)  # 15 minutes
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
    AUTH_MAX_SESSIONS: int = Field(default=5, ge=1, le=50, description="Max concurrent sessions per user")
    AUTH_SESSION_TTL_SECONDS: int = Field(
        default=86400 * 7, ge=3600, le=86400 * 30, description="Session TTL in seconds"
    )
    # SECURITY FIX (M-1): When True, MFA completion is rejected if client IP
    # differs from the IP used during the login phase.
    MFA_ENFORCE_IP_BINDING: bool = Field(
        default=False, description="Reject MFA completion if client IP changed since login (prevents stolen MFA tokens)"
    )
    # Token blacklist cache failure mode - fail_closed by default for security
    # "fail_open": Return None when Redis unavailable (allow auth to continue)
    # "fail_closed": Return True when Redis unavailable (treat as blacklisted, block auth)
    # S-1 NOTE: with fail_open, SessionManager enters degraded mode during Redis
    # outages and accepts sessions whose JWT is unexpired — revoked sessions can
    # be accepted for the outage duration. Only choose fail_open if availability
    # outweighs that risk.
    TOKEN_CACHE_FAILURE_MODE: Literal["fail_open", "fail_closed"] = Field(
        default="fail_closed",
        description="How to handle token blacklist cache failures: 'fail_open' (allow) or 'fail_closed' (block)",
    )
    # SECURITY FIX (S-4): Session registration failure mode. Previously there was
    # no config knob — the getattr fallback in login_helpers.py silently defaulted
    # to fail_open, so concurrent-session limiting was bypassed during Redis outages.
    # "fail_open": Log the error but let the login proceed (availability focused).
    # "fail_closed": Reject the login with 503 (security focused — session limiting enforced).
    # Mirrors AUDIT_LOG_FAILURE_MODE / TOKEN_CACHE_FAILURE_MODE (fail_closed default).
    SESSION_REGISTRATION_FAIL_MODE: Literal["fail_open", "fail_closed"] = Field(
        default="fail_closed",
        description="Session-registration failure mode: 'fail_open' (allow login) or 'fail_closed' (block login)",
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

    # S-3 FIX: default trusts ONLY loopback. The previous default trusted all
    # RFC1918 ranges, so any host on the internal network could spoof
    # X-Forwarded-For and corrupt rate-limit/ban/audit IP identity. Operators
    # behind an internal LB must explicitly set their LB's CIDRs here.
    TRUSTED_PROXY_IPS: list[str] | None = Field(
        default=["127.0.0.1", "::1"],
        description="List of trusted proxy IP addresses (loopback by default)",
    )
    TRUSTED_PROXY_NETWORKS: list[str] | None = Field(
        default=None,
        description=(
            "Trusted proxy network CIDRs. Default None = no network-level "
            "trust (loopback IPs above still apply). Set to your load "
            "balancer's CIDRs explicitly, e.g. ['10.0.0.0/8']."
        ),
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
        default="block",
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
    DEFAULT_RATE_LIMIT_LIMIT: int = 100
    DEFAULT_RATE_LIMIT_PERIOD: int = 3600
    TIER_RATE_LIMITS: dict[str, tuple[int, int]] = {
        "free": (10, 3600),
        "basic": (100, 3600),
        "pro": (1000, 3600),
        "enterprise": (10000, 3600),
    }
    DEFAULT_AUTHENTICATED_LIMIT: tuple[int, int] = (50, 3600)
    DEFAULT_ANONYMOUS_LIMIT: tuple[int, int] = (10, 3600)
    # If True, allow requests when Redis is unavailable (fail-open)
    # If False, block requests when Redis is unavailable (fail-closed, more secure)
    RATE_LIMIT_FAIL_OPEN: bool = False
    # Rate limiting algorithm: "fixed_window" or "gcra"
    # GCRA provides smoother limiting without boundary burst issues
    RATE_LIMIT_ALGORITHM: str = "gcra"
    # INFO-R11 FIX: previously a phantom knob (read via getattr default 3600
    # but defined nowhere). Declared so operators can tune the Redis TTL for
    # tier rate-limit policy cache entries.
    RATE_LIMIT_POLICY_TTL: int = Field(default=3600, ge=60, le=86400)


class CRUDAdminSettings(BaseSettings):
    CRUD_ADMIN_ENABLED: bool = False
    CRUD_ADMIN_MOUNT_PATH: str = "/admin"

    CRUD_ADMIN_ALLOWED_IPS_LIST: list[str] | None = None
    CRUD_ADMIN_ALLOWED_NETWORKS_LIST: list[str] | None = None
    CRUD_ADMIN_MAX_SESSIONS: int = Field(default=10, ge=1, le=100)
    CRUD_ADMIN_SESSION_TIMEOUT: int = Field(default=1440, ge=1, le=43200)
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
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USER: str | None = None
    SMTP_PASSWORD: SecretStr | None = None
    SMTP_USE_TLS: bool = True
    EMAIL_TEMPLATES_DIR: str = "src/app/templates"


class WebhookSettings(BaseSettings):
    # INFO-W-config FIX: these fields are now consumed by
    # modules/webhooks/service.py (retry schedule) and
    # modules/webhooks/http_client.py (HTTP timeouts). Previously the
    # MAX_RETRIES/RETRY_DELAY/TIMEOUT trio was dead config - hardcoded
    # values were used instead, silently ignoring operator overrides.
    WEBHOOK_RETRY_DELAYS: list[int] = Field(default=[60, 300, 900, 1800, 3600])
    WEBHOOK_HTTP_TIMEOUT_SECONDS: float = 30.0
    WEBHOOK_HTTP_CONNECT_TIMEOUT_SECONDS: float = 10.0
    WEBHOOK_SIGNATURE_HEADER: str = "X-Webhook-Signature"
    WEBHOOK_SIGNATURE_ALGORITHM: str = "sha256"
    WEBHOOK_RECOVERY_BATCH_SIZE: int = 100
    WEBHOOK_RECOVERY_BUFFER_MINUTES: int = 5


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


class BillingSettings(BaseSettings):
    """Stripe and Billing settings."""

    STRIPE_SECRET_KEY: SecretStr | None = None
    STRIPE_PUBLISHABLE_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: SecretStr | None = None
    DEFAULT_FREE_TIER_NAME: str = Field(default="free", description="Default tier name for free accounts")


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

    FEATURE_STREAMING_UPLOADS: bool = True
    """Use streaming multipart uploads for S3 storage (P0-3)."""

    FEATURE_EAGER_LOADING: bool = False
    """Use eager loading for relationships in base service (P0-4)."""

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

    @model_validator(mode="after")
    def validate_cors_origins(self) -> "Settings":
        """Validate CORS origins for production safety.

        Raises if wildcard '*' is used in non-local environments.
        Warns (S-6) if localhost origins are present in production/staging —
        the defaults include localhost for local dev and are not production-gated.
        """
        env = getattr(self.env.ENVIRONMENT, "value", self.env.ENVIRONMENT)
        has_wildcard = "*" in self.cors.CORS_ORIGINS

        if has_wildcard and env != "local":
            raise ValueError(
                f"SECURITY ERROR: CORS_ORIGINS contains '*' wildcard in {env} environment. "
                "This allows any website to access your API. "
                "Specify explicit origins for production."
            )

        # S-6: localhost origins in production/staging are almost always a
        # misconfiguration (leftover dev defaults). Warn, don't block.
        if env in ("production", "staging"):
            localhost_markers = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
            localhost_origins = [
                origin
                for origin in self.cors.CORS_ORIGINS
                if any(marker in origin for marker in localhost_markers)
            ]
            if localhost_origins:
                import logging

                logging.getLogger(__name__).warning(
                    "SECURITY WARNING (S-6): CORS_ORIGINS contains localhost origins in %s: %s. "
                    "Restrict CORS_ORIGINS to your real deployment origins.",
                    env,
                    localhost_origins,
                )

        return self

    @property
    def POSTGRES_URI(self) -> str:
        return self.postgres.POSTGRES_URI

    @property
    def REDIS_CACHE_URL(self) -> str:
        return self.redis_cache.REDIS_CACHE_URL

    @property
    def EFFECTIVE_POOL_SIZE(self) -> int:
        return self.postgres.EFFECTIVE_POOL_SIZE

    @property
    def SECRET_KEY(self) -> SecretStr:
        assert self.crypt.SECRET_KEY is not None
        return self.crypt.SECRET_KEY

    @property
    def ALGORITHM(self) -> str:
        return self.crypt.ALGORITHM

    @property
    def BCRYPT_THREAD_POOL_SIZE(self) -> int:
        return self.crypt.BCRYPT_THREAD_POOL_SIZE

    @property
    def ENVIRONMENT(self) -> Any:
        return self.env.ENVIRONMENT

    @property
    def CORS_ORIGINS(self) -> list[str]:
        return self.cors.CORS_ORIGINS

    @property
    def _delegation_map(self) -> dict[str, str]:
        if "_delegation_map" not in self.__dict__:
            dmap = {}
            for field_name in self.model_fields:
                if field_name == "model_config":
                    continue
                try:
                    component = super().__getattribute__(field_name)
                    # Use model_fields and __pydantic_computed_fields__ to avoid hasattr
                    for key in component.model_fields:
                        dmap[key] = field_name
                    computed = getattr(type(component), "__pydantic_computed_fields__", {})
                    for key in computed:
                        dmap[key] = field_name
                except AttributeError:
                    # Not a Pydantic model, skip
                    pass
                except Exception as e:
                    import logging

                    logging.getLogger(__name__).warning("Failed to map config delegation for %s: %s", field_name, e)
            self.__dict__["_delegation_map"] = dmap

        result: dict[str, str] = self.__dict__["_delegation_map"]
        return result

    def __getattr__(self, name: str) -> Any:
        target_component = self._delegation_map.get(name)
        if target_component:
            component = super().__getattribute__(target_component)
            return getattr(component, name)

        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self.model_fields or name.startswith("_") or name == "model_config":
            super().__setattr__(name, value)
            if name in self.model_fields and "_delegation_map" in self.__dict__:
                del self.__dict__["_delegation_map"]
            return

        target_component = self._delegation_map.get(name)
        if target_component:
            component = super().__getattribute__(target_component)
            setattr(component, name, value)
            return

        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")


# Initialize settings without arguments - Pydantic will populate from env
settings = Settings()  # type: ignore[call-arg]
