"""Unit test conftest — pre-imports all SQLAlchemy models into the mapper registry.

Without this, tests that import only a subset of models (e.g. just `User` via `CRUDUser`)
cause `configure_mappers()` to run with incomplete model registrations.

SQLAlchemy resolves relationship string references (like `"Notification"` in
`Tenant.notifications`) lazily at mapper configure time. If the target class
has never been imported, it raises `InvalidRequestError`, crashing any test
that triggers mapper configuration.

FIX: Import all models here so every mapper is registered before any test runs.
"""

# ruff: noqa: F401
# fmt: off

# Core models -- always first (Base must exist before any model)
import src.app.core.db.database  # noqa: F401

# All domain models — must be imported to register with SQLAlchemy mapper
import src.app.modules.ai.models  # noqa: F401
import src.app.modules.api_keys.models  # noqa: F401
import src.app.modules.audit_logs.models  # noqa: F401
import src.app.modules.billing.models  # noqa: F401
import src.app.modules.feature_flags.models  # noqa: F401
import src.app.modules.files.models  # noqa: F401
import src.app.modules.mfa.models  # noqa: F401
import src.app.modules.notifications.models  # noqa: F401  ← fixes Tenant.notifications
import src.app.modules.rbac.models  # noqa: F401
import src.app.modules.storage_settings.models  # noqa: F401
import src.app.modules.tenants.models  # noqa: F401
import src.app.modules.tiers.models  # noqa: F401
import src.app.modules.users.models  # noqa: F401
import src.app.modules.webhooks.models  # noqa: F401
