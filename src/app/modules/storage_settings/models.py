"""Storage Settings SQLAlchemy models.

This module defines the StorageSettings model for per-tenant storage configuration,
including encrypted credential storage and quota settings.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant


class StorageProviderType(str, Enum):
    """Supported storage provider types."""

    S3 = "s3"
    AZURE = "azure"
    LOCAL = "local"


class StorageSettings(Base):
    """Per-tenant storage configuration model.

    Stores provider credentials (encrypted), configuration limits,
    and allowed file types for each tenant's file storage.

    Attributes:
        id: Unique identifier.
        tenant_id: Foreign key to tenant (unique - one settings per tenant).
        provider: Storage provider type (s3/azure/local).
        credentials_encrypted: Encrypted JSON containing provider credentials.
        bucket_name: S3 bucket or Azure container name.
        region: AWS region for S3.
        max_file_size_mb: Maximum file size in MB (default: 100).
        allowed_file_types: JSON array of allowed MIME types.
        max_file_count: Maximum number of files per tenant (default: 10000).
        storage_quota_bytes: Total storage quota in bytes (default: 10GB).
        created_at: When settings were created.
        updated_at: When settings were last modified.
    """

    __tablename__ = "storage_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, init=False)

    # Tenant relationship (one-to-one)
    tenant_id: Mapped[int] = mapped_column(
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
        default=100,
    )

    allowed_file_types: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        insert_default=["image/jpeg", "image/png", "image/gif", "application/pdf"],
        default=None,
    )

    # Quota settings (from clarification Q3)
    max_file_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10000,
    )

    storage_quota_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=10 * 1024 * 1024 * 1024,  # 10GB
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        insert_default=datetime.utcnow,
        default=None,
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        onupdate=datetime.utcnow,
    )

    # Relationship
    tenant: Mapped[Tenant] = relationship(back_populates="storage_settings", init=False)

    def __repr__(self) -> str:
        return f"<StorageSettings(tenant_id={self.tenant_id}, provider={self.provider})>"