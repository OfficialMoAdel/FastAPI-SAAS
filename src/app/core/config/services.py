"""R2-03: settings split from core/config.py — services domain."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


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
    # Security budgets for sensitive endpoints, keyed by budget name.
    # These are identity-keyed brute-force defenses (login attempts, MFA
    # verification, passkey login, AI calls) — distinct from tier path
    # policies above. All values moved here from module-level hardcodes so
    # operators tune them without code changes (same pipeline as ADR-0002).
    SECURITY_BUDGETS: dict[str, tuple[int, int]] = {
        # auth login/register/refresh/reset per client IP (was auth/rate_limit.py 5/60)
        "auth": (5, 60),
        # MFA verification attempts (was mfa.py MFA_VERIFY_* 5/60)
        "mfa_verify": (5, 60),
        # MFA state mutations (was mfa.py MFA_MUTATE_* 5/60; separate budget
        # so verify attempts cannot starve mutations, per P1-5)
        "mfa_mutate": (5, 60),
        # magic link send per email hash (was the anonymous-email path of
        # the deleted rate_limiter_dependency: 3/min, anti-enumeration)
        "magic_link_send": (3, 60),
        "passkey_login": (10, 60),
        # AI chat completions per user (was ai/rate_limiter.py 60/60)
        "ai_chat": (60, 60),
    }


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
    # P0-1: signature freshness window for inbound verification (seconds).
    WEBHOOK_SIGNATURE_MAX_AGE_SECONDS: int = Field(
        default=300, ge=1, le=3600, description="Max age of a webhook signature's t= timestamp before rejection"
    )
    WEBHOOK_SIGNATURE_ALGORITHM: str = "sha256"
    WEBHOOK_RECOVERY_BATCH_SIZE: int = 100
    WEBHOOK_RECOVERY_BUFFER_MINUTES: int = 5
