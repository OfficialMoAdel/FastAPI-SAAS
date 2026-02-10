from fastapi import APIRouter

from .monitoring import router as monitoring_router

# Lazy load v1 router to avoid circular dependency
# from ..api.v1 import router as v1_router

router = APIRouter(prefix="/api")
# router.include_router(v1_router)
router.include_router(monitoring_router, tags=["monitoring"])

def get_router() -> APIRouter:
    """Get the main API router with v1 routes included.

    Lazy loading v1 router avoids circular imports with dependencies/security.
    """
    from ..api.v1 import router as v1_router
    # Check if v1_router is already included to prevent duplicates if called multiple times
    # (Checking simple route existence or just re-including is safe if unique identifiers match,
    # but include_router duplicates routes)
    # A better way is to check tags or rely on this being called once at startup.
    # Given typical app lifecycle, it's called once.

    # We can create a new router to be safe, or modify the module-level one.
    # Modifying module-level one is risky if called multiple times (e.g. tests).
    # But include_router copies routes.

    main_router = APIRouter(prefix="/api")
    main_router.include_router(monitoring_router, tags=["monitoring"])
    main_router.include_router(v1_router)
    return main_router
