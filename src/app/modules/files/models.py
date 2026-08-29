"""File metadata SQLAlchemy models.

This module defines the File model for storing file metadata,
with tenant isolation and user ownership tracking.
"""

from __future__ import annotations

import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant
    from src.app.modules.users.models import User


class File(Base):
    """File metadata model.

    Stores metadata about uploaded files, with references to the
    actual file location in the configured storage provider.

    Tenant isolation is enforced via tenant_id FK.
    User ownership is tracked but set to NULL on user deletion
    (per clarification Q2), then transferred to tenant admin.

    Attributes:
        id: Unique identifier.
        tenant_id: Foreign key to tenant (CASCADE delete).
        user_id: Foreign key to user (SET NULL on delete).
        original_name: Original filename from upload.
        storage_path: Path in the storage provider.
        mime_type: MIME type of the file.
        size_bytes: File size in bytes.
        is_public: Whether file is publicly accessible via signed URL.
        created_at: When file was uploaded.
        updated_at: When metadata was last modified.
    """

    __tablename__ = "files"
    __table_args__ = (
        Index("idx_files_tenant_user_created", "tenant_id", "user_id", "created_at"),
        # R2-HYBRID MED-5: Composite index for MIME type filtering
        # Enables O(1) lookup for queries like: WHERE tenant_id=? AND mime_type=?
        Index("ix_files_tenant_mime", "tenant_id", "mime_type"),
        # N5: unfiltered list is ORDER BY created_at DESC with tenant filter;
        # the 3-column (tenant_id, user_id, created_at) index cannot serve it.
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

    # User relationship (SET NULL on delete - per clarification Q2)
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
        String(127),
        nullable=False,
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
        nullable=False,
        insert_default=lambda: datetime.now(UTC),
        default=None,
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship(back_populates="files", init=False, lazy="selectin")
    user: Mapped[User | None] = relationship(back_populates="files", init=False, lazy="selectin")

    def __repr__(self) -> str:
        return f"<File(id={self.id}, name={self.original_name}, tenant_id={self.tenant_id})>"
