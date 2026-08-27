"""Rate limit API router - HTTP handlers delegating to service layer."""

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastcrud import PaginatedListResponse

from src.app.api.deps import get_current_superuser
from src.app.core.schemas import MessageResponse
from src.app.modules.rate_limits.schemas import RateLimitCreate, RateLimitRead, RateLimitUpdate
from src.app.modules.rate_limits.service import RateLimitService, get_rate_limit_service

router = APIRouter(tags=["rate_limits"])


@router.post(
    "/tiers/{tier_name}/rate-limits",
    response_model=RateLimitRead,
    dependencies=[Depends(get_current_superuser)],
    status_code=status.HTTP_201_CREATED,
)
async def write_rate_limit(
    request: Request,
    tier_name: str,
    rate_limit: RateLimitCreate,
    service: RateLimitService = Depends(get_rate_limit_service),
) -> Any:
    """Create a new rate limit for a tier (superuser only)."""
    return await service.create_rate_limit(tier_name, rate_limit)


@router.get("/tiers/{tier_name}/rate-limits", response_model=PaginatedListResponse[RateLimitRead])
async def read_rate_limits(
    request: Request,
    tier_name: str,
    service: RateLimitService = Depends(get_rate_limit_service),
    page: int = 1,
    per_page: int = 10,
) -> Any:
    """Get paginated rate limits for a tier."""
    return await service.get_rate_limits(tier_name, page, per_page)


@router.get("/tiers/{tier_name}/rate-limits/{id}", response_model=RateLimitRead)
async def read_rate_limit(
    request: Request,
    tier_name: str,
    id: int,
    service: RateLimitService = Depends(get_rate_limit_service),
) -> Any:
    """Get a single rate limit by ID."""
    return await service.get_rate_limit(tier_name, id)


@router.patch(
    "/tiers/{tier_name}/rate-limits/{id}",
    response_model=MessageResponse,
    dependencies=[Depends(get_current_superuser)],
)
async def patch_rate_limit(
    request: Request,
    tier_name: str,
    id: int,
    values: RateLimitUpdate,
    service: RateLimitService = Depends(get_rate_limit_service),
) -> Any:
    """Update a rate limit (superuser only)."""
    return await service.update_rate_limit(tier_name, id, values)


@router.delete(
    "/tiers/{tier_name}/rate-limits/{id}",
    response_model=MessageResponse,
    dependencies=[Depends(get_current_superuser)],
)
async def erase_rate_limit(
    request: Request,
    tier_name: str,
    id: int,
    service: RateLimitService = Depends(get_rate_limit_service),
) -> Any:
    """Delete a rate limit (superuser only)."""
    return await service.delete_rate_limit(tier_name, id)
