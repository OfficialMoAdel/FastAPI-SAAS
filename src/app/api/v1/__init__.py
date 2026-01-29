from fastapi import APIRouter

from ...modules.ai.router import router as ai_router
from ...modules.api_keys.router import router as api_keys_router
from ...modules.app_configuration.router import router as app_configuration_router
from ...modules.audit_logs.router import router as audit_logs_router
from ...modules.auth.router import router as auth_router
from ...modules.billing.router import router as billing_router
from ...modules.billing.routes.checkouts import router as checkouts_router
from ...modules.billing.routes.invoices import router as invoices_router
from ...modules.billing.routes.prices import router as prices_router
from ...modules.billing.routes.products import router as products_router
from ...modules.configurations import router as configurations_router
from ...modules.dashboard.router import router as dashboard_router
from ...modules.feature_flags.router import router as feature_flags_router
from ...modules.files.router import router as files_router
from ...modules.health.router import router as health_router
from ...modules.magic_link.router import router as magic_link_router
from ...modules.notifications.router import router as notifications_router
from ...modules.passkey.router import router as passkey_router
from ...modules.posts.router import router as posts_router
from ...modules.rate_limits.router import router as rate_limits_router
from ...modules.rbac.permissions_router import router as permissions_router
from ...modules.rbac.router import router as rbac_router
from ...modules.security.router import router as security_router
from ...modules.sso.router import router as sso_router
from ...modules.sso_settings.router import router as sso_settings_router
from ...modules.storage_settings.router import router as storage_settings_router
from ...modules.tasks.router import router as tasks_router
from ...modules.tenants.router import router as tenants_router
from ...modules.tiers.router import router as tiers_router
from ...modules.user_preferences.router import router as user_preferences_router
from ...modules.users.router import router as users_router
from ...modules.webhooks.router import router as webhooks_router
from ..v1.admin.circuit_breakers import router as circuit_breakers_router
from ..v1.admin.jobs import router as jobs_router

router = APIRouter(prefix="/v1")
router.include_router(health_router)
router.include_router(app_configuration_router)  # Public configuration endpoint
router.include_router(auth_router)
router.include_router(magic_link_router)
router.include_router(passkey_router)
router.include_router(security_router)  # Passkey convenience wrapper
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
# Billing & AI (Phase 3/5)
router.include_router(billing_router)
router.include_router(products_router)  # API compatibility layer
router.include_router(prices_router)  # API compatibility layer
router.include_router(checkouts_router)  # API compatibility layer
router.include_router(invoices_router)  # API compatibility layer
router.include_router(configurations_router)  # API compatibility layer (Stripe)
router.include_router(ai_router)
# Admin endpoints
router.include_router(circuit_breakers_router, prefix="/admin")
router.include_router(jobs_router, prefix="/admin")
