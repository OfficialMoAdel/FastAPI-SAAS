"""S2 (SEC-09) regression: webhook endpoint READ paths never carry secrets.

``_serialize_endpoint`` embeds raw ``secret`` / ``signing_secret`` — but
only for the INTERNAL L2 endpoint cache consumed by the delivery worker
(needed to sign outbound payloads). The public READ contract must stay
secret-free: ``WebhookEndpointRead`` excludes both fields, and secrets
are returned exactly once on creation (``WebhookEndpointCreateResponse``).

These tests pin that contract at the schema boundary so a future
serializer/response-model change cannot silently leak secrets through a
GET path.
"""

from src.app.modules.webhooks.schemas import (
    WebhookEndpointCreateResponse,
    WebhookEndpointRead,
)


def test_endpoint_read_schema_has_no_secret_fields() -> None:
    """The list/detail READ model must not declare secret fields at all."""
    declared = set(WebhookEndpointRead.model_fields)
    assert "secret" not in declared
    assert "signing_secret" not in declared
    assert "old_signing_secret" not in declared


def test_endpoint_read_model_drops_extra_secret_fields() -> None:
    """Even if a row/dict carrying secrets is validated, they are dropped."""
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "tenant_id": "00000000-0000-0000-0000-000000000002",
        "url": "https://example.com/hook",
        "event_type": "user.created",
        "is_active": True,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": None,
        # leaked-looking payload fields must not survive validation:
        "secret": "supersecret",
        "signing_secret": "signingsecret",
        "old_signing_secret": "oldsigningsecret",
    }
    model = WebhookEndpointRead.model_validate(row)
    dumped = model.model_dump()
    assert "secret" not in dumped
    assert "signing_secret" not in dumped
    assert "old_signing_secret" not in dumped
    assert "supersecret" not in str(dumped)


def test_create_response_is_the_only_secret_bearing_schema() -> None:
    """Secrets exist ONLY on the create response (once-on-create contract)."""
    assert "secret" in WebhookEndpointCreateResponse.model_fields
    assert "signing_secret" in WebhookEndpointCreateResponse.model_fields
