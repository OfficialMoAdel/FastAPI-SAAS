from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.app.modules.webhooks.models import WebhookDeliveryStatus
from src.app.modules.webhooks.router import (
    create_webhook_endpoint,
    delete_webhook_endpoint,
    get_signing_secret,
    get_webhook_delivery,
    get_webhook_endpoint,
    list_webhook_deliveries_route,
    list_webhook_endpoints,
    retry_webhook_delivery,
    rotate_signing_secret,
    update_webhook_endpoint,
)
from src.app.modules.webhooks.router import test_webhook_endpoint as route_test_webhook_endpoint
from src.app.modules.webhooks.schemas import (
    SigningSecretRotateRequest,
    WebhookEndpointCreate,
    WebhookEndpointUpdate,
    WebhookTestRequest,
)


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_tenant():
    tenant = MagicMock()
    tenant.id = uuid4()
    return tenant


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = 1
    return user


def _make_ctx(mock_db, mock_tenant, mock_user):
    from unittest.mock import MagicMock
    ctx = MagicMock()
    ctx.db = mock_db
    ctx.tenant = mock_tenant
    ctx.tenant_id = mock_tenant.id
    ctx.user = mock_user
    ctx.user_id = mock_user.get("id") if isinstance(mock_user, dict) else getattr(mock_user, "id", 1)
    ctx.require_tenant = MagicMock(return_value=mock_tenant)
    ctx.require_ownership = MagicMock()
    return ctx


@pytest.mark.asyncio
async def test_list_webhook_endpoints(mock_db, mock_tenant, mock_user):
    mock_endpoint = MagicMock()
    mock_endpoint.id = uuid4()
    mock_endpoint.tenant_id = mock_tenant.id
    mock_endpoint.url = "https://example.com/webhook"
    mock_endpoint.event_type = "user.created"
    mock_endpoint.is_active = True
    mock_endpoint.created_at = datetime.now(UTC)

    mock_result = MagicMock()
    mock_result.unique.return_value.all.return_value = [(mock_endpoint, 1)]
    mock_db.execute.return_value = mock_result
    result = await list_webhook_endpoints(
        is_active=True,
        event_type="user.created",
        page=1,
        per_page=10,
        ctx=_make_ctx(mock_db, mock_tenant, mock_user),
    )

    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0].url == "https://example.com/webhook"


@pytest.mark.asyncio
@patch("src.app.modules.webhooks.router.WebhookService")
async def test_create_webhook_endpoint(mock_service_cls, mock_db, mock_tenant, mock_user):
    mock_service = MagicMock()
    mock_endpoint = MagicMock()
    mock_endpoint.id = uuid4()
    mock_endpoint.tenant_id = mock_tenant.id
    mock_endpoint.url = "https://example.com/webhook"
    mock_endpoint.event_type = "user.created"
    mock_endpoint.is_active = True
    mock_endpoint.created_at = datetime.now(UTC)
    # create_endpoint attaches the plaintext secrets as transient attributes that the
    # router maps onto the response model (which requires str values).
    mock_endpoint._plaintext_secret = "secret"
    mock_endpoint._plaintext_signing_secret = "signing_secret"
    mock_service.create_endpoint = AsyncMock(return_value=mock_endpoint)
    mock_service_cls.return_value = mock_service

    request = WebhookEndpointCreate(url="https://example.com/webhook", event_type="user.created")
    result = await create_webhook_endpoint(
        request=request, ctx=_make_ctx(mock_db, mock_tenant, mock_user)
    )

    assert result.url == "https://example.com/webhook"
    assert result.secret == "secret"


@pytest.mark.asyncio
async def test_list_webhook_deliveries_route(mock_db, mock_tenant, mock_user):
    mock_delivery = MagicMock()
    mock_delivery.id = uuid4()
    mock_delivery.webhook_endpoint_id = uuid4()
    mock_delivery.event_type = "user.created"
    mock_delivery.status = WebhookDeliveryStatus.DELIVERED
    mock_delivery.payload = {}
    mock_delivery.response_status = 200
    mock_delivery.response_body = "OK"
    mock_delivery.created_at = datetime.now(UTC)

    mock_result = MagicMock()
    mock_result.unique.return_value.all.return_value = [(mock_delivery, 1)]
    mock_db.execute.return_value = mock_result
    result = await list_webhook_deliveries_route(
        endpoint_id=None,
        delivery_status=None,
        event_type=None,
        start_date=None,
        end_date=None,
        page=1,
        per_page=10,
        ctx=_make_ctx(mock_db, mock_tenant, mock_user),
    )

    assert result["total"] == 1
    assert len(result["items"]) == 1


