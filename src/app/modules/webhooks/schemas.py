"""Pydantic schemas for webhooks."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator

from src.app.modules._base import BaseCreate, BaseList, BaseReadUUID, BaseUpdate

# =============================================================================
# Webhook Endpoint Schemas
# =============================================================================


class WebhookEndpointBase(BaseModel):
    """Base webhook endpoint schema."""

    url: HttpUrl = Field(..., description="Webhook URL to POST events to (HTTPS required)")
    event_type: str = Field(..., description="Event type to subscribe to (e.g., 'user.created')")
    is_active: bool = Field(True, description="Whether webhook is active")

    @field_validator("url")
    @classmethod
    def validate_https_url(cls, v: HttpUrl) -> HttpUrl:
        """Ensure webhook URLs use HTTPS for security."""
        if str(v).startswith("http://"):
            raise ValueError("Webhook URLs must use HTTPS for security")
        return v


class WebhookEndpointCreate(WebhookEndpointBase, BaseCreate):
    """Schema for creating webhook endpoint."""

    tenant_id: UUID | None = Field(None, description="Tenant ID (populated from request context if not provided)")
    secret: str | None = Field(
        None, description="Optional custom secret for signature (auto-generated if not provided)"
    )
    signing_secret: str | None = Field(None, description="Per-endpoint signing secret (auto-generated if not provided)")


class WebhookEndpointUpdate(BaseUpdate):
    """Schema for updating webhook endpoint."""

    url: HttpUrl | None = None
    event_type: str | None = None
    is_active: bool | None = None
    secret: str | None = Field(None, description="New secret (regenerates signature)")
    signing_secret: str | None = Field(None, description="New signing secret")


class WebhookEndpointRead(BaseReadUUID):
    """Schema for webhook endpoint response."""

    tenant_id: UUID
    url: str
    event_type: str
    is_active: bool
    # Note: secret and signing_secret NOT included for security


class WebhookEndpointCreateResponse(BaseReadUUID):
    """Schema for webhook endpoint creation response — includes secret once.

    The ``secret`` and ``signing_secret`` are returned only on creation so the caller can store it.
    They will NOT be returned by any other endpoint.
    """

    tenant_id: UUID
    url: str
    event_type: str
    is_active: bool
    secret: str | None = Field(None, description="Auto-generated HMAC secret for signature verification")
    signing_secret: str | None = Field(None, description="Auto-generated signing secret")


class WebhookEndpointAdminRead(BaseReadUUID):
    """Schema for webhook endpoint admin response (includes signing_secret)."""

    tenant_id: UUID
    url: str
    event_type: str
    is_active: bool
    signing_secret: str | None = None  # Included for admin management


# Backward compatibility alias
WebhookEndpointResponse = WebhookEndpointRead


class WebhookEndpointList(BaseList[WebhookEndpointRead]):
    """Schema for paginated webhook endpoint list."""

    pass


# Backward compatibility alias
WebhookEndpointListResponse = WebhookEndpointList


# =============================================================================
# Webhook Delivery Schemas
# =============================================================================


class WebhookDeliveryRead(BaseReadUUID):
    """Schema for webhook delivery response."""

    webhook_endpoint_id: UUID
    event_type: str
    payload: dict[str, Any]
    status: str
    attempt_count: int
    response_status: int | None
    response_body: str | None
    next_retry_at: datetime | None


# Backward compatibility alias
WebhookDeliveryResponse = WebhookDeliveryRead


class WebhookDeliveryList(BaseList[WebhookDeliveryRead]):
    """Schema for paginated webhook delivery list."""

    pass


# Backward compatibility alias
WebhookDeliveryListResponse = WebhookDeliveryList


# =============================================================================
# Request Schemas
# =============================================================================


class WebhookDeliveryFilter(BaseModel):
    """Schema for filtering webhook deliveries."""

    webhook_endpoint_id: UUID | None = None
    event_type: str | None = None
    status: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=100)


class WebhookTestRequest(BaseModel):
    """Schema for testing webhook endpoint."""

    event_type: str = Field(..., description="Event type to simulate")
    payload: dict[str, Any] = Field(default_factory=dict, description="Test payload")


# =============================================================================
# Signing Secret Schemas
# =============================================================================


class SigningSecretViewResponse(BaseModel):
    """Schema for viewing webhook signing secret (admin only)."""

    webhook_endpoint_id: UUID
    signing_secret: str
    created_at: datetime
    message: str = "Store this secret securely. It will not be shown again."


class SigningSecretRotateRequest(BaseModel):
    """Schema for rotating webhook signing secret."""

    grace_period_seconds: int = Field(
        default=300,
        ge=0,
        le=86400,
        description="Grace period in seconds before old secret expires (0-86400, default: 300s/5min)",
    )


class SigningSecretRotateResponse(BaseModel):
    """Schema for signing secret rotation response."""

    webhook_endpoint_id: UUID
    new_signing_secret: str
    rotated_at: datetime | None = None
    old_secret_expires_at: datetime
    grace_period_seconds: int
    message: str = (
        "Secret rotated successfully. Old secret is invalidated immediately. "
        "Update your webhook consumer before rotating."
    )
