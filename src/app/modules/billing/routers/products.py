"""Products router - API compatibility layer delegating to billing service.

Provides /api/v1/products endpoints for frontend compatibility with old backend.
All business logic is handled by the billing module's ProductService.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep
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
    ctx: RequestContextDep,
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    is_active: bool | None = None,
) -> dict:
    """List products.

    Run-3 N-2 (``billing.catalog_reads_scope_from_unauthenticated_header``):
    product rows are CONFIDENTIAL tenant billing data, not a public catalog.
    This route previously took only a DB session, so its tenant scope came
    solely from the caller-supplied ``X-Tenant-ID`` header and any anonymous
    caller who guessed a slug could read that tenant's catalog.
    ``RequestContextDep`` requires authentication AND resolves the tenant from
    the authenticated principal (with ``_enforce_user_tenant_match`` rejecting a
    header that disagrees with the caller's own tenant).
    """
    service = get_product_service(ctx.db)
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


@router.get("/{product_id:int}", response_model=ProductRead)
async def get_product(
    product_id: int,
    ctx: RequestContextDep,
) -> dict:
    """Get a product by integer ID.

    Run-3 N-2: confidential tenant data; requires an authenticated principal
    whose tenant matches the resolved context.
    """
    service = get_product_service(ctx.db)
    return await service.get_product(product_id)


@router.patch(
    "/{product_id:int}",
    response_model=ProductRead,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def update_product(
    product_id: int,
    data: ProductUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Update a product (admin only)."""
    service = get_product_service(db)
    return await service.update_product(product_id, data)


@router.delete(
    "/{product_id:int}",
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def delete_product(
    product_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Delete/deactivate a product (admin only)."""
    service = get_product_service(db)
    result = await service.deactivate_product(product_id)
    return dict(result) if result else {}


@router.post(
    "/{product_id:int}/pricing",
    response_model=PricingRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def add_product_pricing(
    product_id: int,
    data: PricingCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Add a pricing tier to a product (admin only)."""
    service = get_product_service(db)
    return await service.add_pricing(product_id, data)
