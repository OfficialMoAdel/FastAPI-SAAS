"""Notification models for email delivery tracking and template management."""

import enum
import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...core.db.database import Base
from ...core.db.models import UUIDMixin

if TYPE_CHECKING:
    from ...modules.tenants.models import Tenant
    from ...modules.users.models import User


class NotificationStatus(str, enum.Enum):
    """Status of notification delivery."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class Notification(Base, UUIDMixin):
    """
    Notification record for tracking email deliveries.

    Tracks all email notifications sent through the system with
    delivery status, error messages, and retry information.

    Attributes
    ----------
        id: Unique UUID identifier
        tenant_id: Reference to tenant (multi-tenancy isolation)
        user_id: Reference to recipient user
        template_name: Name of email template used
        to_email: Recipient email address
        subject: Email subject line
        status: Delivery status (pending/sent/failed)
        sent_at: Timestamp when email was successfully sent
        error_message: Error details if delivery failed
        created_at: Timestamp when notification was created
        updated_at: Timestamp when status last changed
    """

    __tablename__ = "notifications"

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: datetime.now(UTC), nullable=False, init=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, init=False
    )

    # Foreign Keys
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Email Information
    template_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Template name (e.g., 'welcome', 'password_reset')",
    )
    to_email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Recipient email address",
    )
    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Email subject line",
    )

    # Delivery Status
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, native_enum=False, length=20),
        nullable=False,
        default=NotificationStatus.PENDING,
        index=True,
        comment="Current delivery status",
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        init=False,
        comment="Timestamp when email was successfully sent",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        init=False,
        comment="Error details if delivery failed",
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(  # type: ignore
        "Tenant",
        back_populates="notifications",
        init=False,
    )
    user: Mapped[Optional["User"]] = relationship(  # type: ignore
        "User",
        back_populates="notifications",
        init=False,
    )

    # Composite Indexes for Performance
    __table_args__ = (
        # Query by tenant and status
        Index("idx_notification_tenant_status", "tenant_id", "status"),
        # Query by tenant and creation time
        Index(
            "idx_notification_tenant_created",
            "tenant_id",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        # Query failed notifications for retry
        Index(
            "idx_notification_status_created",
            "status",
            "created_at",
            postgresql_where="status = 'failed'",
        ),
    )

    def __repr__(self) -> str:
        """String representation of notification."""
        return (
            f"<Notification(id={self.id}, template='{self.template_name}', "
            f"to='{self.to_email}', status={self.status.value})>"
        )


class EmailTemplateOverride(Base, UUIDMixin):
    """
    Tenant-specific email template overrides.

    Allows tenants to customize default email templates with
    their own subject lines and HTML content.

    Attributes
    ----------
        id: Unique UUID identifier
        tenant_id: Reference to tenant (multi-tenancy isolation)
        template_name: Name of template to override
        subject_override: Custom subject line (optional)
        content: Custom HTML template content
        created_at: Timestamp when override was created
        updated_at: Timestamp when override was last modified
    """

    __tablename__ = "email_template_overrides"

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: datetime.now(UTC), nullable=False, init=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, init=False
    )

    # Foreign Keys
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Template Information
    template_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Template name to override",
    )
    subject_override: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Custom subject line (null = use default)",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Custom HTML template content (Jinja2)",
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(  # type: ignore
        "Tenant",
        back_populates="email_template_overrides",
        init=False,
    )

    # Constraints
    __table_args__ = (
        # One override per template per tenant
        UniqueConstraint(
            "tenant_id",
            "template_name",
            name="uq_email_template_tenant",
        ),
        # Index for template lookups
        Index("idx_email_template_tenant_name", "tenant_id", "template_name"),
    )

    def __repr__(self) -> str:
        """String representation of email template override."""
        return f"<EmailTemplateOverride(id={self.id}, template='{self.template_name}', tenant_id={self.tenant_id})>"
