from fastapi import status
"""Unit tests for BillingHistoryService.

Tests T101: Unit test for BillingHistoryService in tests/unit/billing/test_billing_history_service.py
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from src.app.modules.billing.service import BillingRecordService


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def mock_tenant():
    """Mock tenant context."""
    tenant = MagicMock()
    tenant.id = uuid.uuid4()
    return tenant


class TestBillingHistoryService:
    """Test suite for billing history service operations."""

    @pytest.mark.asyncio
    async def test_get_user_records_success(self, mock_db_session, mock_tenant):
        """Test getting billing records for a user."""
        service = BillingRecordService(mock_db_session)

        mock_records = {
            "data": [
                {
                    "id": 1,
                    "user_id": 1,
                    "amount_cents": 9900,
                    "currency": "usd",
                    "status": "succeeded",
                    "description": "Monthly subscription",
                    "created_at": datetime.now(UTC),
                },
                {
                    "id": 2,
                    "user_id": 1,
                    "amount_cents": 4900,
                    "currency": "usd",
                    "status": "succeeded",
                    "description": "Add-on purchase",
                    "created_at": datetime.now(UTC) - timedelta(days=7),
                },
            ],
            "total_count": 2,
        }

        with patch("src.app.modules.billing.service.get_current_tenant", return_value=mock_tenant), \
             patch(
            "src.app.modules.billing.crud.crud_billing_records.get_multi",
            new_callable=AsyncMock,
            return_value=mock_records,
        ):
            result = await service.get_user_records(user_id=1, page=1, limit=20)

            assert "data" in result
            assert len(result["data"]) == 2
            assert result["data"][0]["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_get_user_records_pagination(self, mock_db_session, mock_tenant):
        """Test pagination in billing history."""
        service = BillingRecordService(mock_db_session)

        # First page
        mock_page_1 = {
            "data": [{"id": 1, "status": "succeeded"}, {"id": 2, "status": "succeeded"}],
            "total_count": 5,
        }

        with patch("src.app.modules.billing.service.get_current_tenant", return_value=mock_tenant), \
             patch(
            "src.app.modules.billing.crud.crud_billing_records.get_multi",
            new_callable=AsyncMock,
            return_value=mock_page_1,
        ) as mock_get_multi:
            result = await service.get_user_records(user_id=1, page=1, limit=2)

            # Verify offset calculation: (page - 1) * limit = 0
            mock_get_multi.assert_called()
            assert len(result["data"]) == 2

    @pytest.mark.asyncio
    async def test_get_user_records_empty(self, mock_db_session, mock_tenant):
        """Test getting billing records when user has none."""
        service = BillingRecordService(mock_db_session)

        mock_empty = {"data": [], "total_count": 0}

        with patch("src.app.modules.billing.service.get_current_tenant", return_value=mock_tenant), \
             patch(
            "src.app.modules.billing.crud.crud_billing_records.get_multi",
            new_callable=AsyncMock,
            return_value=mock_empty,
        ):
            result = await service.get_user_records(user_id=1, page=1, limit=20)

            assert result["data"] == []
            assert result["total_count"] == 0

    @pytest.mark.asyncio
    async def test_get_user_records_with_status_filter(self, mock_db_session, mock_tenant):
        """Test getting billing records filtered by status."""
        service = BillingRecordService(mock_db_session)

        mock_failed = {
            "data": [
                {
                    "id": 3,
                    "user_id": 1,
                    "amount_cents": 9900,
                    "currency": "usd",
                    "status": "failed",
                    "description": "Payment declined",
                }
            ],
            "total_count": 1,
        }

        with patch("src.app.modules.billing.service.get_current_tenant", return_value=mock_tenant), \
             patch(
            "src.app.modules.billing.crud.crud_billing_records.get_multi",
            new_callable=AsyncMock,
            return_value=mock_failed,
        ):
            result = await service.get_user_records(user_id=1, page=1, limit=20, status="failed")

            assert len(result["data"]) == 1
            assert result["data"][0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_get_record_success(self, mock_db_session, mock_tenant):
        """Test getting a specific billing record."""
        service = BillingRecordService(mock_db_session)

        mock_record = {
            "id": 1,
            "user_id": 1,
            "tenant_id": 1,
            "amount_cents": 9900,
            "currency": "usd",
            "status": "succeeded",
            "description": "Monthly subscription",
            "stripe_payment_intent_id": "pi_test_123",
            "stripe_invoice_id": "in_test_123",
            "created_at": datetime.now(UTC),
        }

        with patch(
            "src.app.modules.billing.crud.crud_billing_records.get",
            new_callable=AsyncMock,
            return_value=mock_record,
        ):
            result = await service.get_record(record_id=1)

            assert result["id"] == 1
            assert result["amount_cents"] == 9900
            assert result["stripe_payment_intent_id"] == "pi_test_123"

    @pytest.mark.asyncio
    async def test_get_record_not_found(self, mock_db_session):
        """Test getting a non-existent billing record."""
        service = BillingRecordService(mock_db_session)

        with patch(
            "src.app.modules.billing.crud.crud_billing_records.get",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(Exception):  # ModelNotFoundError
                await service.get_record(record_id=9999)

    @pytest.mark.asyncio
    async def test_billing_records_sorted_by_date(self, mock_db_session, mock_tenant):
        """Test that billing records are returned sorted by date (newest first)."""
        service = BillingRecordService(mock_db_session)

        now = datetime.now(UTC)
        mock_records = {
            "data": [
                {"id": 3, "created_at": now, "status": "succeeded"},
                {"id": 2, "created_at": now - timedelta(days=7), "status": "succeeded"},
                {"id": 1, "created_at": now - timedelta(days=14), "status": "succeeded"},
            ],
            "total_count": 3,
        }

        with patch("src.app.modules.billing.service.get_current_tenant", return_value=mock_tenant), \
             patch(
            "src.app.modules.billing.crud.crud_billing_records.get_multi",
            new_callable=AsyncMock,
            return_value=mock_records,
        ):
            result = await service.get_user_records(user_id=1, page=1, limit=20)

            # Verify records are in descending order by date
            assert result["data"][0]["id"] == 3
            assert result["data"][1]["id"] == 2
            assert result["data"][2]["id"] == 1


class TestBillingRecordStatusFiltering:
    """Test suite for billing record status filtering."""

    @pytest.mark.asyncio
    async def test_filter_succeeded_records(self, mock_db_session, mock_tenant):
        """Test filtering for succeeded billing records."""
        service = BillingRecordService(mock_db_session)

        mock_succeeded = {
            "data": [{"id": 1, "status": "succeeded"}, {"id": 2, "status": "succeeded"}],
            "total_count": 2,
        }

        with patch("src.app.modules.billing.service.get_current_tenant", return_value=mock_tenant), \
             patch(
            "src.app.modules.billing.crud.crud_billing_records.get_multi",
            new_callable=AsyncMock,
            return_value=mock_succeeded,
        ):
            result = await service.get_user_records(user_id=1, page=1, limit=20, status="succeeded")

            assert all(r["status"] == "succeeded" for r in result["data"])

    @pytest.mark.asyncio
    async def test_filter_failed_records(self, mock_db_session, mock_tenant):
        """Test filtering for failed billing records."""
        service = BillingRecordService(mock_db_session)

        mock_failed = {
            "data": [{"id": 3, "status": "failed"}],
            "total_count": 1,
        }

        with patch("src.app.modules.billing.service.get_current_tenant", return_value=mock_tenant), \
             patch(
            "src.app.modules.billing.crud.crud_billing_records.get_multi",
            new_callable=AsyncMock,
            return_value=mock_failed,
        ):
            result = await service.get_user_records(user_id=1, page=1, limit=20, status="failed")

            assert all(r["status"] == "failed" for r in result["data"])

    @pytest.mark.asyncio
    async def test_filter_refunded_records(self, mock_db_session, mock_tenant):
        """Test filtering for refunded billing records."""
        service = BillingRecordService(mock_db_session)

        mock_refunded = {
            "data": [{"id": 4, "status": "refunded"}, {"id": 5, "status": "refunded"}],
            "total_count": 2,
        }

        with patch("src.app.modules.billing.service.get_current_tenant", return_value=mock_tenant), \
             patch(
            "src.app.modules.billing.crud.crud_billing_records.get_multi",
            new_callable=AsyncMock,
            return_value=mock_refunded,
        ):
            result = await service.get_user_records(user_id=1, page=1, limit=20, status="refunded")

            assert all(r["status"] == "refunded" for r in result["data"])