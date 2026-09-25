"""Core configuration tests - consolidated from multiple small test files."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import SecretStr
from redis.asyncio import ConnectionPool

from src.app.core import setup
from src.app.core.config import AppSettings, CryptSettings, R1UnifiedSettings
from src.app.core.redis_provider import close_redis_pool, get_redis_pool

# ==================== Config Defaults ====================


def test_rbac_cache_ttl_default():
    """Verify RBAC cache TTL default is 1 hour (3600 seconds)."""
    settings = R1UnifiedSettings()
    assert settings.RBAC_CACHE_TTL_SECONDS == 3600


# ==================== Config Secrets ====================


def test_encryption_keys_missing_warning():
    """Verify ValueError is raised when encryption keys are missing."""
    import os

    import pytest

    with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
        with pytest.raises(ValueError, match="ENCRYPTION_KEY and ENCRYPTION_SALT must be set"):
            from pydantic import SecretStr

            from src.app.core.config import CryptSettings

            CryptSettings(SECRET_KEY=SecretStr("s" * 32), ENCRYPTION_KEY=None, ENCRYPTION_SALT=None)


def test_encryption_keys_present_no_warning(caplog):
    """Verify no warning is logged when encryption keys are present."""
    import logging

    caplog.set_level(logging.WARNING)

    CryptSettings(
        SECRET_KEY=SecretStr("s" * 32), ENCRYPTION_KEY=SecretStr("k" * 32), ENCRYPTION_SALT=SecretStr("s" * 16)
    )

    assert "SECURITY: Encryption keys (ENCRYPTION_KEY, ENCRYPTION_SALT) are missing" not in caplog.text


# ==================== Redis Factory ====================


# Override conftest.mock_redis to prevent it from patching create_redis_cache_pool
@pytest.fixture(autouse=True)
def mock_redis():
    yield


@pytest.mark.asyncio
async def test_redis_factory_singleton():
    """Verify that get_redis_pool returns the same instance."""
    # Reset factory state
    await close_redis_pool()

    pool1 = await get_redis_pool()
    pool2 = await get_redis_pool()

    assert pool1 is pool2
    assert isinstance(pool1, ConnectionPool)
    # Check decode_responses is False (bytes mode) for ARQ compatibility
    assert pool1.connection_kwargs.get("decode_responses") is False

    await close_redis_pool()
    assert getattr(get_redis_pool, "_pool", None) is None


@pytest.mark.asyncio
async def test_setup_uses_shared_pool():
    """Verify setup delegates to the provider, which uses the shared pool for both cache and queue."""
    from src.app.core.utils import cache as cache_module
    from src.app.core.utils import queue as queue_module

    # Reset globals to bypass idempotency guards in the provider/setup hooks
    cache_module.client = None
    cache_module.pool = None
    queue_module.pool = None

    mock_pool = Mock(spec=ConnectionPool)

    try:
        # P1-#3: construction lives in redis_provider — patch the provider seam
        with (
            patch("src.app.core.redis_provider.get_redis_pool", new_callable=AsyncMock) as mock_get_pool,
            patch("src.app.core.redis_provider.Redis") as mock_redis_cls,
            patch("src.app.core.redis_provider.ArqRedis") as mock_arq_cls,
        ):
            mock_get_pool.return_value = mock_pool

            # Run creation (thin setup delegates into the provider)
            await setup.create_redis_cache_pool()
            await setup.create_redis_queue_pool()

            # Verify provider pool factory called once per client
            assert mock_get_pool.call_count == 2

            # Verify cache client creation
            # cache.client = Redis(connection_pool=pool, decode_responses=True)
            mock_redis_cls.assert_called_with(connection_pool=mock_pool, decode_responses=True)

            # Verify queue client creation
            # K1: queue.pool = ArqRedis(connection_pool=pool, job_serializer=..., job_deserializer=...)
            call_kwargs = mock_arq_cls.call_args.kwargs
            assert call_kwargs["connection_pool"] is mock_pool
            assert "job_serializer" in call_kwargs, "web-side pool must serialize jobs with JSON (K1)"
            assert "job_deserializer" in call_kwargs, "worker expects JSON-encoded job payloads"

            # Verify same pool used + installed into legacy globals via set_clients
            assert cache_module.pool is mock_pool
            assert cache_module.client is mock_redis_cls.return_value
    finally:
        # Restore globals so mock instances don't leak into other tests
        cache_module.client = None
        cache_module.pool = None
        queue_module.pool = None


# ==================== Logging settings (P2-13) ====================


def test_log_settings_defaults():
    """Verify logging moved onto the AppSettings surface with stable defaults."""
    app_settings = AppSettings()

    assert app_settings.LOG_LEVEL == "INFO"
    assert app_settings.LOG_DIR.endswith("logs")


def test_get_log_level_reads_settings(monkeypatch):
    """Verify get_log_level() resolves through settings with an INFO fallback."""
    import logging

    from src.app.core import logger as logger_module

    monkeypatch.setattr(logger_module.settings.app, "LOG_LEVEL", "debug")
    assert logger_module.get_log_level() == logging.DEBUG

    monkeypatch.setattr(logger_module.settings.app, "LOG_LEVEL", "bogus-level")
    assert logger_module.get_log_level() == logging.INFO
