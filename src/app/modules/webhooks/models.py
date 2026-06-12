"""Webhook models for event subscriptions and delivery tracking."""

import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base
from src.app.core.db.models import UUIDMixin, WebhookDeliveryStatus

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant


class WebhookEndpoint(Base, UUIDMixin):
    """
    Webhook endpoint configuration for tenant event subscriptions.

    Allows tenants to subscribe to system events and receive
    HTTP callbacks with event data.

    Attributes
    ----------
        id: Unique UUID identifier
        tenant_id: Reference to tenant (multi-tenancy isolation)
        url: Target URL for webhook deliveries
        secret: HMAC secret for signature generation (encrypted at rest)
        event_type: Event type to subscribe to (e.g., 'user.created')
        is_active: Whether endpoint is currently active
        created_at: Timestamp when endpoint was created
        updated_at: Timestamp when endpoint was last modified
    """

    __tablename__ = "webhook_endpoints"

    # Foreign Keys
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Endpoint Configuration
    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        comment="Target URL for webhook deliveries",
    )
    secret: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="HMAC secret for signature generation (encrypted at rest)",
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Event type pattern (e.g., 'user.*', 'user.created')",
    )
    signing_secret: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        comment="Per-endpoint signing secret for webhook verification (encrypted fernet string)",
    )
    old_signing_secret: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        comment="Old signing secret for webhook verification during grace period (encrypted fernet string)",
    )
    old_secret_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="Expiration timestamp for the old signing secret",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
        comment="Whether endpoint is currently active",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        insert_default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        init=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        insert_default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        onupdate=lambda: datetime.now(UTC),
        init=False,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(  # type: ignore
        "Tenant",
        back_populates="webhook_endpoints",
        init=False,
    )
    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        "WebhookDelivery",
        back_populates="webhook_endpoint",
        cascade="all, delete-orphan",
        init=False,
    )

    # Composite Indexes for Performance
    __table_args__ = (
        # Query active endpoints by tenant and event type
        Index(
            "idx_webhook_endpoint_tenant_event",
            "tenant_id",
            "event_type",
            "is_active",
        ),
        # Query active endpoints
        Index(
            "idx_webhook_endpoint_active",
            "is_active",
            postgresql_where="is_active = true",
        ),
    )

    def __repr__(self) -> str:
        """String representation of webhook endpoint."""
        return (
            f"<WebhookEndpoint(id={self.id}, url='{self.url}', "
            f"event_type='{self.event_type}', active={self.is_active})>"
        )


class WebhookDelivery(Base, UUIDMixin):
    """
    Webhook delivery attempt tracking.

    Tracks individual webhook delivery attempts with payload,
    response status, retry information, and delivery status.

    Attributes
    ----------
        id: Unique UUID identifier
        webhook_endpoint_id: Reference to webhook endpoint
        event_type: Event type that triggered delivery
        payload: Event data payload (JSONB)
        signature: HMAC signature for payload verification
        status: Delivery status (pending/delivered/failed)
        attempt_count: Number of delivery attempts made
        response_status: HTTP response status code
        response_body: HTTP response body (truncated)
        next_retry_at: Timestamp for next retry attempt
        created_at: Timestamp when delivery was created
        updated_at: Timestamp when delivery was last attempted
    """

    __tablename__ = "webhook_deliveries"

    # Foreign Keys
    webhook_endpoint_id: Mapped["uuid_pkg.UUID"] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Event Information
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Event type that triggered delivery",
    )
    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment="Event data payload",
    )
    signature: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="HMAC-SHA256 signature for verification",
    )

    # Delivery Status
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        Enum(WebhookDeliveryStatus, native_enum=False, length=20),
        nullable=False,
        default=WebhookDeliveryStatus.PENDING,
        index=True,
        comment="Current delivery status",
    )
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default="0",
        comment="Number of delivery attempts made",
    )

    # Response Information
    response_status: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
        init=False,
        comment="HTTP response status code",
    )
    response_body: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        init=False,
        comment="HTTP response body (truncated to 10KB)",
    )

    # Retry Information
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        init=False,
        comment="Timestamp for next retry attempt",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        insert_default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        init=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        insert_default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        onupdate=lambda: datetime.now(UTC),
        init=False,
    )

    # Relationships
    webhook_endpoint: Mapped["WebhookEndpoint"] = relationship(
        "WebhookEndpoint",
        back_populates="deliveries",
        init=False,
    )
    tenant: Mapped["Tenant"] = relationship(  # type: ignore
        "Tenant",
        back_populates="webhook_deliveries",
        init=False,
    )

    # Composite Indexes for Performance
    __table_args__ = (
        # Query pending deliveries ready for retry
        Index(
            "idx_webhook_delivery_retry",
            "status",
            "next_retry_at",
            postgresql_where="status = 'pending' AND next_retry_at IS NOT NULL",
        ),
        # Query deliveries by endpoint and status
        Index(
            "idx_webhook_delivery_endpoint_status",
            "webhook_endpoint_id",
            "status",
        ),
        # Query deliveries by time (for cleanup/archival)
        Index(
            "idx_webhook_delivery_created",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
    )

    def __repr__(self) -> str:
        """String representation of webhook delivery."""
        return (
            f"<WebhookDelivery(id={self.id}, event_type='{self.event_type}', "
            f"status={self.status.value}, attempts={self.attempt_count})>"
        )
