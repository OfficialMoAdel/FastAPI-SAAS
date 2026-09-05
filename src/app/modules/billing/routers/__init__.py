"""Billing sub-routers package.

Standardized canonical naming per PR-11 / Finding #15.
"""

from src.app.modules.billing.routers.checkouts import router as checkouts_router
from src.app.modules.billing.routers.invoices import router as invoices_router
from src.app.modules.billing.routers.prices import router as prices_router
from src.app.modules.billing.routers.products import router as products_router

__all__ = [
    "checkouts_router",
    "invoices_router",
    "prices_router",
    "products_router",
]
