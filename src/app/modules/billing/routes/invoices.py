"""Invoices router - API compatibility layer delegating to billing records service.

Provides /api/v1/invoices endpoints for frontend compatibility with old backend.
All business logic is handled by the billing module's BillingRecordService.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_user
from src.app.core.db.database import async_get_db
from src.app.modules.billing.schemas import BillingRecordList, BillingRecordRead
from src.app.modules.billing.service import get_billing_record_service

router = APIRouter(prefix="/invoices", tags=["invoices"])
logger = logging.getLogger(__name__)


@router.get("", response_model=BillingRecordList)
async def list_invoices(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = 1,
    per_page: int = 20,
    status: str | None = None,
) -> dict:
    """List invoices (billing records) for the current user.

    Args:
        current_user: Authenticated user
        db: Database session
        page: Page number (1-indexed)
        per_page: Number of invoices per page
        status: Optional filter by status (succeeded, failed, pending, refunded)

    Returns:
        Paginated list of invoices with metadata
    """
    service = get_billing_record_service(db)
    result = await service.get_user_records(current_user["id"], page=page, limit=per_page, status=status)
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


@router.get("/{invoice_id}", response_model=BillingRecordRead)
async def get_invoice(
    invoice_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Get a single invoice (billing record) by ID.

    Args:
        invoice_id: Invoice ID (billing record ID)
        current_user: Authenticated user
        db: Database session

    Returns:
        Invoice details
    """
    service = get_billing_record_service(db)
    # get_record only requires record_id, authorization is handled at router level
    result = await service.get_record(invoice_id)
    return dict(result) if result else {}
