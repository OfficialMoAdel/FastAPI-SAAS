"""Audit log model for tracking system actions and changes."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base
from src.app.core.db.models import TimestampMixin, UUIDMixin


class AuditLog(Base, UUIDMixin, TimestampMixin):
    """
    Audit log for tracking all system actions.

    Immutable table - no UPDATE or DELETE operations allowed.
    Used for compliance, security monitoring, and debugging.

    Attributes:
        id: Unique UUID identifier
        tenant_id: Reference to tenant (multi-tenancy isolation)
        user_id: Reference to user who performed action (nullable)
        action: Action type (e.g., 'user.created', 'post.updated')
        entity_type: Type of entity affected (e.g., 'user', 'post')
        entity_id: UUID of the affected entity
        details: Flexible JSONB payload with action-specific data
        ip_address: Client IP address
        user_agent: Client user agent string
        created_at: Timestamp when action occurred (auto-set)
        updated_at: Not used (inherited from TimestampMixin)
    """

    __tablename__ = "audit_logs"

    # Foreign Keys
    tenant_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Action Information
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Action type in format 'entity.verb' (e.g., 'user.created')",
    )
    entity_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        index=True,
        comment="Type of entity affected",
    )
    entity_id: Mapped[Optional[UUID]] = mapped_column(
        nullable=True,
        comment="UUID of the affected entity",
    )

    # Flexible Payload
    details: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="Flexible JSON payload with action-specific details",
    )

    # Request Context
    ip_address: Mapped[Optional[str]] = mapped_column(
        INET,
        nullable=True,
        comment="Client IP address",
    )
    user_agent: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Client user agent string",
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(  # type: ignore  # noqa: F821
        "Tenant",
        back_populates="audit_logs",
    )
    user: Mapped[Optional["User"]] = relationship(  # type: ignore  # noqa: F821
        "User",
        back_populates="audit_logs",
    )

    # Composite Indexes for Performance
    __table_args__ = (
        # Primary query pattern: filter by tenant and time range
        Index(
            "idx_audit_tenant_created",
            "tenant_id",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        # Query by entity
        Index("idx_audit_entity", "entity_type", "entity_id"),
        # Query by action type
        Index("idx_audit_action_created", "action", "created_at"),
        # JSONB GIN index for flexible querying
        Index(
            "idx_audit_details_gin",
            "details",
            postgresql_using="gin",
        ),
    )

    def __repr__(self) -> str:
        """String representation of audit log entry."""
        return (
            f"<AuditLog(id={self.id}, action='{self.action}', "
            f"entity_type='{self.entity_type}', tenant_id={self.tenant_id})>"
        )
