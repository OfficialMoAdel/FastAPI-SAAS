"""Single model-registration point (A-M6).

Imports every SQLAlchemy model so the shared ``Base.metadata``/registry is
complete at mapper-configure time. This file — not any individual module —
is the composition seam: processes that touch mappers (web app via
core/setup.py, ARQ worker via core/worker/settings.py, Alembic autogenerate,
tests) import ``src.app.models`` and get the full registry, while model
modules stay free of circular force-imports (see tenants/models.py).
"""

# Core models
# Auth & Security models
from src.app.core.db.token_blacklist import TokenBlacklist  # noqa: F401
from src.app.modules.ai.models import AIChatMessage, AIChatSession, AISettings, AITokenUsage  # noqa: F401
from src.app.modules.api_keys.models import APIKey  # noqa: F401
from src.app.modules.audit_logs.models import AuditLog  # noqa: F401
from src.app.modules.auth.models import (
    EmailVerificationToken,
    MagicLink,
    MFAMethod,
    PasskeyCredential,
    PasswordResetToken,
    SSOSettings,
    UserSSOConnection,
)
from src.app.modules.billing.models import (
    BillingRecord,
    Pricing,
    Product,
    StripeSettings,
    SubscriptionPlan,
    UserSubscription,
    WebhookEvent,
)  # noqa: F401
from src.app.modules.feature_flags.models import FeatureFlag, FeatureFlagOverride  # noqa: F401
from src.app.modules.notifications.models import EmailTemplateOverride, Notification  # noqa: F401
from src.app.modules.posts.models import Post  # noqa: F401
from src.app.modules.rate_limits.models import RateLimit, Tier  # noqa: F401
from src.app.modules.rbac.models import Permission, Role, RolePermission, UserRole  # noqa: F401
from src.app.modules.storage.models import File, StorageProviderType, StorageSettings  # noqa: F401
from src.app.modules.tenants.models import Tenant  # noqa: F401
from src.app.modules.tiers.models import Tier as TierAlias  # noqa: F401
from src.app.modules.user_preferences.models import UserPreferences  # noqa: F401
from src.app.modules.users.models import User  # noqa: F401
from src.app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint  # noqa: F401

__all__ = [
    "AISettings",
    "APIKey",
    "AIChatMessage",
    "AIChatSession",
    "AITokenUsage",
    "AuditLog",
    "BillingRecord",
    "EmailTemplateOverride",
    "EmailVerificationToken",
    "FeatureFlag",
    "FeatureFlagOverride",
    "File",
    "MagicLink",
    "MFAMethod",
    "Notification",
    "PasskeyCredential",
    "PasswordResetToken",
    "Permission",
    "Post",
    "Pricing",
    "Product",
    "RateLimit",
    "Role",
    "RolePermission",
    "SSOSettings",
    "StorageProviderType",
    "StorageSettings",
    "StripeSettings",
    "SubscriptionPlan",
    "Tenant",
    "Tier",
    "TierAlias",
    "TokenBlacklist",
    "User",
    "UserPreferences",
    "UserRole",
    "UserSSOConnection",
    "UserSubscription",
    "WebhookDelivery",
    "WebhookEndpoint",
    "WebhookEvent",
]