@pytest.mark.asyncio
async def test_get_webhook_endpoint(mock_db, mock_tenant, mock_user):
    endpoint_id = uuid4()
    mock_endpoint = MagicMock()
    mock_endpoint.id = endpoint_id
    mock_endpoint.tenant_id = mock_tenant.id
    mock_endpoint.url = "https://example.com/webhook"
    mock_endpoint.event_type = "*"
    mock_endpoint.is_active = True
    mock_endpoint.created_at = datetime.now(UTC)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_endpoint
    mock_db.execute.return_value = mock_result

    result = await get_webhook_endpoint(
        endpoint_id=endpoint_id, ctx=_make_ctx(mock_db, mock_tenant, mock_user)
    )

    assert result.id == endpoint_id


@pytest.mark.asyncio
async def test_get_webhook_endpoint_not_found(mock_db, mock_tenant, mock_user):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc:
        await get_webhook_endpoint(endpoint_id=uuid4(), ctx=_make_ctx(mock_db, mock_tenant, mock_user))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_webhook_endpoint(mock_db, mock_tenant, mock_user):
    endpoint_id = uuid4()
    mock_endpoint = MagicMock()
    mock_endpoint.id = endpoint_id
    mock_endpoint.tenant_id = mock_tenant.id
    mock_endpoint.url = "https://example.com/webhook"
    mock_endpoint.event_type = "*"
    mock_endpoint.is_active = True
    mock_endpoint.created_at = datetime.now(UTC)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_endpoint
    mock_db.execute.return_value = mock_result

    request = WebhookEndpointUpdate(is_active=False)
    await update_webhook_endpoint(
        endpoint_id=endpoint_id, request=request, ctx=_make_ctx(mock_db, mock_tenant, mock_user)
    )

    assert mock_endpoint.is_active is False
    mock_db.commit.assert_called_once()

@pytest.mark.asyncio
async def test_delete_webhook_endpoint(mock_db, mock_tenant, mock_user):
    endpoint_id = uuid4()
    mock_endpoint = MagicMock()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_endpoint
    mock_db.execute.return_value = mock_result

    await delete_webhook_endpoint(
        endpoint_id=endpoint_id, ctx=_make_ctx(mock_db, mock_tenant, mock_user)
    )

    mock_db.delete.assert_called_once_with(mock_endpoint)
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_webhook_delivery(mock_db, mock_tenant, mock_user):
    delivery_id = uuid4()
    mock_delivery = MagicMock()
    mock_delivery.id = delivery_id
    mock_delivery.webhook_endpoint_id = uuid4()
    mock_delivery.event_type = "*"
    mock_delivery.status = WebhookDeliveryStatus.DELIVERED
    mock_delivery.payload = {}
    mock_delivery.response_status = 200
    mock_delivery.response_body = "OK"
    mock_delivery.created_at = datetime.now(UTC)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_delivery
    mock_db.execute.return_value = mock_result

    result = await get_webhook_delivery(
        delivery_id=delivery_id, ctx=_make_ctx(mock_db, mock_tenant, mock_user)
    )

    assert result.id == delivery_id


@pytest.mark.asyncio
@patch("src.app.modules.webhooks.router.WebhookService")
async def test_retry_webhook_delivery(mock_service_cls, mock_db, mock_tenant, mock_user):
    delivery_id = uuid4()
    mock_delivery = MagicMock()
    mock_delivery.id = delivery_id
    mock_delivery.webhook_endpoint_id = uuid4()
    mock_delivery.event_type = "*"
    mock_delivery.status = WebhookDeliveryStatus.FAILED
    mock_delivery.payload = {}
    mock_delivery.response_status = 500
    mock_delivery.response_body = "Error"
    mock_delivery.created_at = datetime.now(UTC)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_delivery
    mock_db.execute.return_value = mock_result

    mock_service = MagicMock()
    mock_service.get_delivery_by_id = AsyncMock(return_value=mock_delivery)
    mock_service.retry_delivery = AsyncMock()
    mock_service_cls.return_value = mock_service

    result = await retry_webhook_delivery(
        delivery_id=delivery_id, ctx=_make_ctx(mock_db, mock_tenant, mock_user)
    )

    mock_service.retry_delivery.assert_called_once_with(delivery_id)
    assert result.id == delivery_id


