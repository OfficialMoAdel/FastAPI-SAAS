import uuid as uuid_pkg
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from src.app.core.db.database import Base


class Post(Base):
    __tablename__ = "post"

    __table_args__ = (
        Index("idx_posts_tenant_user_deleted", "tenant_id", "created_by_user_id", "is_deleted"),
        # P2 keyset composite: covers get_posts_keyset WHERE
        # (tenant_id, created_by_user_id, is_deleted) + ORDER BY
        # created_at DESC, id DESC exactly (equality prefix + ordered scan).
        # Declared here so autogenerate no longer emits DROP INDEX for it.
        Index(
            "idx_posts_tenant_user_created_id",
            "tenant_id",
            "created_by_user_id",
            "is_deleted",
            "created_at",
            "id",
            postgresql_ops={"created_at": "DESC", "id": "DESC"},
        ),
    )

    id: Mapped[int] = mapped_column("id", autoincrement=True, nullable=False, unique=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), index=True, nullable=False
    )
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(String(63206))
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(UUID(as_uuid=True), default_factory=uuid7, unique=True)
    media_url: Mapped[str | None] = mapped_column(String, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default_factory=lambda: datetime.now(UTC))
    # `default_factory`, NOT `default=None`. Base is a MappedAsDataclass, so
    # `default_factory` becomes the dataclass field default and is applied when
    # the instance is CONSTRUCTED -- which is why Column.default reads None for
    # this attribute and why reading Column.default is the wrong way to audit it.
    # `default=None` means the ORM sends an explicit NULL, and
    # 01_foundation.py declares post.updated_at NOT NULL, so every insert raised
    # NotNullViolationError and both POST /api/v1/posts and
    # POST /api/v1/posts/by-user/{username} answered 500 for every user.
    # Measured on perf-local: `Post(...).updated_at` was None before flush while
    # `User(...).updated_at` (users/models.py, same shape) carried a real
    # timestamp and inserted fine. This is the only word that differed.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    is_deleted: Mapped[bool] = mapped_column(default=False, index=True)
