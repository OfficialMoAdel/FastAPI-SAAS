"""Coverage tests for webhooks CRUD module.

Importing the module instantiates the tenant-aware CRUD classes,
covering the module-level class definitions and instances.
"""

from src.app.modules.webhooks import crud as webhooks_crud


def test_crud_instances_exist() -> None:
    assert webhooks_crud.crud_webhook_endpoints is not None
    assert webhooks_crud.crud_webhook_deliveries is not None
    assert webhooks_crud.CRUDWebhookEndpoint is not None
    assert webhooks_crud.CRUDWebhookDelivery is not None
