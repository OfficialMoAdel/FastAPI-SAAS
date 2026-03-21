import uuid as uuid_pkg
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column
from uuid6 import uuid7


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
        DateTime,
        insert_default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        init=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        onupdate=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        init=False,
    )


class SoftDeleteMixin(MappedAsDataclass):
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, init=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, insert_default=False, init=False)
