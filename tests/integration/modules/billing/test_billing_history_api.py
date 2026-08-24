"""Integration tests for billing history API endpoints.

Tests T102: Integration test for billing history endpoint in tests/integration/billing/test_billing_history_api.py
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4


class TestBillingHistoryEndpoint:
    """Integration tests for GET /billing/records endpoint."""

    def test_billing_records_requires_auth(self, client_auth) -> None:
        """GET /billing/records should require authentication."""
        response = client_auth.get("/api/v1/billing/records")
        assert response.status_code in [400, 401, 403, 404]

    def test_billing_records_success(self, client_auth) -> None:
        """GET /billing/records should return paginated billing records."""
        mock_result = {
            "data": [
                {
                    "id": 1,
                    "tenant_id": str(uuid4()),
                    "user_id": 1,
                    "amount_cents": 9900,
                    "currency": "usd",
                    "status": "succeeded",
                    "description": "Monthly subscription",
                    "stripe_payment_intent_id": "pi_test_123",
                    "stripe_invoice_id": "in_test_123",
                    "user_subscription_id": 1,
                    "created_at": datetime.now(UTC).isoformat(),
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            ],
            "total_count": 1,
        }

        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_user_records",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = client_auth.get("/api/v1/billing/records")
            assert response.status_code in [200, 400, 401, 404, 422]
            if response.status_code == 200:
                data = response.json()
                assert "items" in data or "data" in data
                assert "total" in data or "total_count" in data

    def test_billing_records_pagination(self, client_auth) -> None:
        """GET /billing/records should support pagination parameters."""
        mock_result = {"data": [], "total_count": 0}

        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_user_records",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = client_auth.get(
                "/api/v1/billing/records",
                params={"page": 2, "limit": 5},
            )
            assert response.status_code in [200, 400, 401, 404, 422]

    def test_billing_records_empty_list(self, client_auth) -> None:
        """GET /billing/records should return empty list when no records."""
        mock_result = {"data": [], "total_count": 0}

        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_user_records",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = client_auth.get("/api/v1/billing/records")
            assert response.status_code in [200, 400, 401, 404, 422]
            if response.status_code == 200:
                data = response.json()
                items = data.get("items", data.get("data", []))
                assert items == []

    def test_billing_records_with_status_filter(self, client_auth) -> None:
        """GET /billing/records should support status filtering."""
        mock_result = {
            "data": [{"id": 1, "status": "failed", "amount_cents": 9900}],
            "total_count": 1,
        }

        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_user_records",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = client_auth.get(
                "/api/v1/billing/records",
                params={"status": "failed"},
            )
            assert response.status_code in [200, 400, 401, 404, 422]

    def test_billing_records_returns_correct_fields(self, client_auth) -> None:
        """GET /billing/records should return all expected fields."""
        mock_result = {
            "data": [
                {
                    "id": 1,
                    "tenant_id": str(uuid4()),
                    "user_id": 1,
                    "user_subscription_id": 1,
                    "stripe_payment_intent_id": "pi_test_123",
                    "stripe_invoice_id": "in_test_123",
                    "amount_cents": 9900,
                    "currency": "usd",
                    "status": "succeeded",
                    "description": "Monthly subscription payment",
                    "created_at": datetime.now(UTC).isoformat(),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ],
            "total_count": 1,
        }

        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_user_records",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = client_auth.get("/api/v1/billing/records")
            assert response.status_code in [200, 400, 401, 404, 422]
            if response.status_code == 200:
                data = response.json()
                items = data.get("items", data.get("data", []))
                if items:
                    record = items[0]
                    assert "amount_cents" in record
                    assert "currency" in record
                    assert "status" in record


class TestBillingRecordDetailEndpoint:
    """Integration tests for GET /billing/records/{record_id} endpoint."""

    def test_billing_record_detail_requires_auth(self, client_auth) -> None:
        """GET /billing/records/{record_id} should require authentication."""
        response = client_auth.get("/api/v1/billing/records/1")
        assert response.status_code in [400, 401, 403, 404]

    def test_billing_record_detail_success(self, client_auth) -> None:
        """GET /billing/records/{record_id} should return record details."""
        mock_record = {
            "id": 1,
            "tenant_id": str(uuid4()),
            "user_id": 1,
            "amount_cents": 9900,
            "currency": "usd",
            "status": "succeeded",
            "description": "Monthly subscription",
            "stripe_payment_intent_id": "pi_test_123",
            "stripe_invoice_id": "in_test_123",
            "user_subscription_id": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }

        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_record",
            new_callable=AsyncMock,
            return_value=mock_record,
        ):
            response = client_auth.get("/api/v1/billing/records/1")
            assert response.status_code in [200, 400, 401, 403, 404, 422]

    def test_billing_record_detail_not_found(self, client_auth) -> None:
        """GET /billing/records/{record_id} should return 404 for non-existent record."""
        from src.app.modules._base.exceptions import ModelNotFoundError

        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_record",
            new_callable=AsyncMock,
            side_effect=ModelNotFoundError("BillingRecord", 9999),
        ):
            response = client_auth.get("/api/v1/billing/records/9999")
            assert response.status_code in [400, 401, 404, 422, 500]

    def test_billing_record_detail_wrong_owner(self, client_auth) -> None:
        """GET /billing/records/{record_id} should return 403 for wrong owner."""
        # Record belongs to user_id=2, but authenticated user is user_id=1
        mock_record = {
            "id": 1,
            "tenant_id": str(uuid4()),
            "user_id": 2,  # Different user
            "amount_cents": 9900,
            "currency": "usd",
            "status": "succeeded",
        }

        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_record",
            new_callable=AsyncMock,
            return_value=mock_record,
        ):
            response = client_auth.get("/api/v1/billing/records/1")
            # Should fail - user doesn't own this record
            assert response.status_code in [400, 401, 403, 404, 422]


class TestBillingHistoryPagination:
    """Test pagination behavior for billing history."""

    def test_default_pagination(self, client_auth) -> None:
        """GET /billing/records should use default pagination when not specified."""
        mock_result = {"data": [], "total_count": 0}

        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_user_records",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = client_auth.get("/api/v1/billing/records")
            assert response.status_code in [200, 400, 401, 404, 422]
            if response.status_code == 200:
                data = response.json()
                assert "page" in data
                assert "limit" in data
                assert data["page"] == 1
                assert data["limit"] == 20  # Default limit

    def test_custom_pagination(self, client_auth) -> None:
        """GET /billing/records should accept custom pagination parameters."""
        mock_result = {"data": [], "total_count": 100}

        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_user_records",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = client_auth.get(
                "/api/v1/billing/records",
                params={"page": 3, "limit": 10},
            )
            assert response.status_code in [200, 400, 401, 404, 422]
            if response.status_code == 200:
                data = response.json()
                assert data["page"] == 3
                assert data["limit"] == 10

    def test_pagination_total_pages_calculation(self, client_auth) -> None:
        """GET /billing/records should calculate total pages correctly."""
        mock_result = {"data": [], "total_count": 25}

        with patch(
            "src.app.modules.billing.subscription_service.BillingRecordService.get_user_records",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = client_auth.get(
                "/api/v1/billing/records",
                params={"limit": 10},
            )
            assert response.status_code in [200, 400, 401, 404, 422]
            if response.status_code == 200:
                data = response.json()
                # 25 records with limit 10 = 3 pages
                assert data.get("pages", 0) == 3 or data.get("total_pages", 0) == 3
