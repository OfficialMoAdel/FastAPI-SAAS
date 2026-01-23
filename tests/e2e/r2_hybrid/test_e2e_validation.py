"""R2-HYBRID End-to-End Validation Tests.

These tests validate optimizations work correctly in realistic scenarios.
Run with: uv run pytest tests/e2e/r2_hybrid/ -v --tb=short -s

Requirements:
- Running PostgreSQL (test database)
- Running Redis (test instance)
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

import pytest

# Configure detailed logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S.%f",
)
logger = logging.getLogger(__name__)


@pytest.fixture
def log_section():
    """Log test section start/end."""

    def _log(name: str):
        logger.info("=" * 60)
        logger.info(f"TEST: {name}")
        logger.info("=" * 60)

    return _log


class TestL1CacheE2E:
    """End-to-end tests for L1 cache behavior."""

    @pytest.mark.asyncio
    async def test_tenant_l1_cache_reduces_redis_calls(self, log_section):
        """E2E: Verify L1 cache reduces Redis calls for repeated tenant lookups."""
        log_section("Tenant L1 Cache Redis Reduction")

        from src.app.middleware.tenant import TenantMiddleware, _L1_TENANT_CACHE
        from unittest.mock import AsyncMock, MagicMock, patch

        # Clear cache
        _L1_TENANT_CACHE.clear()
        redis_call_count = 0

        async def mock_redis_get(key):
            nonlocal redis_call_count
            redis_call_count += 1
            logger.info(f"Redis GET called (call #{redis_call_count}): {key}")
            return '{"id": "tenant-001", "slug": "test", "is_active": true}'

        mock_redis = AsyncMock()
        mock_redis.get = mock_redis_get

        middleware = TenantMiddleware(MagicMock())

        # First call - should hit Redis
        logger.info("Call 1: Expecting Redis hit...")
        with patch("src.app.core.utils.cache.client", mock_redis):
            result1 = await middleware._get_tenant_by_slug("test")

        assert redis_call_count == 1
        logger.info(f"Call 1 result: tenant_id={result1.id}, Redis calls={redis_call_count}")

        # Second call - should hit L1 cache, NOT Redis
        logger.info("Call 2: Expecting L1 cache hit...")
        with patch("src.app.core.utils.cache.client", mock_redis):
            result2 = await middleware._get_tenant_by_slug("test")

        assert redis_call_count == 1, "Redis should NOT be called on L1 cache hit"
        logger.info(f"Call 2 result: tenant_id={result2.id}, Redis calls={redis_call_count}")

        # Verify L1 cache is populated
        assert "test" in _L1_TENANT_CACHE
        logger.info(f"L1 cache populated: {list(_L1_TENANT_CACHE.keys())}")
        logger.info("✅ PASSED: L1 cache reduces Redis calls")

    @pytest.mark.asyncio
    async def test_rbac_l1_cache_reduces_redis_calls(self, log_section):
        """E2E: Verify RBAC L1 cache reduces Redis calls for permission checks."""
        log_section("RBAC L1 Cache Redis Reduction")

        from src.app.modules.rbac.service import RoleService, _L1_RBAC_CACHE
        from unittest.mock import AsyncMock, patch

        # Clear cache
        _L1_RBAC_CACHE.clear()
        redis_call_count = 0

        async def mock_redis_get(key):
            nonlocal redis_call_count
            redis_call_count += 1
            logger.info(f"Redis GET called (call #{redis_call_count}): {key}")
            return '[{"id": 1, "slug": "read", "description": "Read", "created_at": "2024-01-01T00:00:00", "updated_at": "2024-01-01T00:00:00"}]'

        mock_redis = AsyncMock()
        mock_redis.get = mock_redis_get
        mock_db = AsyncMock()

        tenant_id = "tenant-e2e"
        role_id = 100

        with patch("src.app.modules.rbac.service.settings") as mock_settings:
            with patch("src.app.modules.rbac.service.record_rbac_cache_hit"):
                mock_settings.r1_unified.RBAC_USE_REDIS_CACHE = True
                service = RoleService(db=mock_db, tenant_id=tenant_id, redis_client=mock_redis)

                # First call - should hit Redis
                logger.info("Call 1: Expecting Redis hit...")
                perms1 = await service.get_role_permissions(role_id)
                assert redis_call_count == 1
                logger.info(f"Call 1: {len(perms1)} permissions, Redis calls={redis_call_count}")

                # Second call - should hit L1 cache
                logger.info("Call 2: Expecting L1 cache hit...")
                perms2 = await service.get_role_permissions(role_id)
                assert redis_call_count == 1, "Redis should NOT be called on L1 hit"
                logger.info(f"Call 2: {len(perms2)} permissions, Redis calls={redis_call_count}")

        logger.info("✅ PASSED: RBAC L1 cache reduces Redis calls")


class TestTokenBlacklistE2E:
    """End-to-end tests for token blacklist optimization."""

    def test_negative_cache_ttl_configured(self, log_section):
        """E2E: Verify negative cache TTL is configured correctly."""
        log_section("Token Blacklist Negative Cache TTL")

        from src.app.core.config import settings
        from src.app.core.security.token_blacklist_cache import _get_negative_cache_ttl

        ttl = _get_negative_cache_ttl()
        logger.info(f"Configured negative cache TTL: {ttl} seconds")

        assert ttl == settings.r1_unified.TOKEN_NEGATIVE_CACHE_TTL
        assert ttl == 300, "Default TTL should be 300 seconds (5 minutes)"

        logger.info("✅ PASSED: Token blacklist TTL is 300 seconds")


class TestFeatureFlagsE2E:
    """End-to-end tests for feature flag configuration."""

    def test_all_flags_enabled(self, log_section):
        """E2E: Verify all R1-UNIFIED feature flags are enabled by default."""
        log_section("Feature Flags Enabled")

        from src.app.core.config import settings

        flags = {
            "EVENT_BUS_USE_LUA_IDEMPOTENCY": settings.r1_unified.EVENT_BUS_USE_LUA_IDEMPOTENCY,
            "SESSION_USE_PIPELINE_EVICTION": settings.r1_unified.SESSION_USE_PIPELINE_EVICTION,
            "XFETCH_USE_LUA_SCRIPT": settings.r1_unified.XFETCH_USE_LUA_SCRIPT,
            "RBAC_USE_REDIS_CACHE": settings.r1_unified.RBAC_USE_REDIS_CACHE,
            "DLQ_USE_BOUNDED_SIZE": settings.r1_unified.DLQ_USE_BOUNDED_SIZE,
            "PASSKEY_USE_COLUMN_SELECT": settings.r1_unified.PASSKEY_USE_COLUMN_SELECT,
            "CIRCUIT_BREAKER_USE_DOUBLE_CHECK": settings.r1_unified.CIRCUIT_BREAKER_USE_DOUBLE_CHECK,
        }

        for flag_name, value in flags.items():
            status = "✅" if value else "❌"
            logger.info(f"  {status} {flag_name}: {value}")
            assert value is True, f"{flag_name} should be True by default"

        logger.info("✅ PASSED: All 7 feature flags enabled")


class TestFileIndexE2E:
    """End-to-end tests for file MIME type index."""

    def test_mime_index_exists_in_model(self, log_section):
        """E2E: Verify MIME type composite index is defined in model."""
        log_section("File MIME Type Index")

        from src.app.modules.files.models import File

        table_args = File.__table_args__
        indices = [idx for idx in table_args if hasattr(idx, "name")]

        logger.info("Defined indices:")
        for idx in indices:
            columns = [col.name for col in idx.columns]
            logger.info(f"  - {idx.name}: ({', '.join(columns)})")

        index_names = [idx.name for idx in indices]
        assert "ix_files_tenant_mime" in index_names

        # Verify column order
        mime_idx = next(idx for idx in indices if idx.name == "ix_files_tenant_mime")
        columns = [col.name for col in mime_idx.columns]
        assert columns == ["tenant_id", "mime_type"]

        logger.info("✅ PASSED: MIME type index defined correctly")


class TestSessionPipelineE2E:
    """End-to-end tests for session pipeline optimization."""

    @pytest.mark.asyncio
    async def test_session_pipeline_flag_enabled(self, log_section):
        """E2E: Verify session pipeline eviction flag is enabled."""
        log_section("Session Pipeline Eviction")

        from src.app.core.config import settings

        flag_value = settings.r1_unified.SESSION_USE_PIPELINE_EVICTION
        logger.info(f"SESSION_USE_PIPELINE_EVICTION: {flag_value}")

        assert flag_value is True, "Pipeline eviction should be enabled"

        logger.info("✅ PASSED: Session pipeline eviction enabled")


class TestCacheConfigE2E:
    """End-to-end tests for cache configuration."""

    def test_l1_cache_sizing(self, log_section):
        """E2E: Verify L1 cache sizing is appropriate."""
        log_section("L1 Cache Sizing")

        from src.app.middleware.tenant import _L1_TENANT_CACHE
        from src.app.modules.rbac.service import _L1_RBAC_CACHE

        logger.info("L1 Cache Configurations:")
        logger.info(f"  Tenant Cache: maxsize={_L1_TENANT_CACHE.maxsize}, ttl={_L1_TENANT_CACHE.ttl}s")
        logger.info(f"  RBAC Cache: maxsize={_L1_RBAC_CACHE.maxsize}, ttl={_L1_RBAC_CACHE.ttl}s")

        # Verify sizing is reasonable
        assert _L1_TENANT_CACHE.maxsize >= 100, "Tenant cache should support at least 100 tenants"
        assert _L1_RBAC_CACHE.maxsize >= 100, "RBAC cache should support at least 100 roles"
        assert _L1_TENANT_CACHE.ttl >= 1.0, "Tenant cache TTL should be at least 1 second"
        assert _L1_RBAC_CACHE.ttl >= 1.0, "RBAC cache TTL should be at least 1 second"

        logger.info("✅ PASSED: L1 cache sizing is appropriate")


def generate_summary_report():
    """Generate summary report of all E2E tests."""
    logger.info("\n" + "=" * 60)
    logger.info("R2-HYBRID E2E VALIDATION SUMMARY")
    logger.info("=" * 60)
    logger.info("Run: uv run pytest tests/e2e/r2_hybrid/ -v -s")
    logger.info("=" * 60)
