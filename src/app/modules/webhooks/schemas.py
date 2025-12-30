"""Pydantic schemas for webhooks."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


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


class WebhookEndpointCreate(WebhookEndpointBase):
    """Schema for creating webhook endpoint."""

    tenant_id: UUID
    secret: str | None = Field(
        None, description="Optional custom secret for signature (auto-generated if not provided)"
    )


class WebhookEndpointUpdate(BaseModel):
    """Schema for updating webhook endpoint."""

    url: HttpUrl | None = None
    event_type: str | None = None
    is_active: bool | None = None
    secret: str | None = Field(None, description="New secret (regenerates signature)")


class WebhookEndpointResponse(BaseModel):
    """Schema for webhook endpoint response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    url: str
    event_type: str
    is_active: bool
    created_at: datetime
    # Note: secret is NOT included for security


class WebhookEndpointListResponse(BaseModel):
    """Schema for paginated webhook endpoint list."""

    items: list[WebhookEndpointResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class WebhookDeliveryResponse(BaseModel):
    """Schema for webhook delivery response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    webhook_endpoint_id: UUID
    event_type: str
    payload: dict[str, Any]
    status: str
    attempt_count: int
    response_status: int | None
    response_body: str | None
    next_retry_at: datetime | None
    created_at: datetime


class WebhookDeliveryListResponse(BaseModel):
    """Schema for paginated webhook delivery list."""

    items: list[WebhookDeliveryResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


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
