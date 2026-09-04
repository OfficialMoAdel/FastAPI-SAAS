"""Tier API router - delegates to deep RateLimitService."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastcrud import PaginatedListResponse

from src.app.api.deps import get_current_superuser
from src.app.core.schemas import MessageResponse
from src.app.modules.rate_limits.schemas import TierCreate, TierRead, TierUpdate
from src.app.modules.rate_limits.service import RateLimitService, get_rate_limit_service

router = APIRouter(tags=["tiers"])


@router.post(
    "/tiers",
    response_model=TierRead,
    dependencies=[Depends(get_current_superuser)],
    status_code=status.HTTP_201_CREATED,
)
async def write_tier(
    request: Request,
    tier: TierCreate,
    service: Annotated[RateLimitService, Depends(get_rate_limit_service)],
) -> Any:
    """Create a new tier (superuser only)."""
    return await service.create_tier(tier)


@router.get("/tiers", response_model=PaginatedListResponse[TierRead])
async def read_tiers(
    request: Request,
    service: Annotated[RateLimitService, Depends(get_rate_limit_service)],
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(10, ge=1, le=100, description="Items per page"),
) -> Any:
    """Get paginated list of tiers."""
    return await service.get_tiers(page, per_page)


@router.get("/tiers/{name}", response_model=TierRead)
async def read_tier(
    request: Request,
    name: str,
    service: Annotated[RateLimitService, Depends(get_rate_limit_service)],
) -> Any:
    """Get tier by name."""
    return await service.get_tier(name)


@router.patch("/tiers/{name}", response_model=MessageResponse, dependencies=[Depends(get_current_superuser)])
async def patch_tier(
    request: Request,
    name: str,
    values: TierUpdate,
    service: Annotated[RateLimitService, Depends(get_rate_limit_service)],
) -> Any:
    """Update a tier (superuser only)."""
    return await service.update_tier(name, values)


@router.delete("/tiers/{name}", response_model=MessageResponse, dependencies=[Depends(get_current_superuser)])
async def erase_tier(
    request: Request,
    name: str,
    service: Annotated[RateLimitService, Depends(get_rate_limit_service)],
) -> Any:
    """Delete a tier (superuser only)."""
    return await service.delete_tier(name)
