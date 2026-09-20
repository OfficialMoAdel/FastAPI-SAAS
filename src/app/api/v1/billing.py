"""v1 billing router group (P2 aggregator).

Includes the billing module router and the API-compatibility sub-routers
(products/prices/checkouts/invoices) in their historical order.
"""

from fastapi import APIRouter

from src.app.modules.billing.router import router as billing_router
from src.app.modules.billing.routers.checkouts import router as checkouts_router
from src.app.modules.billing.routers.invoices import router as invoices_router
from src.app.modules.billing.routers.prices import router as prices_router
from src.app.modules.billing.routers.products import router as products_router

router = APIRouter()
# Billing & AI (Phase 3/5)
router.include_router(billing_router)
router.include_router(products_router)  # API compatibility layer
router.include_router(prices_router)  # API compatibility layer
router.include_router(checkouts_router)  # API compatibility layer
router.include_router(invoices_router)  # API compatibility layer
