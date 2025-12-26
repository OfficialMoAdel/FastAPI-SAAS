from fastapi import APIRouter

from ...modules.health.router import router as health_router
from ...modules.auth.router import router as auth_router
from ...modules.posts.router import router as posts_router
from ...modules.rate_limits.router import router as rate_limits_router
from ...modules.tasks.router import router as tasks_router
from ...modules.tiers.router import router as tiers_router
from ...modules.users.router import router as users_router

router = APIRouter(prefix="/v1")
router.include_router(health_router)
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(posts_router)
router.include_router(tasks_router)
router.include_router(tiers_router)
router.include_router(rate_limits_router)
