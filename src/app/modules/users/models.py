import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from src.app.core.db.database import Base

# Avoid circular import by using string reference for secondary if needed,
# or import inside TYPE_CHECKING if only needed for type,
# but for secondary argument we usually need the table object or string.
# Since user_roles is a Table object in another module, we might want to use string "user_roles"
# provided that module is loaded.
# However, to be safe and explicit, let's try to import the module if it doesn't cause cycle.
# rbac.models does NOT import users.models at runtime. So we can import it here?
# Let's try to avoid top-level import if possible to be super safe, or just use string.
# SQLAlchemy resolves string references from the Base.metadata.
# As long as rbac.models is imported somewhere in the app (e.g. main.py or alembic), it works.
# But for development, explicit is better.

if TYPE_CHECKING:
    from src.app.modules.ai.models import AIChatSession, AISettings, AITokenUsage
    from src.app.modules.api_keys.models import APIKey
    from src.app.modules.audit_logs.models import AuditLog
    from src.app.modules.billing.models import BillingRecord, UserSubscription
    from src.app.modules.files.models import File
    from src.app.modules.mfa.models import MFAMethod
    from src.app.modules.notifications.models import Notification
    from src.app.modules.rbac.models import Role
    from src.app.modules.tenants.models import Tenant
    from src.app.modules.tiers.models import Tier


class User(Base, kw_only=True):
    __tablename__ = "users"
    __table_args__: tuple[Index | UniqueConstraint, ...] = (
        # SECURITY FIX: Use partial unique indexes to allow re-registration of soft-deleted emails/usernames
        # This prevents "unique constraint slots" being occupied by deleted records.
        Index(
            "uq_user_email_tenant",
            "email",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index(
            "uq_user_username_tenant",
            "username",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index("idx_users_tenant_email", "tenant_id", "email"),
        Index("idx_users_tenant_username", "tenant_id", "username"),
        Index("idx_users_tenant_deleted", "tenant_id", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)

    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), index=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(30))
    username: Mapped[str] = mapped_column(String(20), index=True)
    email: Mapped[str] = mapped_column(String(50), index=True)  # Uniqueness now per-tenant via constraint
    hashed_password: Mapped[str] = mapped_column(String)

    profile_image_url: Mapped[str] = mapped_column(String, default="https://www.profileimageurl.com")
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(UUID(as_uuid=True), default_factory=uuid7, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default_factory=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    is_deleted: Mapped[bool] = mapped_column(default=False, index=True)
    is_superuser: Mapped[bool] = mapped_column(default=False)
    is_email_verified: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    tier_id: Mapped[int | None] = mapped_column(ForeignKey("tier.id"), index=True, default=None, init=False)

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="users", init=False)
    roles: Mapped[list["Role"]] = relationship(
        secondary="user_roles",
        back_populates="users",
        init=False,
        viewonly=True,
        lazy="selectin",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="user", init=False, cascade="all, delete-orphan", lazy="selectin"
    )
    files: Mapped[list["File"]] = relationship(
        back_populates="user", init=False, cascade="all, delete-orphan", lazy="selectin"
    )
    api_keys: Mapped[list["APIKey"]] = relationship(
        back_populates="user", init=False, cascade="all, delete-orphan", lazy="selectin"
    )
    mfa_methods: Mapped[list["MFAMethod"]] = relationship(
        back_populates="user", init=False, cascade="all, delete-orphan", lazy="selectin"
    )
    # Billing & AI relationships (Phase 3/5)
    user_subscriptions: Mapped[list["UserSubscription"]] = relationship(
        back_populates="user", init=False, cascade="all, delete-orphan", lazy="selectin"
    )
    billing_records: Mapped[list["BillingRecord"]] = relationship(
        back_populates="user", init=False, cascade="all, delete-orphan", lazy="selectin"
    )
    ai_chat_sessions: Mapped[list["AIChatSession"]] = relationship(
        back_populates="user", init=False, cascade="all, delete-orphan", lazy="selectin"
    )
    ai_settings: Mapped["AISettings | None"] = relationship(
        back_populates="user", uselist=False, init=False, cascade="all, delete-orphan"
    )
    ai_token_usage: Mapped[list["AITokenUsage"]] = relationship(
        back_populates="user", init=False, cascade="all, delete-orphan", lazy="selectin"
    )
    tier: Mapped["Tier | None"] = relationship(init=False, lazy="selectin")
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="user", init=False, cascade="all, delete-orphan", lazy="selectin"
    )