@pytest.mark.asyncio
async def test_retry_webhook_delivery_not_failed(mock_db, mock_tenant, mock_user):
    mock_delivery = MagicMock()
    mock_delivery.status = WebhookDeliveryStatus.DELIVERED

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_delivery
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc:
        await retry_webhook_delivery(
            delivery_id=uuid4(), ctx=_make_ctx(mock_db, mock_tenant, mock_user)
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
@patch("src.app.core.utils.rate_limit.rate_limiter")
@patch("src.app.modules.webhooks.router.WebhookService")
async def test_test_webhook_endpoint(mock_service_cls, mock_rate_limiter, mock_db, mock_tenant):
    endpoint_id = uuid4()

    # Mock rate limit check
    mock_rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 10))

    # Needs a dict for current_user
    mock_user_dict = {"id": 1, "tier_id": 1}

    # Mock rate limit config fetch
    mock_rate_limit_result = MagicMock()
    mock_rate_limit_result.scalar_one_or_none.return_value = None

    # Mock endpoint query
    mock_endpoint = MagicMock()
    mock_endpoint.id = endpoint_id
    mock_endpoint_result = MagicMock()
    mock_endpoint_result.scalar_one_or_none.return_value = mock_endpoint

    mock_db.execute.side_effect = [mock_rate_limit_result, mock_endpoint_result]

    mock_service = MagicMock()
    mock_service.check_test_rate_limit = AsyncMock(return_value=None)
    mock_service.get_endpoint_by_id = AsyncMock(return_value=mock_endpoint)
    mock_delivery = MagicMock()
    mock_delivery.id = uuid4()
    mock_delivery.webhook_endpoint_id = endpoint_id
    mock_delivery.event_type = "test.event"
    mock_delivery.status = WebhookDeliveryStatus.DELIVERED
    mock_delivery.payload = {}
    mock_delivery.response_status = 200
    mock_delivery.response_body = "OK"
    mock_delivery.created_at = datetime.now(UTC)
    mock_service.trigger_endpoint = AsyncMock(return_value=mock_delivery)
    mock_service_cls.return_value = mock_service

    request = WebhookTestRequest(event_type="test.event", payload={"test": True})

    result = await route_test_webhook_endpoint(
        endpoint_id=endpoint_id, request=request, ctx=_make_ctx(mock_db, mock_tenant, mock_user_dict)
    )

    assert result.event_type == "test.event"


@pytest.mark.asyncio
@patch("src.app.modules.webhooks.router.WebhookService")
async def test_get_signing_secret(mock_service_cls, mock_db, mock_tenant, mock_user):
    endpoint_id = uuid4()
    mock_service = MagicMock()
    mock_service.get_signing_secret = AsyncMock(
        return_value={
            "webhook_endpoint_id": endpoint_id,
            "signing_secret": "whsec_a1b2c3d4e5f6g7h8i9j0",
            "created_at": datetime.now(UTC),
        }
    )
    mock_service_cls.return_value = mock_service

    result = await get_signing_secret(
        endpoint_id=endpoint_id, ctx=_make_ctx(mock_db, mock_tenant, mock_user)
    )

    # S-5: the endpoint masks the secret instead of returning plaintext.
    assert result.signing_secret != "whsec_a1b2c3d4e5f6g7h8i9j0"
    assert result.signing_secret.startswith("whsec_")
    assert "…" in result.signing_secret


@pytest.mark.asyncio
@patch("src.app.modules.webhooks.router.WebhookService")
async def test_rotate_signing_secret(mock_service_cls, mock_db, mock_tenant, mock_user):
    endpoint_id = uuid4()
    mock_service = MagicMock()
    mock_service.rotate_signing_secret = AsyncMock(
        return_value={
            "webhook_endpoint_id": endpoint_id,
            "new_signing_secret": "new-secret",
            "rotated_at": datetime.now(UTC),
            "old_secret_expires_at": datetime.now(UTC),
            "grace_period_seconds": 300,
        }
    )
    mock_service_cls.return_value = mock_service

    request = SigningSecretRotateRequest(grace_period_seconds=300)
    result = await rotate_signing_secret(
        endpoint_id=endpoint_id, request=request, ctx=_make_ctx(mock_db, mock_tenant, mock_user)
    )

    assert result.new_signing_secret == "new-secret"
