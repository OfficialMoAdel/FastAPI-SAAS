"""Prices router - API compatibility layer delegating to billing PricingService.

Provides /api/v1/prices endpoints for frontend compatibility with old backend.
All business logic is handled by the billing module's PricingService.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep
from src.app.core.db.database import async_get_db
from src.app.core.logger import get_logger
from src.app.core.pagination import PaginationParams, build_paginated_response, get_pagination_params
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.billing.schemas import PricingCreate, PricingList, PricingRead, PricingUpdate
from src.app.modules.billing.service import PricingService
from src.app.schemas.responses import MessageWithIdResponse

router = APIRouter(prefix="/prices", tags=["prices"])
logger = get_logger(__name__)


@router.post(
    "",
    response_model=PricingRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def create_price(
    data: PricingCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    product_id: Annotated[int, Query(description="Product ID to associate price with")],
) -> dict:
    """Create a new price for a product (admin only).

    Args:
        data: Price creation data
        db: Database session
        product_id: Product ID to associate the price with

    Returns:
        Created price details
    """
    service = PricingService(db)
    return await service.create_pricing(product_id, data)


@router.get("", response_model=PricingList)
async def list_prices(
    ctx: RequestContextDep,
    pagination: Annotated[PaginationParams, Depends(get_pagination_params)],
    product_id: Annotated[int | None, Query(description="Filter by product ID")] = None,
    is_active: Annotated[bool | None, Query(description="Filter by active status")] = None,
) -> dict:
    """List prices with optional filtering (paginated).

    Run-3 N-2: pricing rows are CONFIDENTIAL tenant billing data. This route
    previously took ``tenant_dependency`` but no authentication, so an anonymous
    caller could still supply an arbitrary ``X-Tenant-ID`` and read that
    tenant's prices. ``RequestContextDep`` requires an authenticated principal
    and validates the header against the caller's own tenant.

    Args:
        db: Database session
        current_tenant: Current tenant context
        pagination: Standard page/per_page parameters
        product_id: Optional product ID filter
        is_active: Optional active status filter

    Returns:
        Paginated price list
    """
    service = PricingService(ctx.db)
    result = await service.list_prices(
        page=pagination.page,
        limit=pagination.limit,
        product_id=product_id,
        is_active=is_active,
        tenant_id=str(ctx.tenant_id),
    )
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    return build_paginated_response(items=data, total=total, page=pagination.page, per_page=pagination.per_page)


@router.get("/{price_id}", response_model=PricingRead)
async def get_price(
    price_id: int,
    ctx: RequestContextDep,
) -> dict:
    """Get a price by ID.

    Run-3 N-2: this route took only a DB session. The service loaded the pricing
    row by id with no tenant filter and then validated only that the PARENT
    PRODUCT belonged to the header-derived tenant, so an anonymous caller who
    guessed a slug could read that tenant's pricing rows. Now requires an
    authenticated principal.

    Args:
        price_id: Price ID (database integer ID)

    Returns:
        Price details
    """
    service = PricingService(ctx.db)
    return await service.get_pricing(price_id)


@router.patch(
    "/{price_id}",
    response_model=PricingRead,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def update_price(
    price_id: int,
    data: PricingUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Update a price (admin only).

    Note: Some Stripe price fields are immutable after creation.

    Args:
        price_id: Price ID (database integer ID)
        data: Price update data
        db: Database session

    Returns:
        Updated price details
    """
    service = PricingService(db)
    return await service.update_pricing(price_id, data)


@router.delete(
    "/{price_id}",
    response_model=MessageWithIdResponse,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def delete_price(
    price_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> MessageWithIdResponse:
    """Deactivate a price (admin only).

    Prices are soft-deleted by setting is_active=False.

    Args:
        price_id: Price ID (database integer ID)
        db: Database session

    Returns:
        Success message
    """
    service = PricingService(db)
    # Soft delete by setting is_active=False
    update_data = PricingUpdate(is_active=False)
    await service.update_pricing(price_id, update_data)
    return MessageWithIdResponse(message="Price deactivated successfully", id=price_id)
