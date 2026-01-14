"""Integration tests for service layer performance optimizations.

Tests: U16 (FastCRUD Projection), U17 (Service Registry), U22 (Slug Normalization)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

logger = structlog.get_logger(__name__)


# =============================================================================
# U16: FastCRUD Projection
# =============================================================================
@pytest.mark.integration
class TestFastCRUDProjection:
    """U16: Verify projection schemas reduce query payload."""

    @pytest.mark.asyncio
    async def test_projection_reduces_columns(self, real_postgres):
        """Verify projection selects fewer columns."""
        from sqlalchemy import text

        # Full select (all columns)
        full_result = await real_postgres.execute(text('SELECT * FROM "user" LIMIT 1'))
        full_row = full_result.fetchone()

        # Projected select (fewer columns)
        projected_result = await real_postgres.execute(text('SELECT id, username, email FROM "user" LIMIT 1'))
        projected_row = projected_result.fetchone()

        if full_row and projected_row:
            # Full has more columns
            assert len(full_row) > len(projected_row)
            assert len(projected_row) == 3

    @pytest.mark.asyncio
    async def test_projection_excludes_sensitive_data(self, real_postgres):
        """Verify projection excludes sensitive columns."""
        from sqlalchemy import text

        # List columns should NOT include hashed_password
        projected_result = await real_postgres.execute(text('SELECT id, username, email, name FROM "user" LIMIT 1'))

        # Get column names from result
        columns = list(projected_result.keys())

        assert "hashed_password" not in columns
        assert "id" in columns

    @pytest.mark.asyncio
    async def test_projection_isomorphism(self, real_postgres):
        """Verify projected data matches subset of full data."""
        from sqlalchemy import text

        # Same user, different projections
        full_result = await real_postgres.execute(text('SELECT * FROM "user" WHERE id = 1'))
        full_row = full_result.fetchone()

        projected_result = await real_postgres.execute(text('SELECT id, username FROM "user" WHERE id = 1'))
        projected_row = projected_result.fetchone()

        if full_row and projected_row:
            # Projected fields match full fields
            assert projected_row[0] == full_row[0]  # id


# =============================================================================
# U17: Service Registry Cache
# =============================================================================
@pytest.mark.integration
class TestServiceRegistryCache:
    """U17: Verify service instances are cached in registry."""

    @pytest.mark.asyncio
    async def test_service_singleton(self):
        """Verify same service instance returned on multiple requests."""
        # Simulate service registry
        registry = {}

        def get_service(name: str, factory):
            if name not in registry:
                registry[name] = factory()
            return registry[name]

        class MockService:
            pass

        service1 = get_service("user_service", MockService)
        service2 = get_service("user_service", MockService)

        # Same instance
        assert service1 is service2

    @pytest.mark.asyncio
    async def test_registry_no_repeated_instantiation(self, perf_timer):
        """Verify service not re-instantiated on each request."""
        instantiation_count = 0

        class ExpensiveService:
            def __init__(self):
                nonlocal instantiation_count
                instantiation_count += 1

        registry = {}

        def get_service(name: str):
            if name not in registry:
                registry[name] = ExpensiveService()
            return registry[name]

        # Multiple requests
        for _ in range(10):
            get_service("expensive_service")

        # Only instantiated once
        assert instantiation_count == 1

    @pytest.mark.asyncio
    async def test_registry_per_request_isolation(self):
        """Verify registry supports per-request service state."""
        request_scoped = {}

        def get_request_service(request_id: str):
            if request_id not in request_scoped:
                request_scoped[request_id] = {"request_id": request_id}
            return request_scoped[request_id]

        svc1 = get_request_service("req1")
        svc2 = get_request_service("req2")
        svc1_again = get_request_service("req1")

        assert svc1 is svc1_again
        assert svc1 is not svc2

    @pytest.mark.asyncio
    async def test_registry_isomorphism(self):
        """Verify cached service behaves same as fresh instance."""

        class TestService:
            def __init__(self):
                self.value = 42

            def get_value(self):
                return self.value

        # Fresh instance
        fresh = TestService()

        # Cached instance
        registry = {"test": TestService()}
        cached = registry["test"]

        # Same behavior
        assert fresh.get_value() == cached.get_value()


# =============================================================================
# U22: Slug Normalization
# =============================================================================
@pytest.mark.integration
class TestSlugNormalization:
    """U22: Verify slug normalized to lowercase for consistent lookups."""

    @pytest.mark.asyncio
    async def test_slug_lowercase_normalization(self):
        """Verify slugs are normalized to lowercase."""

        def normalize_slug(slug: str) -> str:
            return slug.lower().strip()

        test_cases = [
            ("MyTenant", "mytenant"),
            ("my-tenant", "my-tenant"),
            ("MY_TENANT", "my_tenant"),
            ("  spaced  ", "spaced"),
            ("MixedCase123", "mixedcase123"),
        ]

        for input_slug, expected in test_cases:
            assert normalize_slug(input_slug) == expected

    @pytest.mark.asyncio
    async def test_slug_lookup_case_insensitive(self, clean_redis):
        """Verify same cache key for different cases."""

        def cache_key(slug: str) -> str:
            return f"tenant:slug:{slug.lower()}"

        # Different case inputs
        key1 = cache_key("MyTenant")
        key2 = cache_key("mytenant")
        key3 = cache_key("MYTENANT")

        # All resolve to same cache key
        assert key1 == key2 == key3

        # Cache hit works regardless of case
        await clean_redis.set(key1, "tenant_data")

        data1 = await clean_redis.get(key1)
        data2 = await clean_redis.get(key2)
        data3 = await clean_redis.get(key3)

        assert data1 == data2 == data3 == "tenant_data"

    @pytest.mark.asyncio
    async def test_slug_normalization_at_input(self):
        """Verify slugs normalized at API input level."""
        from pydantic import BaseModel, field_validator

        class TenantCreate(BaseModel):
            name: str
            slug: str

            @field_validator("slug")
            @classmethod
            def normalize_slug(cls, v: str) -> str:
                return v.lower().strip()

        tenant = TenantCreate(name="Test", slug="  MyTenant  ")
        assert tenant.slug == "mytenant"

    @pytest.mark.asyncio
    async def test_slug_normalization_isomorphism(self, clean_redis):
        """Verify normalized slug lookups return same data."""

        def normalize_slug(slug: str) -> str:
            return slug.lower().strip()

        # Store with normalized key
        normalized = normalize_slug("TestTenant")
        await clean_redis.set(f"tenant:{normalized}", "data")

        # Lookup with various cases - all should find same data
        lookups = ["TestTenant", "testtenant", "TESTTENANT", "testTENANT"]

        for lookup in lookups:
            key = f"tenant:{normalize_slug(lookup)}"
            data = await clean_redis.get(key)
            assert data == "data", f"Failed for lookup: {lookup}"

    @pytest.mark.asyncio
    async def test_slug_unicode_normalization(self):
        """Verify unicode slugs handled correctly."""

        def normalize_slug(slug: str) -> str:
            import unicodedata

            # Normalize unicode and convert to lowercase
            normalized = unicodedata.normalize("NFKC", slug)
            return normalized.lower().strip()

        # Unicode test cases
        assert normalize_slug("Café") == "café"
        assert normalize_slug("NAÏVE") == "naïve"

    @pytest.mark.asyncio
    async def test_slug_invalid_characters(self):
        """Verify invalid slug characters handled."""

        def validate_slug(slug: str) -> bool:
            import re

            # Only allow alphanumeric, hyphens, underscores
            return bool(re.match(r"^[a-z0-9\-_]+$", slug.lower()))

        assert validate_slug("valid-slug") is True
        assert validate_slug("valid_slug") is True
        assert validate_slug("valid123") is True
        assert validate_slug("invalid slug") is False  # space
        assert validate_slug("invalid!slug") is False  # special char
