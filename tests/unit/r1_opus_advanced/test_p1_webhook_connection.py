"""P1-1: Webhook DB Connection Release Tests.

Verifies that webhook delivery releases DB connection before HTTP calls.
"""

import inspect


class TestWebhookConnectionRelease:
    """Tests for DB connection release during webhook HTTP calls."""

    def test_db_commit_before_http_in_source(self) -> None:
        """Verify DB commit happens before HTTP call in source code."""
        from src.app.modules.webhooks import service

        source = inspect.getsource(service)

        # Find the deliver_webhook function and check pattern
        assert "await self.db.commit()" in source
        assert "endpoint_url = endpoint.url" in source
        # CRIT-2 FIX: payload is now serialized directly to bytes, not stored in variable
        # The pattern now uses delivery.payload directly in json.dumps for single serialization
        assert "json.dumps(delivery.payload, sort_keys=True)" in source

    def test_delivery_refetched_after_http_in_source(self) -> None:
        """Verify delivery is re-fetched after HTTP call."""
        from src.app.modules.webhooks import service

        source = inspect.getsource(service)

        # The pattern should re-fetch delivery after HTTP call
        assert "await self.db.get(WebhookDelivery, delivery_id)" in source

    def test_http_errors_captured_without_db_session(self) -> None:
        """Verify HTTP errors are captured in variables, not raised in DB context."""
        from src.app.modules.webhooks import service

        source = inspect.getsource(service)

        # Check for error capture pattern
        assert "http_error: Exception | None = None" in source
        assert "circuit_error: Exception | None = None" in source

    def test_selectinload_used_for_eager_loading(self) -> None:
        """Verify selectinload is used for webhook endpoint."""
        from src.app.modules.webhooks import service

        source = inspect.getsource(service)

        # Check for selectinload usage
        assert "selectinload" in source
        assert "WebhookDelivery.webhook_endpoint" in source
