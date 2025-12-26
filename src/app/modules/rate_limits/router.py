"""Rate limit API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastcrud import PaginatedListResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_superuser
from ...core.db.database import async_get_db
from .schemas import RateLimitCreate, RateLimitRead, RateLimitUpdate
from .service import rate_limit_service

router = APIRouter(tags=["rate_limits"])


@router.post("/tier/{tier_name}/rate_limit", dependencies=[Depends(get_current_superuser)], status_code=201)
async def write_rate_limit(
    request: Request, tier_name: str, rate_limit: RateLimitCreate, db: Annotated[AsyncSession, Depends(async_get_db)]
) -> dict[str, Any]:
    """Create a new rate limit for a tier (superuser only)."""
    return await rate_limit_service.create_rate_limit(tier_name, rate_limit, db)


@router.get("/tier/{tier_name}/rate_limits", response_model=PaginatedListResponse[RateLimitRead])
async def read_rate_limits(
    request: Request,
    tier_name: str,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = 1,
    items_per_page: int = 10,
) -> dict:
    """Get paginated rate limits for a tier."""
    return await rate_limit_service.get_rate_limits(tier_name, db, page, items_per_page)


@router.get("/tier/{tier_name}/rate_limit/{id}", response_model=RateLimitRead)
async def read_rate_limit(
    request: Request, tier_name: str, id: int, db: Annotated[AsyncSession, Depends(async_get_db)]
) -> dict[str, Any]:
    """Get a single rate limit by ID."""
    return await rate_limit_service.get_rate_limit(tier_name, id, db)


@router.patch("/tier/{tier_name}/rate_limit/{id}", dependencies=[Depends(get_current_superuser)])
async def patch_rate_limit(
    request: Request,
    tier_name: str,
    id: int,
    values: RateLimitUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str]:
    """Update a rate limit (superuser only)."""
    return await rate_limit_service.update_rate_limit(tier_name, id, values, db)


@router.delete("/tier/{tier_name}/rate_limit/{id}", dependencies=[Depends(get_current_superuser)])
async def erase_rate_limit(
    request: Request, tier_name: str, id: int, db: Annotated[AsyncSession, Depends(async_get_db)]
) -> dict[str, str]:
    """Delete a rate limit (superuser only)."""
    return await rate_limit_service.delete_rate_limit(tier_name, id, db)
