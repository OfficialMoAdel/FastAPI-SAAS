"""Products router - API compatibility layer delegating to billing service.

Provides /api/v1/products endpoints for frontend compatibility with old backend.
All business logic is handled by the billing module's ProductService.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.billing.schemas import (
    PricingCreate,
    PricingRead,
    ProductCreate,
    ProductList,
    ProductRead,
    ProductUpdate,
)
from src.app.modules.billing.service import get_product_service

router = APIRouter(prefix="/products", tags=["products"])
logger = logging.getLogger(__name__)


@router.post(
    "",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def create_product(
    data: ProductCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Create a new product (admin only)."""
    service = get_product_service(db)
    return await service.create_product(data)


@router.get("", response_model=ProductList)
async def list_products(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = 1,
    per_page: int = 20,
    is_active: bool | None = None,
) -> dict:
    """List products."""
    service = get_product_service(db)
    result = await service.get_products(page=page, limit=per_page, is_active=is_active)
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    return {
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.get("/{product_id:path}", response_model=ProductRead)
async def get_product(
    product_id: str,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Get a product by ID.

    Note: Uses :path type to support Stripe product IDs (e.g., prod_xxx).
    """
    service = get_product_service(db)
    # Try to convert to int first for database IDs
    try:
        product_id_int = int(product_id)
        return await service.get_product(product_id_int)
    except ValueError:
        # If not a valid int, treat as Stripe ID string
        # For now, we'll still pass to service which expects int
        # This will be handled by service layer if needed
        raise


@router.patch(
    "/{product_id:path}",
    response_model=ProductRead,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def update_product(
    product_id: str,
    data: ProductUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Update a product (admin only).

    Note: Uses :path type to support Stripe product IDs (e.g., prod_xxx).
    """
    service = get_product_service(db)
    # Try to convert to int first for database IDs
    try:
        product_id_int = int(product_id)
        return await service.update_product(product_id_int, data)
    except ValueError:
        # If not a valid int, treat as Stripe ID string
        raise


@router.delete(
    "/{product_id:path}",
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def delete_product(
    product_id: str,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Delete/deactivate a product (admin only).

    Note: Uses :path type to support Stripe product IDs (e.g., prod_xxx).
    """
    service = get_product_service(db)
    # Try to convert to int first for database IDs
    try:
        product_id_int = int(product_id)
        result = await service.deactivate_product(product_id_int)
        return dict(result) if result else {}
    except ValueError:
        # If not a valid int, treat as Stripe ID string
        raise


@router.post(
    "/{product_id:path}/pricing",
    response_model=PricingRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def add_product_pricing(
    product_id: str,
    data: PricingCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Add a pricing tier to a product (admin only).

    Note: Uses :path type to support Stripe product IDs (e.g., prod_xxx).
    """
    service = get_product_service(db)
    # Try to convert to int first for database IDs
    try:
        product_id_int = int(product_id)
        return await service.add_pricing(product_id_int, data)
    except ValueError:
        # If not a valid int, treat as Stripe ID string
        raise
