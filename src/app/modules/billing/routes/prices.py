"""Prices router - API compatibility layer delegating to billing PricingService.

Provides /api/v1/prices endpoints for frontend compatibility with old backend.
All business logic is handled by the billing module's PricingService.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.logger import get_logger
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.billing.schemas import PricingCreate, PricingRead, PricingUpdate
from src.app.modules.billing.service import PricingService

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


@router.get("", response_model=list[PricingRead])
async def list_prices(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    product_id: Annotated[int | None, Query(description="Filter by product ID")] = None,
    is_active: Annotated[bool | None, Query(description="Filter by active status")] = None,
) -> list[dict]:
    """List prices with optional filtering.

    Args:
        db: Database session
        product_id: Optional product ID filter
        is_active: Optional active status filter

    Returns:
        List of prices
    """
    service = PricingService(db)
    result = await service.list_prices(limit=100, product_id=product_id, is_active=is_active)
    data = result.get("data", [])
    return list(data) if data else []


@router.get("/{price_id}", response_model=PricingRead)
async def get_price(
    price_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Get a price by ID.

    Args:
        price_id: Price ID (database integer ID)
        db: Database session

    Returns:
        Price details
    """
    service = PricingService(db)
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
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def delete_price(
    price_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
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
    return {"message": "Price deactivated successfully", "id": price_id}
