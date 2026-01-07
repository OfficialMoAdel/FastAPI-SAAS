"""Tier API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastcrud import PaginatedListResponse

from ...api.dependencies import get_current_superuser
from ...core.db.database import async_get_db
from .schemas import TierCreate, TierRead, TierUpdate
from .service import TierService, get_tier_service

router = APIRouter(tags=["tiers"])


@router.post("/tier", dependencies=[Depends(get_current_superuser)], status_code=201)
async def write_tier(
    request: Request,
    tier: TierCreate,
    service: Annotated[TierService, Depends(lambda db=Depends(async_get_db): get_tier_service(db))],
) -> dict[str, Any]:
    """Create a new tier (superuser only)."""
    return await service.create_tier(tier)


@router.get("/tiers", response_model=PaginatedListResponse[TierRead])
async def read_tiers(
    request: Request,
    service: Annotated[TierService, Depends(lambda db=Depends(async_get_db): get_tier_service(db))],
    page: int = 1,
    items_per_page: int = 10,
) -> dict:
    """Get paginated list of tiers."""
    return await service.get_tiers(page, items_per_page)


@router.get("/tier/{name}", response_model=TierRead)
async def read_tier(
    request: Request,
    name: str,
    service: Annotated[TierService, Depends(lambda db=Depends(async_get_db): get_tier_service(db))],
) -> dict[str, Any]:
    """Get tier by name."""
    return await service.get_tier(name)


@router.patch("/tier/{name}", dependencies=[Depends(get_current_superuser)])
async def patch_tier(
    request: Request,
    name: str,
    values: TierUpdate,
    service: Annotated[TierService, Depends(lambda db=Depends(async_get_db): get_tier_service(db))],
) -> dict[str, str]:
    """Update a tier (superuser only)."""
    return await service.update_tier(name, values)


@router.delete("/tier/{name}", dependencies=[Depends(get_current_superuser)])
async def erase_tier(
    request: Request,
    name: str,
    service: Annotated[TierService, Depends(lambda db=Depends(async_get_db): get_tier_service(db))],
) -> dict[str, str]:
    """Delete a tier (superuser only)."""
    return await service.delete_tier(name)
