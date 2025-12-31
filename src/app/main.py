from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .admin.initialize import create_admin_interface
from .api import router
from .core.config import settings
from .core.setup import create_application, lifespan_factory
from .middleware.tenant import TenantMiddleware

admin = create_admin_interface()


@asynccontextmanager
async def lifespan_with_admin(app: FastAPI) -> AsyncGenerator[None, None]:
    """Custom lifespan that includes admin initialization."""
    # Get the default lifespan
    default_lifespan = lifespan_factory(settings)

    # Run the default lifespan initialization and our admin initialization
    async with default_lifespan(app):
        # Initialize admin interface if it exists
        if admin:
            # Initialize admin database and setup
            await admin.initialize()

        yield


app = create_application(router=router, settings=settings, lifespan=lifespan_with_admin)

# Add tenant middleware for multi-tenancy support
app.add_middleware(TenantMiddleware)

from .middleware.rate_limit import RateLimitMiddleware
from .middleware.request_id import RequestIDMiddleware

# Request ID must be first (outermost) for tracing
app.add_middleware(RequestIDMiddleware)
app.add_middleware(RateLimitMiddleware)

# Mount admin interface if enabled
if admin:
    app.mount(settings.CRUD_ADMIN_MOUNT_PATH, admin.app)
