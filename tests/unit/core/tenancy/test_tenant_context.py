"""Unit tests for tenant context operations using ContextVar.

Tests the core tenant context functionality:
- Setting and getting tenant context
- Clearing tenant context
- Isolation between async operations
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from src.app.core.tenancy.context import (
    TenantContext,
    clear_tenant_context,
    get_current_tenant,
    set_current_tenant,
)
from src.app.modules.tenants.models import Tenant


def create_mock_tenant(name: str, slug: str, is_active: bool = True) -> MagicMock:
    """Create a mock tenant object for testing."""
    tenant = MagicMock()
    tenant.name = name
    tenant.slug = slug
    tenant.is_active = is_active
    tenant.id = 1
    return tenant


class TestTenantContext:
    """Test suite for tenant context operations."""

    def test_get_tenant_context_returns_none_when_not_set(self) -> None:
        """Test that get_current_tenant returns None when no tenant is set."""
        clear_tenant_context()
        result = get_current_tenant()
        assert result is None

    def test_set_and_get_tenant_context(self) -> None:
        """Test setting and getting tenant context."""
        clear_tenant_context()
        mock_tenant = create_mock_tenant("Test Tenant", "test-tenant")

        set_current_tenant(mock_tenant)
        result = get_current_tenant()

        assert result is not None
        assert result.tenant_slug == "test-tenant"
        assert result.tenant_name == "Test Tenant"
        assert result.tenant_id == 1

        clear_tenant_context()

    def test_clear_tenant_context(self) -> None:
        """Test clearing tenant context."""
        mock_tenant = create_mock_tenant("Test Tenant", "test-tenant")
        set_current_tenant(mock_tenant)

        clear_tenant_context()
        result = get_current_tenant()

        assert result is None

    @pytest.mark.asyncio
    async def test_context_isolation_between_async_tasks(self) -> None:
        """Test that tenant context is isolated between concurrent async tasks."""
        clear_tenant_context()
        results: dict[str, str | None] = {}

        async def task_a() -> None:
            tenant_a = create_mock_tenant("Tenant A", "tenant-a")
            set_current_tenant(tenant_a)
            await asyncio.sleep(0.01)
            ctx = get_current_tenant()
            results["task_a"] = ctx.tenant_slug if ctx else None

        async def task_b() -> None:
            tenant_b = create_mock_tenant("Tenant B", "tenant-b")
            set_current_tenant(tenant_b)
            await asyncio.sleep(0.01)
            ctx = get_current_tenant()
            results["task_b"] = ctx.tenant_slug if ctx else None

        await asyncio.gather(task_a(), task_b())

        assert results["task_a"] == "tenant-a"
        assert results["task_b"] == "tenant-b"

        clear_tenant_context()

    def test_set_tenant_context_with_none(self) -> None:
        """Test setting tenant context to None explicitly."""
        mock_tenant = create_mock_tenant("Test Tenant", "test-tenant")
        set_current_tenant(mock_tenant)

        set_current_tenant(None)
        result = get_current_tenant()

        assert result is None

    def test_get_tenant_context_fields(self) -> None:
        """Test that tenant context contains all expected fields."""
        clear_tenant_context()
        mock_tenant = create_mock_tenant("Test Tenant", "test-tenant", is_active=True)

        set_current_tenant(mock_tenant)
        result = get_current_tenant()

        assert result is not None
        assert result.tenant_id == 1
        assert result.tenant_slug == "test-tenant"
        assert result.tenant_name == "Test Tenant"
        assert result.is_active is True

        clear_tenant_context()

    @pytest.mark.asyncio
    async def test_lightweight_context_isolation(self) -> None:
        """Test that lightweight context is isolated between async tasks."""
        clear_tenant_context()
        results: dict[str, str | None] = {}

        async def task_a() -> None:
            tenant_a = create_mock_tenant("Tenant A", "tenant-a")
            set_current_tenant(tenant_a)
            await asyncio.sleep(0.01)
            ctx = get_current_tenant()
            results["task_a"] = ctx.tenant_slug if ctx else None

        async def task_b() -> None:
            tenant_b = create_mock_tenant("Tenant B", "tenant-b")
            set_current_tenant(tenant_b)
            await asyncio.sleep(0.01)
            ctx = get_current_tenant()
            results["task_b"] = ctx.tenant_slug if ctx else None

        await asyncio.gather(task_a(), task_b())

        assert results["task_a"] == "tenant-a"
        assert results["task_b"] == "tenant-b"

        clear_tenant_context()


# === MEMORY OPTIMIZATION TESTS (from tests/unit/tenancy/) ===
# Tests for TenantContext memory optimization - __slots__, immutability

import sys


@pytest.fixture
def sample_tenant_for_memory() -> Tenant:
    """Create a sample Tenant model for testing."""
    tenant = Tenant(
        name="Test Tenant",
        slug="test-tenant",
        is_active=True,
    )
    tenant.id = 1
    return tenant


def test_tenant_context_from_tenant(sample_tenant_for_memory: Tenant) -> None:
    """Verify all fields correctly extracted from Tenant model."""
    context = TenantContext.from_model(sample_tenant_for_memory)

    assert context.tenant_id == sample_tenant_for_memory.id
    assert context.tenant_slug == sample_tenant_for_memory.slug
    assert context.tenant_name == sample_tenant_for_memory.name
    assert context.is_active == sample_tenant_for_memory.is_active


def test_tenant_context_immutable(sample_tenant_for_memory: Tenant) -> None:
    """Verify frozen=True prevents modification."""
    context = TenantContext.from_model(sample_tenant_for_memory)

    with pytest.raises(AttributeError):
        context.tenant_id = 999

    with pytest.raises(AttributeError):
        context.tenant_slug = "new-slug"

    with pytest.raises(AttributeError):
        context.tenant_name = "New Name"


def test_tenant_context_slots(sample_tenant_for_memory: Tenant) -> None:
    """Verify __slots__ reduces memory usage."""
    context = TenantContext.from_model(sample_tenant_for_memory)

    assert hasattr(TenantContext, "__slots__")
    assert not hasattr(context, "__dict__")

    expected_slots = {"tenant_id", "tenant_slug", "tenant_name", "is_active", "extra"}
    assert set(TenantContext.__slots__) == expected_slots


def test_backwards_compatibility(sample_tenant_for_memory: Tenant) -> None:
    """Verify backward compatibility properties work correctly."""
    context = TenantContext.from_model(sample_tenant_for_memory)

    assert context.id == sample_tenant_for_memory.id
    assert context.slug == sample_tenant_for_memory.slug
    assert context.name == sample_tenant_for_memory.name

    assert context.tenant_id == sample_tenant_for_memory.id
    assert context.tenant_slug == sample_tenant_for_memory.slug
    assert context.tenant_name == sample_tenant_for_memory.name


def test_tenant_context_memory_size(sample_tenant_for_memory: Tenant) -> None:
    """Verify TenantContext uses __slots__ for memory efficiency."""
    context = TenantContext.from_model(sample_tenant_for_memory)

    sys.getsizeof(context)
    sys.getsizeof(sample_tenant_for_memory)

    assert hasattr(TenantContext, "__slots__")
    assert not hasattr(context, "__dict__")
