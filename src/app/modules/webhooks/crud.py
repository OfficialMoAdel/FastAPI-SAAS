"""Webhooks CRUD operations.

Tenant-aware CRUD classes for webhook models.
"""

from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint
from src.app.modules.webhooks.schemas import (
    WebhookEndpointCreate,
    WebhookEndpointResponse,
    WebhookEndpointUpdate,
)

# Webhook Endpoint CRUD
CRUDWebhookEndpoint = TenantAwareCRUD[
    WebhookEndpoint,
    WebhookEndpointCreate,
    WebhookEndpointUpdate,
    WebhookEndpointUpdate,
    WebhookEndpointResponse,
    WebhookEndpointResponse,
]
crud_webhook_endpoints = CRUDWebhookEndpoint(WebhookEndpoint)

# Webhook Delivery CRUD
# Note: Since deliveries don't use standard create/update schemas, we use type Any
# for those type parameters, but this still allows using the generic read methods.
from typing import Any

CRUDWebhookDelivery = TenantAwareCRUD[
    WebhookDelivery,
    Any,
    Any,
    Any,
    Any,
    Any,
]
crud_webhook_deliveries = CRUDWebhookDelivery(WebhookDelivery)
