"""v1 core-services router group (P2 aggregator).

Composition-only: the include order here preserves the relative order of
the former flat 35-router list in ``api/v1/__init__.py`` so OpenAPI paths
(and route registration order for same-prefix routes) stay stable. Billing
lives in ``billing.py``; the superuser admin mounts live in ``admin.py``.
The AI router keeps its historical position (after files, before billing).
"""

from fastapi import APIRouter

from src.app.modules.ai.router import router as ai_router
from src.app.modules.api_keys.router import router as api_keys_router
from src.app.modules.app_configuration.router import router as app_configuration_router
from src.app.modules.audit_logs.router import router as audit_logs_router
from src.app.modules.auth import (
    auth_router,
    magic_link_router,
    mfa_router,
    passkey_router,
    sso_router,
    sso_settings_router,
)
from src.app.modules.dashboard.router import router as dashboard_router
from src.app.modules.feature_flags.router import router as feature_flags_router
from src.app.modules.health.router import router as health_router
from src.app.modules.notifications.router import router as notifications_router
from src.app.modules.posts.router import router as posts_router
from src.app.modules.rate_limits.router import router as rate_limits_router
from src.app.modules.rbac.permissions_router import router as permissions_router
from src.app.modules.rbac.router import router as rbac_router
from src.app.modules.security.router import router as security_router
from src.app.modules.storage.router import files_router, storage_settings_router
from src.app.modules.tasks.router import router as tasks_router
from src.app.modules.tenants.router import router as tenants_router
from src.app.modules.tiers.router import router as tiers_router
from src.app.modules.user_preferences.router import router as user_preferences_router
from src.app.modules.users import users_router  # facade re-export (TID251)
from src.app.modules.webhooks.router import router as webhooks_router

router = APIRouter()
router.include_router(health_router)
router.include_router(app_configuration_router)  # Public configuration endpoint
router.include_router(auth_router)
router.include_router(magic_link_router)
router.include_router(passkey_router)
router.include_router(security_router)  # Passkey convenience wrapper
router.include_router(mfa_router)  # MFA routes (requires auth)
router.include_router(users_router)
router.include_router(user_preferences_router)
router.include_router(posts_router)
router.include_router(tasks_router)
router.include_router(tiers_router)
router.include_router(rate_limits_router)
router.include_router(tenants_router)
router.include_router(rbac_router)
router.include_router(permissions_router)  # Standalone permissions endpoint
router.include_router(dashboard_router)  # Dashboard metrics
router.include_router(api_keys_router)
# Core Services
router.include_router(audit_logs_router)
router.include_router(notifications_router)
router.include_router(webhooks_router)
router.include_router(feature_flags_router)
router.include_router(sso_router)
router.include_router(sso_settings_router)
# Storage & Files (Phase 4)
router.include_router(storage_settings_router)
router.include_router(files_router)
# AI (Phase 5) — historical position: after files, before the billing group
router.include_router(ai_router)
