"""R2-03: settings split from core/config.py — database domain."""

from pydantic import BaseModel, Field, SecretStr, ValidationInfo, computed_field, field_validator
from pydantic_settings import BaseSettings


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
