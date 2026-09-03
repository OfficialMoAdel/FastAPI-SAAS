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

from sqlalchemy import text

# Add src to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


async def init_test_db():
    """Create all tables and stamp alembic to HEAD."""
    # Import after path setup
    from app.core.db.database import Base, async_engine
    from app.core.db.token_blacklist import TokenBlacklist  # noqa: F401

    # Import all models from the central models module
    # This registers them with Base.metadata
    from app.models import (  # noqa: F401
        APIKey,
        AuditLog,
        EmailTemplateOverride,
        EmailVerificationToken,
        FeatureFlagOverride,
        # File, # Imported via app.models to avoid circular dependency
        MagicLink,
        Notification,
        PasskeyCredential,
        PasswordResetToken,
        Permission,
        Post,
        RateLimit,
        Role,
        RolePermission,
        StorageSettings,
        Tenant,
        Tier,
        User,
        UserPreferences,
        UserRole,
        UserSSOConnection,
        WebhookDelivery,
        WebhookEndpoint,
    )
    from app.modules.ai.models import (  # noqa: F401
        AIChatMessage,
        AIChatSession,
        AISettings,
    )
    from app.modules.billing.models import (  # noqa: F401
        BillingRecord,
        Pricing,
        Product,
        StripeSettings,
        SubscriptionPlan,
        UserSubscription,
    )


    print("Creating tables in database...")
    print(f"Engine URL: {async_engine.url}")

    async with async_engine.begin() as conn:
        # Drop all tables with CASCADE to handle foreign key dependencies
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE;"))
        await conn.execute(text("CREATE SCHEMA public;"))
        print("Dropped existing schema with CASCADE.")

        # Create all tables
        await conn.run_sync(Base.metadata.create_all)
        print("Created all tables from SQLAlchemy models.")

    await async_engine.dispose()
    print("\nTest database initialized successfully!")
    print("\nNow run: uv run alembic stamp head")
    print("This marks all migrations as applied (tables already exist).")


if __name__ == "__main__":
    asyncio.run(init_test_db())
