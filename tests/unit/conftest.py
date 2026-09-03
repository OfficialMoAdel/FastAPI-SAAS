"""Unit test conftest — pre-imports all SQLAlchemy models into the mapper registry.

Without this, tests that import only a subset of models (e.g. just `User` via `CRUDUser`)
cause `configure_mappers()` to run with incomplete model registrations.

SQLAlchemy resolves relationship string references (like `"Notification"` in
`Tenant.notifications`) lazily at mapper configure time. If the target class
has never been imported, it raises `InvalidRequestError`, crashing any test
that triggers mapper configuration.

FIX: Import all models here so every mapper is registered before any test runs.
"""

import os

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef0123456789abcdef12")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdmin123!@#456")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("POSTGRES_DB", "test_db")
os.environ.setdefault("POSTGRES_SERVER", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("REDIS_QUEUE_HOST", "localhost")
os.environ.setdefault("REDIS_QUEUE_PORT", "6379")
os.environ.setdefault("REDIS_CACHE_HOST", "localhost")
os.environ.setdefault("REDIS_CACHE_PORT", "6379")

# ruff: noqa: F401
# fmt: off

# Core models -- always first (Base must exist before any model)
import src.app.core.db.database  # noqa: F401

# All domain models — must be imported to register with SQLAlchemy mapper
import src.app.modules.ai.models  # noqa: F401
import src.app.modules.api_keys.models  # noqa: F401
import src.app.modules.audit_logs.models  # noqa: F401
import src.app.modules.auth.models  # noqa: F401
import src.app.modules.billing.models  # noqa: F401
import src.app.modules.feature_flags.models  # noqa: F401
import src.app.modules.notifications.models  # noqa: F401  ← fixes Tenant.notifications
import src.app.modules.rbac.models  # noqa: F401
import src.app.modules.storage.models  # noqa: F401
import src.app.modules.tenants.models  # noqa: F401
import src.app.modules.tiers.models  # noqa: F401
import src.app.modules.users.models  # noqa: F401
import src.app.modules.webhooks.models  # noqa: F401
