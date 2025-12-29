from fastapi import APIRouter

from src.app.modules.auth.router import router as auth_router
from src.app.modules.health.router import router as health_router
from src.app.modules.posts.router import router as posts_router
from src.app.modules.rate_limits.router import router as rate_limits_router
from src.app.modules.rbac.router import router as rbac_router
from src.app.modules.tasks.router import router as tasks_router
from src.app.modules.tenants.router import router as tenants_router
from src.app.modules.tiers.router import router as tiers_router
from src.app.modules.users.router import router as users_router

router = APIRouter(prefix="/v1")
router.include_router(health_router)
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(posts_router)
router.include_router(tasks_router)
router.include_router(tiers_router)
router.include_router(rate_limits_router)
router.include_router(tenants_router)
router.include_router(rbac_router)
