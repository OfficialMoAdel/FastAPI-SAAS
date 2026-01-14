"""Initialize test database with all tables.

This script creates all base tables from SQLAlchemy models,
then stamps alembic to HEAD so migrations don't try to alter
already-existing tables.

Usage:
    cd D:\\project\\FastAPI_saas
    $env:POSTGRES_DB = "test_db"
    uv run python scripts/init_test_db.py
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


async def init_test_db():
    """Create all tables and stamp alembic to HEAD."""
    # Import after path setup
    from app.core.db.database import Base, async_engine

    # Import all models from the central models module
    # This registers them with Base.metadata
    from app.models import (  # noqa: F401
        APIKey,
        AuditLog,
        EmailVerificationToken,
        PasswordResetToken,
        FeatureFlagOverride,
        File,
        MagicLink,
        EmailTemplateOverride,
        Notification,
        PasskeyCredential,
        Post,
        RateLimit,
        Permission,
        Role,
        RolePermission,
        UserRole,
        UserSSOConnection,
        StorageSettings,
        Tenant,
        Tier,
        UserPreferences,
        User,
        WebhookDelivery,
        WebhookEndpoint,
    )

    # Import additional models not in the central module
    from app.modules.sso_settings.models import SSOSettings  # noqa: F401
    from app.modules.billing.models import (  # noqa: F401
        StripeSettings,
        SubscriptionPlan,
        Product,
        Pricing,
        UserSubscription,
        BillingRecord,
    )
    from app.modules.ai.models import (  # noqa: F401
        AIChatSession,
        AIChatMessage,
        AISettings,
    )
    from app.core.db.token_blacklist import TokenBlacklist  # noqa: F401

    print(f"Creating tables in database...")
    print(f"Engine URL: {async_engine.url}")

    async with async_engine.begin() as conn:
        # Drop all tables first for clean state
        await conn.run_sync(Base.metadata.drop_all)
        print("Dropped existing tables.")

        # Create all tables
        await conn.run_sync(Base.metadata.create_all)
        print("Created all tables from SQLAlchemy models.")

    await async_engine.dispose()
    print("\nTest database initialized successfully!")
    print("\nNow run: uv run alembic stamp head")
    print("This marks all migrations as applied (tables already exist).")


if __name__ == "__main__":
    asyncio.run(init_test_db())
