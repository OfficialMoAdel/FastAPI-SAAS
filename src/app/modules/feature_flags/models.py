"""Feature flag models for tenant-level feature management."""

import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from ...core.db.database import Base

if TYPE_CHECKING:
    from ...modules.tenants.models import Tenant


class FeatureFlag(Base):
    """
    Global feature flag definition.

    These are system-level feature flags that can be overridden
    per-tenant. Each flag has a key, description, default value,
    and metadata for UI presentation.

    Attributes
    ----------
        id: Unique integer identifier
        uuid: Unique UUID identifier
        key: Unique flag identifier (e.g., 'dark_mode', 'beta_features')
        description: Human-readable description of the flag
        default_value: Default value if not overridden by tenant
        value_type: Type of value (boolean, string, number, json)
        is_system: Whether this is a system flag (cannot be deleted)
        created_at: Timestamp when flag was created
        updated_at: Timestamp when flag was last modified
    """

    __tablename__ = "feature_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, insert_default=uuid7, server_default=text("gen_random_uuid()"), init=False
    )

    # Core Attributes
    key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        comment="Unique flag identifier (e.g., 'dark_mode')",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Human-readable description of the flag",
    )
    default_value: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="Default value for new tenants",
    )
    value_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="boolean",
        server_default="boolean",
        comment="Type of value (boolean, string, number, json)",
    )
    flag_metadata: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default_factory=dict,
        server_default="{}",
        comment="Additional metadata for UI (categories, tiers, etc.)",
    )

    # System Flag Indicator
    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="System flags cannot be deleted",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        insert_default=lambda: datetime.now(UTC),
        nullable=False,
        init=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        nullable=True,
        init=False,
    )

    # Relationships
    tenant_overrides: Mapped[list["FeatureFlagOverride"]] = relationship(
        "FeatureFlagOverride",
        back_populates="feature_flag",
        cascade="all, delete-orphan",
        init=False,
    )

    # Indexes
    __table_args__: tuple[Index, Index, dict[str, bool]] = (  # type: ignore[assignment]
        Index("idx_feature_flag_key", "key", unique=True),
        Index("idx_feature_flag_system", "is_system"),
        {"extend_existing": True},
    )

    def __repr__(self) -> str:
        """String representation of feature flag."""
        return f"<FeatureFlag(key='{self.key}', default={self.default_value})>"


class FeatureFlagOverride(Base):
    """
    Tenant-specific feature flag overrides.

    Allows tenants to customize the value of global feature flags
    for their specific use case. Overrides take precedence over
    the default value.

    Attributes
    ----------
        id: Unique integer identifier
        uuid: Unique UUID identifier
        tenant_id: Reference to tenant
        feature_flag_id: Reference to global feature flag
        custom_value: Overridden value for this tenant
        reason: Optional explanation for the override
        created_at: Timestamp when override was created
        updated_at: Timestamp when override was last modified
    """

    __tablename__ = "feature_flag_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, insert_default=uuid7, server_default=text("gen_random_uuid()"), init=False
    )

    # Foreign Keys
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feature_flag_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("feature_flags.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Override Value
    custom_value: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment="Overridden value (can be any JSON type)",
    )
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional explanation for the override",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        insert_default=lambda: datetime.now(UTC),
        nullable=False,
        init=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        nullable=True,
        init=False,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(  # type: ignore
        "Tenant",
        back_populates="feature_flag_overrides",
        init=False,
    )
    feature_flag: Mapped["FeatureFlag"] = relationship(
        "FeatureFlag",
        back_populates="tenant_overrides",
        init=False,
    )

    # Constraints
    __table_args__: tuple[UniqueConstraint, Index, Index, dict[str, bool]] = (  # type: ignore[assignment]
        UniqueConstraint(
            "tenant_id",
            "feature_flag_id",
            name="uq_tenant_feature_flag_override",
        ),
        Index("idx_feature_flag_override_tenant", "tenant_id"),
        Index("idx_feature_flag_override_flag", "feature_flag_id"),
        {"extend_existing": True},
    )

    def __repr__(self) -> str:
        """String representation of feature flag override."""
        return f"<FeatureFlagOverride(tenant_id={self.tenant_id}, flag_id={self.feature_flag_id})>"
