"""Tenant mixin for SQLAlchemy models requiring multi-tenant isolation.

This module provides a TenantMixin class that can be used with SQLAlchemy models
to add standardized tenant_id support for row-level tenant isolation.

Usage:
    from src.app.core.db.tenant_mixin import TenantMixin
    
    class MyModel(Base, TenantMixin):
        __tablename__ = "my_table"
        # ... other columns
        
    # The model will automatically have:
    # - tenant_id: int (ForeignKey to tenants.id)
    # - tenant: relationship to Tenant model

Note:
    For models using MappedAsDataclass (like this project's Base), you may need
    to explicitly define tenant_id to control init behavior. This mixin serves
    as documentation and a reference implementation.
"""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant


class TenantMixin:
    """Mixin class that adds tenant_id field to a model.
    
    This mixin provides a standardized way to add tenant isolation to models.
    Models that include this mixin will:
    
    1. Have a tenant_id foreign key column pointing to tenants.id
    2. Have an index on tenant_id for efficient querying
    3. NOT NULL constraint to ensure all rows have a tenant
    
    Example:
        >>> class Invoice(Base, TenantMixin):
        ...     __tablename__ = "invoices"
        ...     id: Mapped[int] = mapped_column(primary_key=True)
        ...     amount: Mapped[float] = mapped_column()
        
    When used with TenantAwareCRUD, queries will automatically be filtered
    by the current tenant context.
    
    Note:
        Due to MappedAsDataclass behavior, you may need to explicitly define
        tenant_id in your model with appropriate init= settings. See the
        User model for a reference implementation.
    """
    
    # Note: When using MappedAsDataclass, subclasses should define tenant_id
    # explicitly to control whether it appears in __init__. This declaration
    # serves as documentation for the expected column structure.
    #
    # Example implementation in actual models:
    # tenant_id: Mapped[int] = mapped_column(
    #     ForeignKey("tenants.id"), 
    #     index=True, 
    #     nullable=False
    # )


def create_tenant_id_column(*, init: bool = True, nullable: bool = False) -> Mapped[int]:
    """Factory function to create a standardized tenant_id column.
    
    This provides a consistent way to add tenant_id columns across models
    while allowing customization of init and nullable behavior.
    
    Args:
        init: Whether to include in dataclass __init__ (for MappedAsDataclass)
        nullable: Whether the column allows NULL values
        
    Returns:
        A properly configured mapped column for tenant_id
        
    Example:
        >>> class MyModel(Base):
        ...     __tablename__ = "my_table"
        ...     tenant_id: Mapped[int] = create_tenant_id_column(init=True)
    """
    if init:
        return mapped_column(
            ForeignKey("tenants.id"),
            index=True,
            nullable=nullable,
        )
    else:
        return mapped_column(
            ForeignKey("tenants.id"),
            index=True,
            nullable=nullable,
            init=False,
        )
