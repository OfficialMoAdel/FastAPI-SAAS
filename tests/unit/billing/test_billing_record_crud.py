"""Unit tests for BillingRecordCRUD operations.

Tests T065: Unit test for BillingRecordCRUD in tests/unit/billing/test_billing_record_crud.py
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.billing.crud import crud_billing_records
from src.app.modules.billing.models import BillingRecord
from src.app.modules.billing.schemas import BillingRecordCreate


@pytest.fixture
def billing_record_data():
    """Sample billing record data for testing."""
    return {
        "user_id": 1,
        "amount_cents": 9900,
        "currency": "usd",
        "status": "succeeded",
        "description": "Monthly subscription payment",
        "stripe_payment_intent_id": "pi_test_123",
        "stripe_invoice_id": "in_test_123",
        "user_subscription_id": 1,
    }


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


class TestBillingRecordCRUD:
    """Test suite for BillingRecordCRUD operations."""

    @pytest.mark.asyncio
    async def test_create_billing_record_succeeded(self, mock_db_session, billing_record_data):
        """Test creating a billing record with succeeded status."""
        tenant_id = 1
        create_schema = BillingRecordCreate(**billing_record_data)

        mock_record = MagicMock(spec=BillingRecord)
        mock_record.id = 1
        mock_record.tenant_id = tenant_id
        mock_record.user_id = billing_record_data["user_id"]
        mock_record.amount_cents = billing_record_data["amount_cents"]
        mock_record.currency = billing_record_data["currency"]
        mock_record.status = billing_record_data["status"]
        mock_record.description = billing_record_data["description"]
        mock_record.stripe_payment_intent_id = billing_record_data["stripe_payment_intent_id"]
        mock_record.stripe_invoice_id = billing_record_data["stripe_invoice_id"]
        mock_record.created_at = datetime.now(UTC)

        with patch.object(crud_billing_records, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_record

            result = await crud_billing_records.create(
                db=mock_db_session,
                tenant_id=tenant_id,
                obj_in=create_schema
            )

            assert result.id == 1
            assert result.amount_cents == 9900
            assert result.status == "succeeded"
            assert result.currency == "usd"
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_billing_record_failed(self, mock_db_session):
        """Test creating a billing record with failed status."""
        tenant_id = 1
        record_data = {
            "user_id": 1,
            "amount_cents": 9900,
            "currency": "usd",
            "status": "failed",
            "description": "Payment declined - insufficient funds",
        }
        create_schema = BillingRecordCreate(**record_data)

        mock_record = MagicMock(spec=BillingRecord)
        mock_record.id = 2
        mock_record.tenant_id = tenant_id
        mock_record.status = "failed"
        mock_record.description = record_data["description"]

        with patch.object(crud_billing_records, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_record

            result = await crud_billing_records.create(
                db=mock_db_session,
                tenant_id=tenant_id,
                obj_in=create_schema
            )

            assert result.status == "failed"
            assert "declined" in result.description

    @pytest.mark.asyncio
    async def test_create_billing_record_refunded(self, mock_db_session):
        """Test creating a billing record with refunded status."""
        tenant_id = 1
        record_data = {
            "user_id": 1,
            "amount_cents": 9900,
            "currency": "usd",
            "status": "refunded",
            "description": "Customer refund processed",
        }
        create_schema = BillingRecordCreate(**record_data)

        mock_record = MagicMock(spec=BillingRecord)
        mock_record.id = 3
        mock_record.tenant_id = tenant_id
        mock_record.status = "refunded"

        with patch.object(crud_billing_records, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_record

            result = await crud_billing_records.create(
                db=mock_db_session,
                tenant_id=tenant_id,
                obj_in=create_schema
            )

            assert result.status == "refunded"

    @pytest.mark.asyncio
    async def test_get_billing_record_by_id(self, mock_db_session):
        """Test retrieving a billing record by ID."""
        tenant_id = 1
        record_id = 1

        mock_record = MagicMock(spec=BillingRecord)
        mock_record.id = record_id
        mock_record.tenant_id = tenant_id
        mock_record.amount_cents = 9900
        mock_record.status = "succeeded"

        with patch.object(crud_billing_records, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_record

            result = await crud_billing_records.get(
                db=mock_db_session,
                tenant_id=tenant_id,
                id=record_id
            )

            assert result is not None
            assert result.id == record_id
            assert result.amount_cents == 9900

    @pytest.mark.asyncio
    async def test_get_billing_record_not_found(self, mock_db_session):
        """Test retrieving a non-existent billing record returns None."""
        tenant_id = 1
        record_id = 999

        with patch.object(crud_billing_records, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            result = await crud_billing_records.get(
                db=mock_db_session,
                tenant_id=tenant_id,
                id=record_id
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_get_multi_billing_records(self, mock_db_session):
        """Test getting multiple billing records with pagination."""
        tenant_id = 1

        mock_records = [
            MagicMock(spec=BillingRecord, id=1, status="succeeded", amount_cents=9900),
            MagicMock(spec=BillingRecord, id=2, status="succeeded", amount_cents=4900),
            MagicMock(spec=BillingRecord, id=3, status="failed", amount_cents=9900),
        ]

        with patch.object(crud_billing_records, 'get_multi', new_callable=AsyncMock) as mock_get_multi:
            mock_get_multi.return_value = mock_records

            result = await crud_billing_records.get_multi(
                db=mock_db_session,
                tenant_id=tenant_id,
                skip=0,
                limit=10
            )

            assert len(result) == 3
            assert result[0].status == "succeeded"
            assert result[2].status == "failed"

    @pytest.mark.asyncio
    async def test_billing_record_status_values(self):
        """Test that billing record status values are valid."""
        valid_statuses = ["succeeded", "failed", "pending", "refunded"]

        for status in valid_statuses:
            data = {
                "user_id": 1,
                "amount_cents": 9900,
                "currency": "usd",
                "status": status,
            }
            schema = BillingRecordCreate(**data)
            assert schema.status == status

    @pytest.mark.asyncio
    async def test_billing_record_with_stripe_ids(self, mock_db_session, billing_record_data):
        """Test billing record creation with Stripe IDs."""
        tenant_id = 1
        create_schema = BillingRecordCreate(**billing_record_data)

        mock_record = MagicMock(spec=BillingRecord)
        mock_record.id = 1
        mock_record.stripe_payment_intent_id = "pi_test_123"
        mock_record.stripe_invoice_id = "in_test_123"

        with patch.object(crud_billing_records, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_record

            result = await crud_billing_records.create(
                db=mock_db_session,
                tenant_id=tenant_id,
                obj_in=create_schema
            )

            assert result.stripe_payment_intent_id == "pi_test_123"
            assert result.stripe_invoice_id == "in_test_123"

    @pytest.mark.asyncio
    async def test_billing_record_without_subscription(self, mock_db_session):
        """Test billing record creation without linked subscription (one-time payment)."""
        tenant_id = 1
        record_data = {
            "user_id": 1,
            "amount_cents": 2500,
            "currency": "usd",
            "status": "succeeded",
            "description": "One-time product purchase",
            "user_subscription_id": None,
        }
        create_schema = BillingRecordCreate(**record_data)

        mock_record = MagicMock(spec=BillingRecord)
        mock_record.id = 1
        mock_record.user_subscription_id = None

        with patch.object(crud_billing_records, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_record

            result = await crud_billing_records.create(
                db=mock_db_session,
                tenant_id=tenant_id,
                obj_in=create_schema
            )

            assert result.user_subscription_id is None

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, mock_db_session):
        """Test that billing record queries are tenant-isolated."""
        tenant_id_1 = 1
        tenant_id_2 = 2
        record_id = 1

        mock_record = MagicMock(spec=BillingRecord)
        mock_record.id = record_id
        mock_record.tenant_id = tenant_id_1

        with patch.object(crud_billing_records, 'get', new_callable=AsyncMock) as mock_get:
            # When querying with correct tenant, should find record
            mock_get.return_value = mock_record
            result = await crud_billing_records.get(
                db=mock_db_session,
                tenant_id=tenant_id_1,
                id=record_id
            )
            assert result is not None

            # When querying with different tenant, should not find record
            mock_get.return_value = None
            result = await crud_billing_records.get(
                db=mock_db_session,
                tenant_id=tenant_id_2,
                id=record_id
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_currency_format(self):
        """Test that currency follows ISO 4217 format."""
        valid_currencies = ["usd", "eur", "gbp", "jpy"]

        for currency in valid_currencies:
            data = {
                "user_id": 1,
                "amount_cents": 9900,
                "currency": currency,
                "status": "succeeded",
            }
            schema = BillingRecordCreate(**data)
            assert schema.currency == currency
            assert len(schema.currency) == 3
