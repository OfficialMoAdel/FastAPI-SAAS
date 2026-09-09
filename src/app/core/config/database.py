"""R2-03: settings split from core/config.py — database domain."""

import os

from pydantic import BaseModel, Field, SecretStr, ValidationInfo, computed_field, field_validator
from pydantic_settings import BaseSettings

KNOWN_LEAKED_POSTGRES_PASSWORDS: frozenset[str] = frozenset(
    {
        # C-1: committed in 6cbc8a56/af450c42 (`.env.txt`, POSTGRES_PASSWORD).
        # When rotating after a leak, append the old value here.
        "mypassword",
    }
)


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

    @field_validator("DB_POOL_SIZE", mode="after")
    @classmethod
    def _warn_cross_process_headroom(cls, v: int, info: ValidationInfo) -> int:
        # P3-4: cross-process connection budget. The per-process validator
        # above already prevents over-allocation per process, but ``n
        # workers × (pool + overflow) + ARQ × ARQ_MAX_JOBS`` can still
        # exceed POSTGRES_MAX_CONNECTIONS once every worker is up. Warn
        # when the aggregate is within 10% of the limit so operators see
        # the squeeze before it bites.
        workers = info.data.get("DB_WORKERS", 1)
        max_overflow = info.data.get("DB_MAX_OVERFLOW", 10)
        max_conn = info.data.get("POSTGRES_MAX_CONNECTIONS", 100)
        try:
            from src.app.core.config.integrations import ArqSettings

            arq = ArqSettings()  # model_validate is fine; settings are env-driven
            arq_conns = arq.ARQ_WORKERS * arq.ARQ_MAX_JOBS
        except Exception:
            # ArqSettings construction can fail in tests without env vars;
            # do not fail the database config because of it.
            arq_conns = 0
        aggregate = workers * (v + max_overflow) + arq_conns
        if aggregate >= int(0.9 * max_conn):
            import logging

            logging.getLogger("app.core.config").warning(
                "Connection budget tight: workers*pool(=%d) + ARQ(=%d) = %d, "
                "POSTGRES_MAX_CONNECTIONS=%d (>=90%% used). Either reduce "
                "DB_POOL_SIZE / DB_WORKERS / ARQ_MAX_JOBS, or raise "
                "PostgreSQL max_connections.",
                workers * (v + max_overflow),
                arq_conns,
                aggregate,
                max_conn,
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

    @field_validator("POSTGRES_PASSWORD", mode="after")
    @classmethod
    def validate_no_leaked_password(cls, v: SecretStr | None, info: ValidationInfo) -> SecretStr | None:
        # C-1: the value was committed to git history (.env.txt in
        # 6cbc8a56/af450c42). Hard-fail outside local; in local it is also
        # the documented example value, so only warn — but never let it
        # boot silently in staging/production.
        if v is None or v.get_secret_value() not in KNOWN_LEAKED_POSTGRES_PASSWORDS:
            return v
        # DatabaseConfigMixin doesn't carry ENVIRONMENT; re-read from the
        # process environment the same way CryptSettings does (dotenv-aware).
        from pathlib import Path

        from dotenv import dotenv_values

        env_value = os.environ.get("ENVIRONMENT")
        if env_value is None:
            src_dir_env = Path(__file__).resolve().parents[2] / ".env"
            repo_env = Path(".env")
            for candidate in (src_dir_env, repo_env):
                if candidate.is_file():
                    env_value = dotenv_values(candidate).get("ENVIRONMENT")
                    if env_value is not None:
                        break
        env = (env_value or "local").lower()
        if env in ("production", "staging"):
            raise ValueError(
                "POSTGRES_PASSWORD matches a value that leaked into git history. "
                "Rotate it immediately and update your .env."
            )
        import logging

        logging.getLogger(__name__).warning(
            "POSTGRES_PASSWORD matches a value that leaked into git history (C-1). "
            "Local boot allowed, but rotate it and pick a unique password — this "
            "value must never reach staging/production."
        )
        return v

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
