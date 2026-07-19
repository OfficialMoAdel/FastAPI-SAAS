"""Unit tests for configurations/stripe_router.py compatibility layer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.modules.configurations.stripe_router import router as stripe_router


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(stripe_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_router_prefix_and_tags():
    assert stripe_router.prefix == "/configurations/stripe"
    assert "stripe" in stripe_router.tags


async def test_get_stripe_configuration_returns_settings():
    settings = {
        "id": 1,
        "tenant_id": 5,
        "stripe_customer_id": "cus_123",
        "stripe_account_id": "acct_123",
        "is_active": True,
        "encrypted_secret_key": "enc",
        "webhook_secret": "whsec",
        "created_at": None,
        "updated_at": None,
    }
    mock_service = MagicMock()
    mock_service.get_settings = AsyncMock(return_value=settings)

    with patch(
        "src.app.modules.configurations.stripe_router.get_stripe_settings_service",
        return_value=mock_service,
    ):
        from src.app.modules.configurations.stripe_router import get_stripe_configuration

        result = await get_stripe_configuration(db=MagicMock())
        assert result["has_secret_key"] is True
        assert result["has_webhook_secret"] is True
        assert result["stripe_customer_id"] == "cus_123"


async def test_get_stripe_configuration_none_returns_defaults():
    mock_service = MagicMock()
    mock_service.get_settings = AsyncMock(return_value=None)

    with patch(
        "src.app.modules.configurations.stripe_router.get_stripe_settings_service",
        return_value=mock_service,
    ):
        from src.app.modules.configurations.stripe_router import get_stripe_configuration

        result = await get_stripe_configuration(db=MagicMock())
        assert result["id"] == 0
        assert result["is_active"] is False
        assert result["has_secret_key"] is False
        assert result["has_webhook_secret"] is False


async def test_create_or_update_stripe_configuration():
    mock_service = MagicMock()
    mock_service.update_settings = AsyncMock(return_value={"id": 1})

    with patch(
        "src.app.modules.configurations.stripe_router.get_stripe_settings_service",
        return_value=mock_service,
    ):
        from src.app.modules.billing.schemas import StripeSettingsUpdate
        from src.app.modules.configurations.stripe_router import (
            create_or_update_stripe_configuration,
        )

        data = StripeSettingsUpdate()
        result = await create_or_update_stripe_configuration(data=data, db=MagicMock())
        assert result == {"id": 1}
        mock_service.update_settings.assert_awaited_once_with(data)


async def test_handle_webhooks_missing_signature():
    from src.app.modules.configurations.stripe_router import handle_stripe_webhooks

    request = MagicMock()
    request.body = AsyncMock(return_value=b"{}")

    with pytest.raises(Exception):  # HTTPException 400
        await handle_stripe_webhooks(
            request=request,
            background_tasks=MagicMock(),
            redis=MagicMock(),
            stripe_signature=None,
        )


async def test_handle_webhooks_duplicate_event():
    from src.app.modules.configurations.stripe_router import handle_stripe_webhooks

    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"id": "evt_1"}')

    redis = MagicMock()
    redis.exists = AsyncMock(return_value=1)

    secret = MagicMock()
    secret.get_secret_value.return_value = "whsec_global"

    with (
        patch("src.app.core.config.settings", MagicMock(STRIPE_WEBHOOK_SECRET=secret)),
        patch(
            "stripe.Webhook.construct_event",
            return_value={"id": "evt_1"},
        ),
    ):
        result = await handle_stripe_webhooks(
            request=request,
            background_tasks=MagicMock(),
            redis=redis,
            stripe_signature="sig",
        )
        assert result == {"status": "duplicate"}


async def test_handle_webhooks_success_global_secret():
    from src.app.modules.configurations.stripe_router import handle_stripe_webhooks

    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"id": "evt_2"}')

    redis = MagicMock()
    redis.exists = AsyncMock(return_value=0)
    redis.setex = AsyncMock()

    background_tasks = MagicMock()
    background_tasks.add_task = MagicMock()

    secret = MagicMock()
    secret.get_secret_value.return_value = "whsec_global"

    with (
        patch("src.app.core.config.settings", MagicMock(STRIPE_WEBHOOK_SECRET=secret)),
        patch(
            "stripe.Webhook.construct_event",
            return_value={"id": "evt_2"},
        ),
    ):
        result = await handle_stripe_webhooks(
            request=request,
            background_tasks=background_tasks,
            redis=redis,
            stripe_signature="sig",
        )
        assert result == {"status": "success"}
        redis.setex.assert_awaited_once()
        background_tasks.add_task.assert_called_once()


async def test_handle_webhooks_no_secret_configured():
    from src.app.modules.configurations.stripe_router import handle_stripe_webhooks

    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"id": "evt_3"}')

    redis = MagicMock()
    redis.exists = AsyncMock(return_value=0)

    with patch("src.app.core.config.settings", MagicMock(STRIPE_WEBHOOK_SECRET=None)):
        with pytest.raises(Exception):  # HTTPException 500
            await handle_stripe_webhooks(
                request=request,
                background_tasks=MagicMock(),
                redis=redis,
                stripe_signature="sig",
            )


async def test_handle_webhooks_invalid_signature():
    from src.app.modules.configurations.stripe_router import handle_stripe_webhooks

    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"id": "evt_4"}')

    redis = MagicMock()
    redis.exists = AsyncMock(return_value=0)

    secret = MagicMock()
    secret.get_secret_value.return_value = "whsec_global"

    with (
        patch("src.app.core.config.settings", MagicMock(STRIPE_WEBHOOK_SECRET=secret)),
        patch(
            "stripe.Webhook.construct_event",
            side_effect=Exception("bad sig"),
        ),
    ):
        with pytest.raises(Exception):  # HTTPException 400
            await handle_stripe_webhooks(
                request=request,
                background_tasks=MagicMock(),
                redis=redis,
                stripe_signature="sig",
            )


async def test_handle_webhooks_tenant_specific_secret():
    from src.app.modules.configurations.stripe_router import handle_stripe_webhooks

    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"id": "evt_5", "account": "acct_5"}')

    redis = MagicMock()
    redis.exists = AsyncMock(return_value=0)
    redis.setex = AsyncMock()

    background_tasks = MagicMock()
    background_tasks.add_task = MagicMock()

    # Tenant-specific settings record with encrypted webhook secret
    settings_record = MagicMock()
    settings_record.encrypted_webhook_secret = "enc_secret"

    mock_session = AsyncMock()
    mock_crud = MagicMock()
    mock_crud.get_by_account_id = AsyncMock(return_value=settings_record)

    # local_session() is an async context manager
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_session
    cm.__aexit__.return_value = False

    with (
        patch("src.app.core.config.settings", MagicMock(STRIPE_WEBHOOK_SECRET=None)),
        patch(
            "src.app.core.db.database.local_session",
            return_value=cm,
        ),
        patch(
            "src.app.modules.billing.crud.crud_stripe_settings",
            mock_crud,
        ),
        patch(
            "src.app.core.security.encryption.CredentialEncryption.decrypt_column",
            return_value="decrypted_secret",
        ),
        patch(
            "stripe.Webhook.construct_event",
            return_value={"id": "evt_5"},
        ),
    ):
        result = await handle_stripe_webhooks(
            request=request,
            background_tasks=background_tasks,
            redis=redis,
            stripe_signature="sig",
        )
        assert result == {"status": "success"}
        mock_crud.get_by_account_id.assert_awaited_once_with(mock_session, "acct_5")
        redis.setex.assert_awaited_once()
        background_tasks.add_task.assert_called_once()


async def test_handle_webhooks_tenant_lookup_exception_falls_back():
    from src.app.modules.configurations.stripe_router import handle_stripe_webhooks

    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"id": "evt_6", "account": "acct_6"}')

    redis = MagicMock()
    redis.exists = AsyncMock(return_value=0)
    redis.setex = AsyncMock()

    background_tasks = MagicMock()
    background_tasks.add_task = MagicMock()

    # local_session raises -> caught -> falls back to global secret
    cm = AsyncMock()
    cm.__aenter__.side_effect = RuntimeError("db down")
    cm.__aexit__.return_value = False

    secret = MagicMock()
    secret.get_secret_value.return_value = "whsec_global"

    with (
        patch("src.app.core.config.settings", MagicMock(STRIPE_WEBHOOK_SECRET=secret)),
        patch(
            "src.app.core.db.database.local_session",
            return_value=cm,
        ),
        patch(
            "stripe.Webhook.construct_event",
            return_value={"id": "evt_6"},
        ),
    ):
        result = await handle_stripe_webhooks(
            request=request,
            background_tasks=background_tasks,
            redis=redis,
            stripe_signature="sig",
        )
        assert result == {"status": "success"}
