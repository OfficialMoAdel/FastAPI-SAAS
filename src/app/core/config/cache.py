"""R2-03: settings split from core/config.py — cache domain."""

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings


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
