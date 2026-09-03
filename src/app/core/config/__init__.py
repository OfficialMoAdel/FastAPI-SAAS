"""R2-03: configuration package (split from the monolithic core/config.py).

This package is the strangler-start of the config split. Every settings class
now lives in a domain module (app, crypt, database, auth, cache, security,
services, environment, integrations, performance), and this ``__init__``
re-exports them all so that existing ``from src.app.core.config import X``
imports keep working unchanged.

The ``Settings`` composition class, its flat-access delegation
(``__getattr__``/``__setattr__``/``_delegation_map``), and the ``settings``
singleton are defined here exactly as before — behavior is byte-identical.
Removing the delegation (the report's later big-bang step) is deliberately NOT
done in this increment.
"""

from typing import Any

from dotenv import load_dotenv

# Load .env into os.environ before child BaseSettings classes instantiate
load_dotenv()
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.app.core.config.app import AppSettings
from src.app.core.config.auth import KNOWN_LEAKED_ADMIN_PASSWORDS, AuthSettings, FirstUserSettings
from src.app.core.config.cache import ClientSideCacheSettings, RedisCacheSettings, TestSettings
from src.app.core.config.crypt import KNOWN_LEAKED_SECRET_KEYS, CryptSettings
from src.app.core.config.database import DatabaseConfigMixin, MySQLSettings, PostgresSettings, SQLiteSettings
from src.app.core.config.environment import CORSSettings, EnvironmentOption, EnvironmentSettings, StorageSettings
from src.app.core.config.integrations import AISettings, ArqSettings, BillingSettings
from src.app.core.config.performance import EventSettings, PerformanceSettings, QueryProfilerSettings, R1UnifiedSettings
from src.app.core.config.security import AttackDetectionSettings, SecuritySettings
from src.app.core.config.services import (
    CRUDAdminSettings,
    DefaultRateLimitSettings,
    EmailSettings,
    RedisQueueSettings,
    WebhookSettings,
)

__all__ = [
    "AppSettings",
    "KNOWN_LEAKED_SECRET_KEYS",
    "CryptSettings",
    "DatabaseConfigMixin",
    "SQLiteSettings",
    "MySQLSettings",
    "PostgresSettings",
    "KNOWN_LEAKED_ADMIN_PASSWORDS",
    "FirstUserSettings",
    "AuthSettings",
    "TestSettings",
    "RedisCacheSettings",
    "ClientSideCacheSettings",
    "SecuritySettings",
    "AttackDetectionSettings",
    "RedisQueueSettings",
    "DefaultRateLimitSettings",
    "CRUDAdminSettings",
    "EmailSettings",
    "WebhookSettings",
    "EnvironmentOption",
    "EnvironmentSettings",
    "StorageSettings",
    "CORSSettings",
    "ArqSettings",
    "BillingSettings",
    "AISettings",
    "EventSettings",
    "R1UnifiedSettings",
    "PerformanceSettings",
    "QueryProfilerSettings",
    "Settings",
    "settings",
]


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

        # P1-7: localhost origins in production/staging are almost always a
        # misconfiguration (leftover dev defaults). Promote the prior
        # warning to a hard fail so operators can't ship dev origins to prod
        # by accident. Mirrors the wildcard-`*` behavior at lines 127-132.
        if env in ("production", "staging"):
            localhost_markers = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
            localhost_origins = [
                origin for origin in self.cors.CORS_ORIGINS if any(marker in origin for marker in localhost_markers)
            ]
            if localhost_origins:
                raise ValueError(
                    f"SECURITY ERROR (S-6): CORS_ORIGINS contains localhost origins in {env}: {localhost_origins}. "
                    "Restrict CORS_ORIGINS to your real deployment origins."
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
            # P2-2: warn on first access through the dynamic delegation map.
            # The plan is to codemod call sites to ``settings.<component>.<name>``
            # and delete this fallback. Until that lands, every use of the
            # legacy flat accessor is a hint to migrate.
            # P2-2: warn once per field (not per access) — otherwise the
            # hot path spams stderr and PowerShell treats every warning line
            # as a NativeCommandError when 2>&1 is used.
            _warned: set[str] = getattr(self, "_flat_accessor_warned", set())
            if name not in _warned:
                _warned.add(name)
                try:
                    object.__setattr__(self, "_flat_accessor_warned", _warned)
                except Exception:
                    pass
                import logging

                logging.getLogger(__name__).debug(
                    "config_legacy_flat_accessor_used field=%s component=%s",
                    name,
                    target_component,
                )
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
