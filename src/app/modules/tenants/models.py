from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.ai.models import AIChatSession
    from src.app.modules.audit_logs.models import AuditLog
    from src.app.modules.billing.models import Product, StripeSettings, SubscriptionPlan
    from src.app.modules.feature_flags.models import FeatureFlagOverride
    from src.app.modules.files.models import File
    from src.app.modules.notifications.models import EmailTemplateOverride, Notification
    from src.app.modules.storage_settings.models import StorageSettings
    from src.app.modules.users.models import User
    from src.app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint


class Tenant(Base):
    """Tenant model for multi-tenant organization management.

    Represents an organization/customer in the multi-tenant system.
    Used for row-level data isolation via tenant_id foreign keys.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    feature_flags: Mapped[dict] = mapped_column(JSONB, nullable=False, default_factory=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True)

    # Relationships
    users: Mapped[list["User"]] = relationship(back_populates="tenant", init=False)
    notifications: Mapped[list["Notification"]] = relationship(back_populates="tenant", init=False)
    email_template_overrides: Mapped[list["EmailTemplateOverride"]] = relationship(back_populates="tenant", init=False)
    webhook_endpoints: Mapped[list["WebhookEndpoint"]] = relationship(back_populates="tenant", init=False)
    webhook_deliveries: Mapped[list["WebhookDelivery"]] = relationship(back_populates="tenant", init=False)
    feature_flag_overrides: Mapped[list["FeatureFlagOverride"]] = relationship(back_populates="tenant", init=False)
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="tenant", init=False)
    storage_settings: Mapped["StorageSettings | None"] = relationship(
        back_populates="tenant", uselist=False, init=False
    )
    files: Mapped[list["File"]] = relationship(back_populates="tenant", init=False)
    # Billing & AI relationships (Phase 3/5)
    stripe_settings: Mapped["StripeSettings | None"] = relationship(
        back_populates="tenant", uselist=False, init=False
    )
    subscription_plans: Mapped[list["SubscriptionPlan"]] = relationship(
        back_populates="tenant", init=False
    )
    products: Mapped[list["Product"]] = relationship(back_populates="tenant", init=False)
    ai_chat_sessions: Mapped[list["AIChatSession"]] = relationship(
        back_populates="tenant", init=False
    )
