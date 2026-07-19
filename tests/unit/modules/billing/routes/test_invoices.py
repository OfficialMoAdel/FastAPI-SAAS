"""Unit tests for billing/routes/invoices.py compatibility layer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.modules.billing.routes.invoices import get_invoice, list_invoices


async def test_list_invoices():
    mock_service = MagicMock()
    mock_service.get_user_records = AsyncMock(return_value={"data": [{"id": 1}], "total_count": 1})

    with patch(
        "src.app.modules.billing.routes.invoices.get_billing_record_service",
        return_value=mock_service,
    ):
        result = await list_invoices(current_user={"id": 1}, db=MagicMock(), page=1, per_page=20, status=None)
        assert result["items"] == [{"id": 1}]
        assert result["total"] == 1
        assert result["has_next"] is False
        assert result["has_previous"] is False
        mock_service.get_user_records.assert_awaited_once_with(1, page=1, limit=20, status=None)


async def test_list_invoices_has_next():
    mock_service = MagicMock()
    mock_service.get_user_records = AsyncMock(return_value={"data": [{"id": 1}], "total_count": 50})

    with patch(
        "src.app.modules.billing.routes.invoices.get_billing_record_service",
        return_value=mock_service,
    ):
        result = await list_invoices(current_user={"id": 1}, db=MagicMock(), page=1, per_page=20, status="succeeded")
        assert result["has_next"] is True


async def test_get_invoice_success():
    mock_service = MagicMock()
    mock_service.get_record = AsyncMock(return_value={"id": 5, "user_id": 1})

    with patch(
        "src.app.modules.billing.routes.invoices.get_billing_record_service",
        return_value=mock_service,
    ):
        result = await get_invoice(invoice_id=5, current_user={"id": 1}, db=MagicMock())
        assert result == {"id": 5, "user_id": 1}


async def test_get_invoice_not_found():
    mock_service = MagicMock()
    mock_service.get_record = AsyncMock(return_value=None)

    with patch(
        "src.app.modules.billing.routes.invoices.get_billing_record_service",
        return_value=mock_service,
    ):
        with pytest.raises(HTTPException) as exc:
            await get_invoice(invoice_id=5, current_user={"id": 1}, db=MagicMock())
        assert exc.value.status_code == 404


async def test_get_invoice_forbidden():
    mock_service = MagicMock()
    mock_service.get_record = AsyncMock(return_value={"id": 5, "user_id": 2})

    with patch(
        "src.app.modules.billing.routes.invoices.get_billing_record_service",
        return_value=mock_service,
    ):
        with pytest.raises(HTTPException) as exc:
            await get_invoice(invoice_id=5, current_user={"id": 1}, db=MagicMock())
        assert exc.value.status_code == 403
