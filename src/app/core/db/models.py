import uuid as uuid_pkg
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column
from uuid6 import uuid7

from src.app.core.db.database import Base


class AuditAction(str, Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    LOGOUT = "logout"
    FAILED_LOGIN = "failed_login"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class WebhookDeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class UUIDMixin(MappedAsDataclass):
    id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        insert_default=uuid7,
        server_default=text("gen_random_uuid()"),
        init=False,
    )


class TimestampMixin(MappedAsDataclass):
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        insert_default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        init=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        init=False,
    )


class SoftDeleteMixin(MappedAsDataclass):
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, init=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, insert_default=False, init=False)


class RLSAuditLog(Base):
    __tablename__ = "rls_audit_log"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, init=False)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        unique=True,
        server_default=text("gen_random_uuid()"),
        insert_default=uuid7,
        init=False,
    )
    # S-46 FIX: the DB column is VARCHAR(100) (migration 07_observability) and
    # that is what this must keep matching. The model previously declared
    # `Integer`, which contradicted the schema.
    #
    # Severity note: this was filed as High because the stated consequence was
    # "every bypass_rls with a numeric actor aborts with RLSAuditFailureError".
    # A repo-wide grep for `bypass_rls(` callers shows NONE pass `user_id=`,
    # so that column is permanently NULL today and the bug is dormant — it was
    # demoted from High for that reason.
    #
    # It is still fixed, because the drift is a trap for whoever adds the
    # FIRST caller that passes an actor id: asyncpg would hand an int to a
    # varchar column and the write would fail at runtime. Matching the schema
    # removes the trap rather than leaving it for the next change.
    user_id: Mapped[str | None] = mapped_column(String(100), nullable=True, init=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, init=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), insert_default=lambda: datetime.now(UTC), init=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True, init=False)
    table_name: Mapped[str | None] = mapped_column(String(100), nullable=True, init=False)
    operation: Mapped[str | None] = mapped_column(String(50), nullable=True, init=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), insert_default=lambda: datetime.now(UTC), init=False
    )
