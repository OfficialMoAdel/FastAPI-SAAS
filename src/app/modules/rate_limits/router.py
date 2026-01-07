"""Rate limit API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastcrud import PaginatedListResponse

from ...api.dependencies import get_current_superuser
from ...core.db.database import async_get_db
from .schemas import RateLimitCreate, RateLimitRead, RateLimitUpdate
from .service import RateLimitService, get_rate_limit_service

router = APIRouter(tags=["rate_limits"])

# Type alias for dependency injection
RateLimitServiceDep = Annotated[RateLimitService, Depends(lambda db=Depends(async_get_db): get_rate_limit_service(db))]


@router.post("/tier/{tier_name}/rate_limit", dependencies=[Depends(get_current_superuser)], status_code=201)
async def write_rate_limit(
    request: Request, tier_name: str, rate_limit: RateLimitCreate, service: RateLimitServiceDep
) -> dict[str, Any]:
    """Create a new rate limit for a tier (superuser only)."""
    return await service.create_rate_limit(tier_name, rate_limit)


@router.get("/tier/{tier_name}/rate_limits", response_model=PaginatedListResponse[RateLimitRead])
async def read_rate_limits(
    request: Request,
    tier_name: str,
    service: RateLimitServiceDep,
    page: int = 1,
    items_per_page: int = 10,
) -> dict:
    """Get paginated rate limits for a tier."""
    return await service.get_rate_limits(tier_name, page, items_per_page)


@router.get("/tier/{tier_name}/rate_limit/{id}", response_model=RateLimitRead)
async def read_rate_limit(request: Request, tier_name: str, id: int, service: RateLimitServiceDep) -> dict[str, Any]:
    """Get a single rate limit by ID."""
    return await service.get_rate_limit(tier_name, id)


@router.patch("/tier/{tier_name}/rate_limit/{id}", dependencies=[Depends(get_current_superuser)])
async def patch_rate_limit(
    request: Request,
    tier_name: str,
    id: int,
    values: RateLimitUpdate,
    service: RateLimitServiceDep,
) -> dict[str, str]:
    """Update a rate limit (superuser only)."""
    return await service.update_rate_limit(tier_name, id, values)


@router.delete("/tier/{tier_name}/rate_limit/{id}", dependencies=[Depends(get_current_superuser)])
async def erase_rate_limit(request: Request, tier_name: str, id: int, service: RateLimitServiceDep) -> dict[str, str]:
    """Delete a rate limit (superuser only)."""
    return await service.delete_rate_limit(tier_name, id)
