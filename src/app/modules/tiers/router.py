"""Tier API router - served by the canonical TierService."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastcrud import PaginatedListResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep, get_current_superuser
from src.app.core.db.database import async_get_db
from src.app.core.schemas import MessageResponse
from src.app.modules.tiers.schemas import TierCreate, TierRead, TierUpdate
from src.app.modules.tiers.service import TierService

router = APIRouter(tags=["tiers"])


def get_tier_service(db: AsyncSession = Depends(async_get_db)) -> TierService:
    """Dependency injection factory for TierService.

    Q-M4: factories live in router.py — service files stay Depends-free.
    """
    return TierService(db)


@router.post(
    "/tiers",
    response_model=TierRead,
    dependencies=[Depends(get_current_superuser)],
    status_code=status.HTTP_201_CREATED,
)
async def write_tier(
    request: Request,
    tier: TierCreate,
    service: Annotated[TierService, Depends(get_tier_service)],
) -> Any:
    """Create a new tier (superuser only)."""
    return await service.create_tier(tier)


@router.get("/tiers", response_model=PaginatedListResponse[TierRead])
async def read_tiers(
    ctx: RequestContextDep,
    service: Annotated[TierService, Depends(get_tier_service)],
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(10, ge=1, le=100, description="Items per page"),
) -> Any:
    """Get paginated list of tiers (authenticated).

    Run-3 pgo0.1 (tiers.catalog_reads_are_anonymous): every MUTATING route in
    this router is superuser-gated while both reads carried no auth at all, so
    the whole catalog was reachable by any anonymous caller. ``RequestContextDep``
    is the billing-catalog treatment (``billing/routers/products.py``, pinned by
    ``test_legacy_catalog_auth.py``): require an authenticated principal and
    resolve the tenant from it rather than from a caller-supplied header.

    Authentication — not a superuser gate — is the correct bar: these rows are
    the GLOBAL tier catalog (``Tier`` has no ``tenant_id``) that the caller's own
    users are already subject to, so any principal may read them. That also keeps
    the reads strictly less privileged than their superuser-gated siblings.
    """
    return await service.get_tiers(page, per_page)


@router.get("/tiers/{name}", response_model=TierRead)
async def read_tier(
    ctx: RequestContextDep,
    name: str,
    service: Annotated[TierService, Depends(get_tier_service)],
) -> Any:
    """Get tier by name (authenticated).

    Same ``RequestContextDep`` gate as ``read_tiers`` — see its docstring for
    why the catalog is authenticated rather than superuser-gated.
    """
    return await service.get_tier(name)


@router.patch("/tiers/{name}", response_model=MessageResponse, dependencies=[Depends(get_current_superuser)])
async def patch_tier(
    request: Request,
    name: str,
    values: TierUpdate,
    service: Annotated[TierService, Depends(get_tier_service)],
) -> Any:
    """Update a tier (superuser only)."""
    return await service.update_tier(name, values)


@router.delete("/tiers/{name}", response_model=MessageResponse, dependencies=[Depends(get_current_superuser)])
async def erase_tier(
    request: Request,
    name: str,
    service: Annotated[TierService, Depends(get_tier_service)],
) -> Any:
    """Delete a tier (superuser only)."""
    return await service.delete_tier(name)
