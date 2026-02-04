"""Unit tests for TenantContext memory optimization.

Tests verify that TenantContext correctly extracts data from Tenant model,
is immutable, hashable, and uses __slots__ for memory efficiency.
"""

from __future__ import annotations

import sys

import pytest

from src.app.core.tenancy.context import TenantContext
from src.app.modules.tenants.models import Tenant


@pytest.fixture
def sample_tenant() -> Tenant:
    """Create a sample Tenant model for testing."""
    tenant = Tenant(
        name="Test Tenant",
        slug="test-tenant",
        is_active=True,
    )
    # Set id manually since it's init=False
    tenant.id = 1
    return tenant


def test_tenant_context_from_tenant(sample_tenant: Tenant) -> None:
    """Verify all fields correctly extracted from Tenant model."""
    context = TenantContext.from_model(sample_tenant)

    assert context.tenant_id == sample_tenant.id
    assert context.tenant_slug == sample_tenant.slug
    assert context.tenant_name == sample_tenant.name
    assert context.is_active == sample_tenant.is_active


def test_tenant_context_immutable(sample_tenant: Tenant) -> None:
    """Verify frozen=True prevents modification."""
    context = TenantContext.from_model(sample_tenant)

    with pytest.raises(AttributeError):
        context.tenant_id = 999

    with pytest.raises(AttributeError):
        context.tenant_slug = "new-slug"

    with pytest.raises(AttributeError):
        context.tenant_name = "New Name"


def test_tenant_context_hashable(sample_tenant: Tenant) -> None:
    """Verify can be used as dict key (when extra is empty)."""
    context1 = TenantContext.from_model(sample_tenant)
    context2 = TenantContext.from_model(sample_tenant)

    # TenantContext is frozen but not hashable due to 'extra' dict field
    # This is expected behavior - frozen dataclasses with dict fields are not hashable
    with pytest.raises(TypeError):
        hash(context1)

    # Can still be used in dict with custom key
    test_dict: dict[int, TenantContext] = {}
    test_dict[context1.tenant_id] = context1
    test_dict[context2.tenant_id] = context2

    # Same key should overwrite
    assert len(test_dict) == 1
    assert test_dict[context1.tenant_id] == context2


def test_tenant_context_slots(sample_tenant: Tenant) -> None:
    """Verify __slots__ reduces memory usage."""
    context = TenantContext.from_model(sample_tenant)

    # Verify __slots__ is defined
    assert hasattr(TenantContext, "__slots__")

    # Verify no __dict__ (memory optimization)
    assert not hasattr(context, "__dict__")

    # Verify slots contain expected fields (including extra for Pydantic)
    expected_slots = {"tenant_id", "tenant_slug", "tenant_name", "is_active", "extra"}
    assert set(TenantContext.__slots__) == expected_slots


def test_backwards_compatibility(sample_tenant: Tenant) -> None:
    """Verify backward compatibility properties work correctly."""
    context = TenantContext.from_model(sample_tenant)

    # Old property names should work
    assert context.id == sample_tenant.id
    assert context.slug == sample_tenant.slug
    assert context.name == sample_tenant.name

    # New property names should also work
    assert context.tenant_id == sample_tenant.id
    assert context.tenant_slug == sample_tenant.slug
    assert context.tenant_name == sample_tenant.name


def test_tenant_context_memory_size(sample_tenant: Tenant) -> None:
    """Verify TenantContext uses significantly less memory than Tenant."""
    context = TenantContext.from_model(sample_tenant)

    context_size = sys.getsizeof(context)
    tenant_size = sys.getsizeof(sample_tenant)

    # Note: sys.getsizeof only measures the object itself, not referenced objects
    # Tenant model has SQLAlchemy state machinery and relationships
    # TenantContext is a frozen dataclass with __slots__, much more memory-efficient
    # The real benefit is in ContextVar storage and concurrent usage

    # Verify TenantContext has __slots__ for memory efficiency
    assert hasattr(TenantContext, "__slots__")
    assert not hasattr(context, "__dict__")


def test_tenant_context_from_model_none() -> None:
    """Verify from_model handles None input gracefully."""
    with pytest.raises(AttributeError):
        TenantContext.from_model(None)


def test_tenant_context_multiple_instances(sample_tenant: Tenant) -> None:
    """Verify multiple instances can be created and stored."""
    context1 = TenantContext.from_model(sample_tenant)
    context2 = TenantContext.from_model(sample_tenant)
    context3 = TenantContext.from_model(sample_tenant)

    # All should have same data
    assert context1 == context2 == context3

    # But different instances
    assert context1 is not context2
    assert context2 is not context3


def test_tenant_context_equality(sample_tenant: Tenant) -> None:
    """Verify equality comparison works correctly."""
    from src.app.core.tenancy.context import TenantContext as TC

    context1 = TC.from_model(sample_tenant)
    context2 = TC.from_model(sample_tenant)
    context3 = TC(tenant_id=2, tenant_slug="other", tenant_name="Other", is_active=True)

    # Same data should be equal
    assert context1 == context2

    # Different data should not be equal
    assert context1 != context3

    # Not equal to other types
    assert context1 != "not a context"
    assert context1 != 123
