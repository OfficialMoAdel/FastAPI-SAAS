"""Audit log model for tracking system actions and changes."""

from datetime import UTC, datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from src.app.core.db.database import Base


class AuditLog(Base):
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
        entity_id: UUID of affected entity
        details: Flexible JSONB payload with action-specific data
        ip_address: Client IP address
        user_agent: Client user agent string
        created_at: Timestamp when action occurred (auto-set)
    """

    __tablename__ = "audit_logs"

    # Primary Key (with init=False since it's DB-generated)
    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, insert_default=uuid7, server_default=text("gen_random_uuid()"), init=False
    )

    # Foreign Keys (required fields without defaults)
    tenant_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Action Information (required fields)
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
        comment="UUID of affected entity",
    )

    # Request Context (nullable fields)
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

    # Flexible Payload (with default - must come after FK fields)
    details: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default_factory=dict,
        server_default="{}",
        comment="Flexible JSON payload with action-specific details",
    )

    # Timestamp (with init=False, must come after fields with defaults)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        insert_default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        init=False,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(  # type: ignore  # noqa: F821
        "Tenant",
        back_populates="audit_logs",
        init=False,
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
