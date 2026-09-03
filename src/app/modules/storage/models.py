"""Storage SQLAlchemy models.

This module unifies file metadata (File) and per-tenant storage provider
configuration (StorageSettings), providing full tenant isolation and
encrypted credential management.
"""

from __future__ import annotations

import uuid as uuid_pkg
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant
    from src.app.modules.users.models import User


class StorageProviderType(str, Enum):
    """Supported storage provider types."""

    S3 = "s3"
    AZURE = "azure"
    LOCAL = "local"


class StorageSettings(Base):
    """Per-tenant storage configuration model.

    Stores provider selection, encrypted credentials, and quota limits
    for a specific tenant. One-to-one with Tenant.

    Attributes:
        id: Primary key.
        tenant_id: Foreign key to tenant (one-to-one).
        provider: Storage provider type (s3, azure, local).
        credentials_encrypted: Encrypted JSON credentials.
        bucket_name: S3 bucket or Azure container name.
        region: S3 region.
        max_file_size_mb: Maximum allowed file size in megabytes.
        allowed_file_types: JSON list of allowed MIME types.
        max_file_count: Maximum number of files allowed for this tenant.
        storage_quota_bytes: Maximum storage space in bytes.
        created_at: When settings were created.
        updated_at: When settings were last modified.
    """

    __tablename__ = "storage_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, init=False)

    # Tenant relationship (one-to-one)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Provider configuration
    provider: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=StorageProviderType.LOCAL.value,
    )

    # Encrypted credentials (JSON string encrypted with Fernet)
    # Contains: access_key, secret_key (S3), connection_string (Azure), base_path (Local)
    credentials_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
        default=None,
    )

    # Provider-specific settings
    bucket_name: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    region: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)

    # File limits
    max_file_size_mb: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,  # 100 MB default
    )
    allowed_file_types: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        default_factory=lambda: ["image/jpeg", "image/png", "image/gif", "application/pdf"],
    )

    # Quota settings
    max_file_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10000,
    )
    storage_quota_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=10 * 1024 * 1024 * 1024,  # 10 GB
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        nullable=True,
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationship
    tenant: Mapped[Tenant] = relationship(back_populates="storage_settings", init=False, lazy="selectin")

    def __repr__(self) -> str:
        return f"<StorageSettings(tenant_id={self.tenant_id}, provider={self.provider})>"


class File(Base):
    """File metadata model.

    Stores metadata about uploaded files, including storage path,
    size, MIME type, and ownership. Physical files are stored
    in the configured storage provider.

    Attributes:
        id: Primary key.
        tenant_id: Tenant UUID for isolation.
        user_id: User who uploaded the file (SET NULL on delete).
        original_name: Original filename from upload.
        storage_path: Relative path in storage provider.
        mime_type: MIME type of the file.
        size_bytes: Size of the file in bytes.
        is_public: Whether file is publicly accessible.
        description: Optional user description.
        created_at: When file was uploaded.
        updated_at: When metadata was last modified.
    """

    __tablename__ = "files"
    __table_args__ = (
        Index("idx_files_tenant_user_created", "tenant_id", "user_id", "created_at"),
        # R2-HYBRID MED-5: Composite index for MIME type filtering
        Index("ix_files_tenant_mime", "tenant_id", "mime_type"),
        # N5: unfiltered list is ORDER BY created_at DESC with tenant filter
        Index(
            "idx_files_tenant_created",
            "tenant_id",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        {"extend_existing": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, init=False)

    # Tenant relationship (CASCADE delete - files deleted with tenant)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # User relationship (SET NULL on delete)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # File metadata
    original_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    storage_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        unique=True,
        index=True,
    )
    mime_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    # Visibility
    is_public: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Optional description
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        nullable=True,
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship(back_populates="files", init=False, lazy="selectin")
    user: Mapped[User | None] = relationship(back_populates="files", init=False, lazy="selectin")

    def __repr__(self) -> str:
        return f"<File(id={self.id}, name={self.original_name}, tenant_id={self.tenant_id})>"


__all__ = [
    "StorageProviderType",
    "StorageSettings",
    "File",
]
