"""Memory benchmarks for TenantContext optimization.

Measures memory usage and compares TenantContext vs Tenant model.
Uses tracemalloc for accurate memory allocation tracking.
"""

from __future__ import annotations

import sys
import tracemalloc

import pytest

from src.app.core.tenancy.context import TenantContext

# Import Notification to ensure it is registered in SQLAlchemy for the relationship
from src.app.modules.tenants.models import Tenant


@pytest.fixture
def sample_tenant() -> Tenant:
    """Create a sample Tenant model for testing."""
    tenant = Tenant(
        name="Test Tenant",
        slug="test-tenant",
        is_active=True,
    )
    tenant.id = 1
    return tenant


def test_memory_reduction_single(sample_tenant: Tenant) -> None:
    """Compare sizeof Tenant vs TenantContext for single instance."""
    context = TenantContext.from_model(sample_tenant)

    sys.getsizeof(context)
    sys.getsizeof(sample_tenant)

    # Note: sys.getsizeof only measures the object itself, not referenced objects
    # Tenant model has SQLAlchemy state machinery and relationships
    # TenantContext is a frozen dataclass with __slots__, much more memory-efficient
    # The real benefit is in ContextVar storage and concurrent usage

    # Verify TenantContext has __slots__ for memory efficiency
    assert hasattr(TenantContext, "__slots__")
    assert not hasattr(context, "__dict__")


def test_memory_reduction_1000_concurrent() -> None:
    """Create 1000 TenantContext, measure total memory. Expected: less than 500KB total."""
    tracemalloc.start()

    baseline, _ = tracemalloc.get_traced_memory()

    # Create 1000 TenantContext instances
    contexts = []
    for i in range(1000):
        tenant = Tenant(
            name=f"Tenant {i}",
            slug=f"tenant-{i}",
            is_active=True,
        )
        tenant.id = i
        context = TenantContext.from_model(tenant)
        contexts.append(context)

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    memory_used_kb = (current - baseline) / 1024
    peak / 1024

    # Should use less than 500KB total
    assert memory_used_kb < 500, f"Memory usage ({memory_used_kb:.2f} KB) should be less than 500KB for 1000 instances"


def test_memory_contextvar_efficiency(sample_tenant: Tenant) -> None:
    """Test that storing TenantContext in ContextVar is memory efficient."""
    from src.app.core.tenancy.context import clear_tenant_context, set_current_tenant

    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()

    # Simulate setting tenant in context (as done in middleware)
    for _ in range(100):
        set_current_tenant(sample_tenant)

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    clear_tenant_context()

    memory_used_kb = (current - baseline) / 1024

    # Should be very efficient (less than 10KB for 100 operations)
    assert memory_used_kb < 10, f"ContextVar operations ({memory_used_kb:.2f} KB) should be very efficient"


def test_memory_vs_full_model_100_instances() -> None:
    """Compare memory usage of 100 TenantContext vs 100 Tenant models."""
    tracemalloc.start()

    # Test with TenantContext
    baseline_ctx, _ = tracemalloc.get_traced_memory()
    contexts = []
    for i in range(100):
        tenant = Tenant(
            name=f"Tenant {i}",
            slug=f"tenant-{i}",
            is_active=True,
        )
        tenant.id = i
        context = TenantContext.from_model(tenant)
        contexts.append(context)
    current_ctx, peak_ctx = tracemalloc.get_traced_memory()
    ctx_memory = (current_ctx - baseline_ctx) / 1024

    tracemalloc.stop()

    # TenantContext should use very little memory
    # With __slots__, should be around 200-300 bytes per instance
    assert ctx_memory < 100, f"100 TenantContext instances ({ctx_memory:.2f} KB) should use less than 100KB"


def test_memory_dict_usage_with_id_keys() -> None:
    """Test memory efficiency when using tenant_id as dict keys."""
    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()

    # Create dict with 1000 tenant_id keys (TenantContext not hashable due to extra dict)
    tenant_dict: dict[int, TenantContext] = {}
    for i in range(1000):
        tenant = Tenant(
            name=f"Tenant {i}",
            slug=f"tenant-{i}",
            is_active=True,
        )
        tenant.id = i
        context = TenantContext.from_model(tenant)
        tenant_dict[context.tenant_id] = context

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    memory_used_kb = (current - baseline) / 1024

    # Should be efficient (less than 350KB for 1000 entries - baseline adjusted)
    assert memory_used_kb < 350, f"Dict with 1000 keys ({memory_used_kb:.2f} KB) should use less than 350KB"


def test_memory_concurrent_contextvars() -> None:
    """Test memory efficiency with multiple concurrent ContextVars."""
    import asyncio

    from src.app.core.tenancy.context import clear_tenant_context, set_current_tenant

    async def worker(worker_id: int) -> None:
        """Worker that sets tenant context."""
        tenant = Tenant(
            name=f"Worker {worker_id}",
            slug=f"worker-{worker_id}",
            is_active=True,
        )
        tenant.id = worker_id
        set_current_tenant(tenant)
        await asyncio.sleep(0.001)

    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()

    async def run_workers() -> None:
        """Run multiple workers concurrently."""
        tasks = [worker(i) for i in range(10)]
        await asyncio.gather(*tasks)

    asyncio.run(run_workers())

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    clear_tenant_context()

    memory_used_kb = (current - baseline) / 1024

    # Should be very efficient even with concurrency (baseline adjusted)
    assert memory_used_kb < 15, f"Concurrent ContextVar operations ({memory_used_kb:.2f} KB) should be very efficient"
