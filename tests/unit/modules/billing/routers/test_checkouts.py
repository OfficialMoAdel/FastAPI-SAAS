"""Unit tests for billing/routes/checkouts.py compatibility layer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.api.deps.auth import AuthenticatedUser
from src.app.modules.billing.routers.checkouts import (
    cancel_current_subscription,
    create_checkout,
    get_current_subscription,
)


async def test_create_checkout():
    mock_service = MagicMock()
    mock_service.create_session = AsyncMock(return_value={"url": "https://stripe.com/x"})

    current_user = AuthenticatedUser(id=1, email="u@e.com")
    data = MagicMock()

    with patch(
        "src.app.modules.billing.routers.checkouts.get_checkout_service",
        return_value=mock_service,
    ):
        result = await create_checkout(data=data, current_user=current_user, db=MagicMock())
        assert result == {"url": "https://stripe.com/x"}
        mock_service.create_session.assert_awaited_once_with(user_id=1, user_email="u@e.com", data=data)


async def test_get_current_subscription_active():
    mock_service = MagicMock()
    mock_service.get_user_subscriptions = AsyncMock(
        return_value={
            "data": [
                {"id": 1, "status": "canceled"},
                {"id": 2, "status": "active"},
            ]
        }
    )

    with patch(
        "src.app.modules.billing.routers.checkouts.get_user_subscription_service",
        return_value=mock_service,
    ):
        result = await get_current_subscription(current_user=AuthenticatedUser(id=1, email="u@e.com"), db=MagicMock())
        assert result == {"id": 2, "status": "active"}


async def test_get_current_subscription_none():
    mock_service = MagicMock()
    mock_service.get_user_subscriptions = AsyncMock(return_value={"data": []})

    with patch(
        "src.app.modules.billing.routers.checkouts.get_user_subscription_service",
        return_value=mock_service,
    ):
        result = await get_current_subscription(current_user=AuthenticatedUser(id=1, email="u@e.com"), db=MagicMock())
        assert result is None


async def test_cancel_subscription_with_id():
    mock_service = MagicMock()
    mock_service.get_subscription = AsyncMock(return_value={"id": 7, "user_id": 1, "tenant_id": 3})
    mock_service.cancel_subscription = AsyncMock(return_value={"id": 7, "status": "canceled"})

    current_user = AuthenticatedUser(id=1, email="u@e.com", tenant_id=3)
    with patch(
        "src.app.modules.billing.routers.checkouts.get_user_subscription_service",
        return_value=mock_service,
    ):
        result = await cancel_current_subscription(current_user=current_user, db=MagicMock(), subscription_id=7)
        assert result == {"id": 7, "status": "canceled"}
        mock_service.cancel_subscription.assert_awaited_once_with(7)


async def test_cancel_subscription_finds_active():
    mock_service = MagicMock()
    mock_service.get_user_subscriptions = AsyncMock(return_value={"data": [{"id": 9, "status": "trialing"}]})
    mock_service.get_subscription = AsyncMock(return_value={"id": 9, "user_id": 1, "tenant_id": 3})
    mock_service.cancel_subscription = AsyncMock(return_value={"id": 9, "status": "canceled"})

    current_user = AuthenticatedUser(id=1, email="u@e.com", tenant_id=3)
    with patch(
        "src.app.modules.billing.routers.checkouts.get_user_subscription_service",
        return_value=mock_service,
    ):
        result = await cancel_current_subscription(current_user=current_user, db=MagicMock(), subscription_id=None)
        assert result == {"id": 9, "status": "canceled"}


async def test_cancel_subscription_no_active_raises():
    from src.app.shared.exceptions import ModelNotFoundError

    mock_service = MagicMock()
    mock_service.get_user_subscriptions = AsyncMock(return_value={"data": []})

    current_user = AuthenticatedUser(id=1, email="u@e.com", tenant_id=3)
    with patch(
        "src.app.modules.billing.routers.checkouts.get_user_subscription_service",
        return_value=mock_service,
    ):
        with pytest.raises(ModelNotFoundError, match="No active subscription found"):
            await cancel_current_subscription(current_user=current_user, db=MagicMock(), subscription_id=None)


async def test_cancel_subscription_unauthorized():
    from src.app.shared.exceptions import ForbiddenError

    mock_service = MagicMock()
    mock_service.get_user_subscriptions = AsyncMock(return_value={"data": [{"id": 9, "status": "active"}]})
    mock_service.get_subscription = AsyncMock(return_value={"id": 9, "user_id": 2, "tenant_id": 3})

    current_user = AuthenticatedUser(id=1, email="u@e.com", tenant_id=3)
    with patch(
        "src.app.modules.billing.routers.checkouts.get_user_subscription_service",
        return_value=mock_service,
    ):
        with pytest.raises(ForbiddenError, match="Not authorized to cancel this subscription"):
            await cancel_current_subscription(current_user=current_user, db=MagicMock(), subscription_id=None)
