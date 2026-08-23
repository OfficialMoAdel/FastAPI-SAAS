"""Stripe webhook processing for the billing module.

Decomposes the former single-module ``webhook.py`` into one submodule per
concern:

- :mod:`.client` — Stripe trust boundary: signature verification
- :mod:`.idempotency` — at-least-once delivery handling (Redis + DB locks)
- :mod:`.handlers` — business handlers for each Stripe event type
- :mod:`.dispatch` — event-type registry and the ``handle_webhook_event`` entry point

Shared seam
-----------
Names that tests patch or ``core.setup`` mutates at runtime (``redis_client``,
RLS job contexts, CRUD singletons, handlers) live in this package namespace.
Submodules resolve them through the package object (``_pkg.<name>``), which
preserves the exact late-binding lookup semantics of the original single
module — ``mock.patch("...billing.webhook.<name>")`` keeps working.
"""

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.utils.cache import client as redis_client
from src.app.core.worker.rls_context import job_bypass_context, job_tenant_context
from src.app.modules.billing.crud import crud_billing_records, crud_user_subscriptions

logger = get_logger(__name__)

# Redis key prefix for webhook idempotency tracking
# Stripe events have at-least-once delivery guarantee, so we must track processed events
_WEBHOOK_EVENT_KEY_PREFIX = "stripe:webhook:processed:"
# Short-lived lock held ONLY while a handler is actively running. If the process
# crashes mid-handling, this key auto-expires and Stripe can safely retry,
# instead of the event being permanently marked "processed" and lost.
_WEBHOOK_PROCESSING_TTL_SECONDS = 300  # 5 minutes max handler time

from .client import verify_webhook_signature  # noqa: E402,F401
from .dispatch import EVENT_HANDLERS, handle_webhook_event  # noqa: E402,F401
from .handlers import (  # noqa: E402,F401
    handle_charge_refunded,
    handle_checkout_session_completed,
    handle_invoice_payment_failed,
    handle_invoice_payment_succeeded,
    handle_subscription_deleted,
    handle_subscription_updated,
)
from .idempotency import (  # noqa: E402,F401
    _db_idempotency_check,
    _release_webhook_event_lock,
    _try_acquire_event_lock,
)
