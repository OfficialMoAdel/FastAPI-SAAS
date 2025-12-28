"""Unit tests for tenant context operations using ContextVar.

Tests the core tenant context functionality:
- Setting and getting tenant context
- Clearing tenant context
- Isolation between async operations
"""

import asyncio

import pytest

from src.app.core.tenancy.context import clear_tenant_context, get_tenant_context, set_tenant_context
from src.app.modules.tenants.models import Tenant


class TestTenantContext:
    """Test suite for tenant context operations."""

    def test_get_tenant_context_returns_none_when_not_set(self) -> None:
        """Test that get_tenant_context returns None when no tenant is set."""
        clear_tenant_context()
        result = get_tenant_context()
        assert result is None

    def test_set_and_get_tenant_context(self) -> None:
        """Test setting and getting tenant context."""
        clear_tenant_context()
        mock_tenant = Tenant(name="Test Tenant", slug="test-tenant", is_active=True)

        set_tenant_context(mock_tenant)
        result = get_tenant_context()

        assert result is not None
        assert result.slug == "test-tenant"
        assert result.name == "Test Tenant"

        clear_tenant_context()

    def test_clear_tenant_context(self) -> None:
        """Test clearing tenant context."""
        mock_tenant = Tenant(name="Test Tenant", slug="test-tenant", is_active=True)
        set_tenant_context(mock_tenant)

        clear_tenant_context()
        result = get_tenant_context()

        assert result is None

    @pytest.mark.asyncio
    async def test_context_isolation_between_async_tasks(self) -> None:
        """Test that tenant context is isolated between concurrent async tasks."""
        clear_tenant_context()
        results: dict[str, str | None] = {}

        async def task_a() -> None:
            tenant_a = Tenant(name="Tenant A", slug="tenant-a", is_active=True)
            set_tenant_context(tenant_a)
            await asyncio.sleep(0.01)
            ctx = get_tenant_context()
            results["task_a"] = ctx.slug if ctx else None

        async def task_b() -> None:
            tenant_b = Tenant(name="Tenant B", slug="tenant-b", is_active=True)
            set_tenant_context(tenant_b)
            await asyncio.sleep(0.01)
            ctx = get_tenant_context()
            results["task_b"] = ctx.slug if ctx else None

        await asyncio.gather(task_a(), task_b())

        assert results["task_a"] == "tenant-a"
        assert results["task_b"] == "tenant-b"

        clear_tenant_context()

    def test_set_tenant_context_with_none(self) -> None:
        """Test setting tenant context to None explicitly."""
        mock_tenant = Tenant(name="Test Tenant", slug="test-tenant", is_active=True)
        set_tenant_context(mock_tenant)

        set_tenant_context(None)
        result = get_tenant_context()

        assert result is None
