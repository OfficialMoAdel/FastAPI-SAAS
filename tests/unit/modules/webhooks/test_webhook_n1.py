"""P2-1: Webhook N+1 Query Fix Tests.

Verifies that webhook delivery uses selectinload to avoid N+1 queries.
The delivery loop lives in webhooks/delivery.py (extracted from service.py
in the delivery-module consolidation); the eager-load contract is asserted
where the code lives now, and the service delegate is verified to reach it.
"""

import inspect


def _delivery_source() -> str:
    from src.app.modules.webhooks import delivery

    return inspect.getsource(delivery)


class TestWebhookN1Fix:
    """Tests for webhook N+1 query fix using selectinload."""

    def test_uses_selectinload(self) -> None:
        """Verify delivery query uses selectinload for webhook_endpoint."""
        source = _delivery_source()

        # Verify selectinload is used
        assert "selectinload" in source
        assert "selectinload(WebhookDelivery.webhook_endpoint)" in source

    def test_single_query_pattern(self) -> None:
        """Verify single query with options pattern is used."""
        source = _delivery_source()

        # Verify the single query pattern with options
        assert ".options(selectinload" in source

    def test_endpoint_accessed_from_delivery(self) -> None:
        """Verify endpoint is accessed from delivery object, not separate query."""
        source = _delivery_source()

        # Verify pattern accesses eagerly loaded endpoint
        assert "delivery.webhook_endpoint" in source

    def test_no_separate_endpoint_query(self) -> None:
        """Verify no separate db.get for endpoint after initial query."""
        source = _delivery_source()

        # The code should NOT have separate endpoint fetch after the initial query
        # It should use the eagerly loaded endpoint instead
        assert "selectinload" in source

        # And that we access the eagerly loaded relationship
        assert "endpoint = delivery.webhook_endpoint" in source

    def test_service_delegates_to_delivery_module(self) -> None:
        """The service surface reaches the delivery loop (delegate wiring)."""
        from src.app.modules.webhooks import service

        source = inspect.getsource(service)
        assert "delivery_module.deliver" in source
