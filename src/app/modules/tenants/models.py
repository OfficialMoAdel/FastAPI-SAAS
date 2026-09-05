import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from src.app.core.db.database import Base
from src.app.core.logger import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from src.app.modules.ai.models import AIChatSession, AITokenUsage
    from src.app.modules.api_keys.models import APIKey
    from src.app.modules.audit_logs.models import AuditLog
    from src.app.modules.billing.models import Product, StripeSettings, SubscriptionPlan
    from src.app.modules.feature_flags.models import FeatureFlagOverride
    from src.app.modules.notifications.models import EmailTemplateOverride, Notification
    from src.app.modules.storage.models import File, StorageSettings
    from src.app.modules.users.models import User
    from src.app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint


class Tenant(Base):
    """Tenant model for multi-tenant organization management.

    Represents an organization/customer in the multi-tenant system.
    Used for row-level data isolation via tenant_id foreign keys.
    """

    __tablename__ = "tenants"

    id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        insert_default=uuid7,
        server_default=text("gen_random_uuid()"),
        init=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    feature_flags: Mapped[dict] = mapped_column(JSONB, nullable=False, default_factory=dict, server_default="{}")

    # Storage quota counters (denormalized for O(1) quota checks)
    storage_usage_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, onupdate=lambda: datetime.now(UTC)
    )

    def set_feature_flag(self, name: str, value: Any) -> None:
        """Set or update a single feature flag value."""
        from sqlalchemy.orm.attributes import flag_modified

        if self.feature_flags is None:
            self.feature_flags = {}
        self.feature_flags[name] = value
        flag_modified(self, "feature_flags")

    def remove_feature_flag(self, name: str) -> bool:
        """Remove a feature flag value if present. Returns True if removed."""
        from sqlalchemy.orm.attributes import flag_modified

        if self.feature_flags is not None and name in self.feature_flags:
            del self.feature_flags[name]
            flag_modified(self, "feature_flags")
            return True
        return False

    def update_feature_flags(self, flags: dict[str, Any]) -> None:
        """Bulk update feature flags."""
        from sqlalchemy.orm.attributes import flag_modified

        if self.feature_flags is None:
            self.feature_flags = {}
        self.feature_flags.update(flags)
        flag_modified(self, "feature_flags")

    # Relationships
    # NOTE ON LAZY LOADING STRATEGY:
    # - Large collections (users, audit_logs, etc.) keep default lazy="select" to
    #   avoid loading thousands of rows on every Tenant fetch.
    # - Singleton/scalar associations (storage_settings, stripe_settings) use
    #   lazy="selectin" because they are almost always accessed alongside the tenant
    #   and the extra subquery cost is negligible (one additional tiny query).
    users: Mapped[list["User"]] = relationship(back_populates="tenant", init=False, lazy="noload")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="tenant", init=False, lazy="noload")
    email_template_overrides: Mapped[list["EmailTemplateOverride"]] = relationship(
        back_populates="tenant", init=False, lazy="noload"
    )
    webhook_endpoints: Mapped[list["WebhookEndpoint"]] = relationship(
        back_populates="tenant", init=False, lazy="noload"
    )
    webhook_deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        back_populates="tenant", init=False, lazy="noload"
    )
    feature_flag_overrides: Mapped[list["FeatureFlagOverride"]] = relationship(
        back_populates="tenant", init=False, lazy="noload"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="tenant", init=False, lazy="noload")
    # Scalar associations — use selectin to avoid N+1 when loading tenant context
    storage_settings: Mapped["StorageSettings | None"] = relationship(
        back_populates="tenant", uselist=False, init=False, lazy="selectin"
    )
    files: Mapped[list["File"]] = relationship(back_populates="tenant", init=False, lazy="noload")
    api_keys: Mapped[list["APIKey"]] = relationship(back_populates="tenant", init=False, lazy="noload")
    # Billing & AI relationships (Phase 3/5)
    stripe_settings: Mapped["StripeSettings | None"] = relationship(
        back_populates="tenant", uselist=False, init=False, lazy="selectin"
    )
    subscription_plans: Mapped[list["SubscriptionPlan"]] = relationship(
        back_populates="tenant", init=False, lazy="noload"
    )
    products: Mapped[list["Product"]] = relationship(back_populates="tenant", init=False, lazy="noload")
    ai_chat_sessions: Mapped[list["AIChatSession"]] = relationship(back_populates="tenant", init=False, lazy="noload")
    ai_token_usage: Mapped[list["AITokenUsage"]] = relationship(back_populates="tenant", init=False, lazy="noload")


# Ensure all related models are imported so SQLAlchemy's mapper can resolve
# forward-reference strings in relationship() declarations like relationship("StripeSettings").
# All imports in the TYPE_CHECKING block above are type-hint-only; SQLAlchemy needs the
# actual classes loaded at configure time. This block must be at module bottom to
# avoid circular imports since these modules import from tenants.models.
def _ensure_related_models_registered() -> None:
    """Import all models referenced in Tenant relationships."""
    import importlib

    for module_path in [
        "src.app.modules.ai.models",
        "src.app.modules.api_keys.models",
        "src.app.modules.audit_logs.models",
        "src.app.modules.billing.models",
        "src.app.modules.feature_flags.models",
        "src.app.modules.storage.models",
        "src.app.modules.notifications.models",
        "src.app.modules.users.models",
        "src.app.modules.webhooks.models",
    ]:
        try:
            importlib.import_module(module_path)
        except ImportError as e:
            logger.warning("tenant_related_model_import_failed", module=module_path, error=str(e))


_ensure_related_models_registered()
