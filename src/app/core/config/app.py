"""R2-03: settings split from core/config.py — app domain."""

from pydantic import Field
from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    APP_NAME: str = "FastAPI app"
    APP_DESCRIPTION: str | None = None
    APP_VERSION: str | None = None
    LICENSE_NAME: str | None = None
    CONTACT_NAME: str | None = None
    CONTACT_EMAIL: str | None = None
    FRONTEND_URL: str = "http://localhost:3000"  # Frontend URL for magic links, etc.
    # Cache warming
    CACHE_WARMING_ENABLED: bool = True
    # P-M9: token blacklist boot-time warmup loads every active blacklisted
    # token — O(total sessions). Cap the warmup set to the most-recent N rows;
    # tokens beyond the cap fall back to the on-demand DB check on first use.
    # 0 disables warmup entirely (cold start; every revoked-token lookup pays
    # one DB round-trip until it is cached).
    TOKEN_BLACKLIST_WARMUP_LIMIT: int = Field(default=1000, ge=0, le=1_000_000)

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

    # R1-07: BCRYPT_THREAD_POOL_SIZE lives on CryptSettings (flat access
    # routes there via the Settings property). The duplicate field that was
    # here was dead — shadowed by that property — and has been removed.

    WEBHOOK_ALLOW_INTERNAL_URLS: bool = Field(
        default=False, description="Allow webhooks to target internal IPs (RFC1918 etc)"
    )
