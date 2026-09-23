"""Contract tests for the fused billing gateway seam.

``retrieve_subscription_for_tenant`` owns resolve → build → retrieve in one
call. Tests inject a fake gateway factory at the module seam — no Stripe, no
DB, no patching of ``clients.billing.stripe``.
"""

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from src.app.modules.billing.subscription_service import (
    fetch_stripe_subscription,
    retrieve_subscription_for_tenant,
)


class FakeGateway:
    """Minimal PaymentGateway double: records construction + retrieval args."""

    def __init__(self, store: dict[str, Any], api_key: str | None = None) -> None:
        self._store = store
        self._store.setdefault("constructed_with", []).append({"api_key": api_key})
        self._api_key = api_key

    async def get_api_key(self, tenant_id: Any = None) -> str | None:
        self._store["key_requested_for"] = tenant_id
        return "sk_test_fake"

    async def retrieve_subscription(self, subscription_id: str, **kwargs: Any) -> Any:
        self._store["retrieved"] = {"id": subscription_id, "kwargs": kwargs}
        return {"id": subscription_id, "status": "active"}


def _factory(store: dict[str, Any]) -> Any:
    return lambda **kw: FakeGateway(store, **kw)


async def test_fused_choreography_resolve_build_retrieve() -> None:
    """One call: key resolved via the factory gateway, same key builds the
    retrieving gateway, canned subscription returned."""
    tid = uuid4()
    store: dict[str, Any] = {}
    result = await retrieve_subscription_for_tenant(
        AsyncMock(), tid, "sub_123", gateway_factory=_factory(store)
    )
    assert result == {"id": "sub_123", "status": "active"}
    assert store["key_requested_for"] == tid
    assert store["constructed_with"][-1] == {"api_key": "sk_test_fake"}
    assert store["retrieved"]["id"] == "sub_123"


async def test_default_path_uses_resolve_seam_and_fetch_primitive() -> None:
    """No factory: resolve goes through CheckoutService.resolve_api_key and
    retrieval through fetch_stripe_subscription (the old two-step, byte-identical)."""
    tid = uuid4()
    with (
        patch(
            "src.app.modules.billing.checkout_service.CheckoutService.resolve_api_key",
            return_value="sk_test_resolved",
        ) as mock_resolve,
        patch(
            "src.app.modules.billing.subscription_service.fetch_stripe_subscription",
            return_value={"id": "sub_9", "status": "trialing"},
        ) as mock_fetch,
    ):
        result = await retrieve_subscription_for_tenant(AsyncMock(), tid, "sub_9")
    assert result == {"id": "sub_9", "status": "trialing"}
    mock_resolve.assert_awaited_once_with(tid)
    mock_fetch.assert_awaited_once_with("sk_test_resolved", "sub_9")


async def test_fetch_primitive_builds_gateway_with_given_key() -> None:
    """fetch_stripe_subscription(api_key, id) constructs exactly one gateway
    with that key and retrieves — the primitive the default path reuses."""
    with patch(
        "src.app.clients.billing.stripe.get_payment_gateway"
    ) as mock_factory:
        gateway = AsyncMock()
        gateway.retrieve_subscription.return_value = {"id": "sub_1"}
        mock_factory.return_value = gateway
        result = await fetch_stripe_subscription("sk_test_x", "sub_1")
    assert result == {"id": "sub_1"}
    mock_factory.assert_called_once_with(api_key="sk_test_x")
    gateway.retrieve_subscription.assert_awaited_once_with("sub_1")


async def test_factory_receives_resolved_key_not_tenant() -> None:
    """The key — never the tenant id — is what builds the retrieving gateway.
    Two constructions: resolve (no key; the gateway resolves it internally
    via get_api_key) then retrieve (with the resolved key)."""
    store: dict[str, Any] = {}
    await retrieve_subscription_for_tenant(
        AsyncMock(), UUID(int=0), "sub_0", gateway_factory=_factory(store)
    )
    assert store["constructed_with"] == [{"api_key": None}, {"api_key": "sk_test_fake"}]
    assert store["key_requested_for"] == UUID(int=0)
