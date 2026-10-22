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
    Integer,
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
    # The creating layer (07_observability) declares these two columns; the
    # ORM did not, so the two layers disagreed about the table's shape. They
    # are not part of the delivery contract — they are operator-facing
    # annotations — so they are `init=False` and default to NULL.
    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        default=None,
        init=False,
        comment="Operator-facing description of this endpoint",
    )
    # Named `endpoint_metadata`, not `metadata`: SQLAlchemy RESERVES
    # `metadata` on a declarative base (`Base.metadata` is the registry), so
    # a column of that name cannot be mapped by attribute name at all. The
    # creating layer uses the same name, so the two layers agree.
    endpoint_metadata: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
        init=False,
        comment="Free-form operator metadata for this endpoint",
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
    #
    # TIMEZONE-AWARE, and the creating layer says so too. These columns used
    # to be declared as a naive `DateTime` while every write path used
    # `datetime.now(UTC)`, and the recovery sweep compares
    # `coalesce(next_retry_at, created_at)` against an aware cutoff — a
    # naive/aware mix that PostgreSQL resolves against the SERVER's session
    # timezone, so the recovery window skewed by the local UTC offset on any
    # non-UTC database.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        insert_default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        init=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        insert_default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        onupdate=lambda: datetime.now(UTC),
        init=False,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(
        "Tenant", back_populates="webhook_endpoints", init=False, lazy="noload"
    )
    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        "WebhookDelivery", back_populates="webhook_endpoint", cascade="all, delete-orphan", init=False, lazy="noload"
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
        # Active endpoints for one tenant.
        #
        # This index was previously declared as a SINGLE-COLUMN PARTIAL on
        # is_active (WHERE is_active = true) while the migration created it as
        # the composite (tenant_id, is_active) -- one name, two definitions.
        # Alembic compares by NAME and this repo declares no naming_convention,
        # so autogenerate reported no drift and the divergence was invisible.
        #
        # Every query that filters is_active is tenant-scoped: the paginated
        # list (service.py:200, :224) goes through ExplicitTenant, and the
        # delivery hot path (service.py:389-397) does the same. A single-column
        # index cannot serve those, because tenant_id is the leading RLS
        # predicate. The composite matches the emitted predicate, so it is the
        # correct definition and the partial form is dropped rather than
        # renamed: it served no query in this codebase, and keeping it would
        # have meant a second index whose only effect is write cost on a table
        # that holds every tenant's webhook configuration.
        Index(
            "idx_webhook_endpoint_active",
            "tenant_id",
            "is_active",
        ),
        # The creating layer creates this one on (tenant_id); the ORM used to
        # ask for an unnamed `index=True` instead, so a create_all database
        # carried a differently-named index on the same column that the
        # migrated database did not have. The named declaration makes the two
        # layers describe the same index.
        Index(
            "idx_webhook_endpoint_tenant",
            "tenant_id",
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
    signature: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        comment="DEPRECATED trigger-time HMAC-SHA256 signature (dead on the wire; "
        "the live send-time t=,v1= signature is computed in delivery.py and sent "
        "as a header, never persisted). Nullable for new rows; kept (not dropped) "
        "so existing delivery history is preserved.",
    )

    # Delivery Status
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        Enum(WebhookDeliveryStatus, native_enum=False, length=20, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=WebhookDeliveryStatus.PENDING,
        index=True,
        comment="Current delivery status",
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
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
    # The creating layer (07_observability) declares these three columns and
    # the delivery path keeps them in sync: `response_status_code` mirrors
    # `response_status`, `delivered_at` is set on a 2xx, and `error_message`
    # carries the latest failure. They exist for readers that ask for these
    # names; `delivered_at` is the delivery outcome, not `updated_at`.
    response_status_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        init=False,
        comment="HTTP response status code (creating-layer name for response_status)",
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        init=False,
        comment="Timestamp the delivery was accepted by the receiver",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        init=False,
        comment="Error message for a failed delivery",
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
    #
    # TIMEZONE-AWARE, matching the creating layer. The recovery sweep
    # compares `coalesce(next_retry_at, created_at)` against an aware
    # cutoff; a naive `created_at` made that comparison depend on the
    # database server's session timezone, so the recovery window skewed by
    # the local UTC offset on any non-UTC database.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        insert_default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        init=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        insert_default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        onupdate=lambda: datetime.now(UTC),
        init=False,
    )

    # Relationships
    webhook_endpoint: Mapped["WebhookEndpoint"] = relationship(
        "WebhookEndpoint", back_populates="deliveries", init=False, lazy="selectin"
    )
    tenant: Mapped["Tenant"] = relationship(
        "Tenant", back_populates="webhook_deliveries", init=False, lazy="noload"
    )

    # Composite Indexes for Performance
    __table_args__ = (
        # Query pending deliveries ready for retry.
        #
        # The predicate used to add `AND next_retry_at IS NOT NULL`, which
        # excluded exactly the rows the recovery sweep needs: a delivery whose
        # INITIAL enqueue was lost has next_retry_at NULL, and the sweep
        # selects on `coalesce(next_retry_at, created_at)`. The partial index
        # could never serve that population, so the sweep fell back to a
        # sequential scan on a table that grows per webhook. The predicate
        # now covers every PENDING row; the creating layer says the same.
        Index(
            "idx_webhook_delivery_retry",
            "status",
            "next_retry_at",
            postgresql_where="status = 'pending'",
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
        # Query deliveries by tenant, status, and time (list deliveries filtering/ordering)
        Index(
            "idx_delivery_tenant_status_created",
            "tenant_id",
            "status",
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
