# Billing Module - Agent Guidance

## Package Identity

Stripe-integrated billing service handling subscriptions, checkout sessions, payment webhooks, and billing history. Manages subscription lifecycle including trials, cancellations, and plan changes.

## Setup & Run

```bash
# Run billing unit tests
uv run pytest tests/unit/modules/billing/ -v

# Run billing integration tests
uv run pytest tests/integration/modules/billing/ -v

# Test checkout session creation
curl -X POST http://localhost:8000/api/v1/billing/checkout \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"plan_id": 1, "billing_interval": "monthly", "success_url": "http://localhost:3000/success", "cancel_url": "http://localhost:3000/cancel"}'
```

## Patterns & Conventions

### DO: Create checkout sessions through `CheckoutService`

There is no `BillingService` — checkout lives in `CheckoutService`
(`checkout_service.py`), and `service.py` holds `StripeSettingsService`,
`ProductService` and `PricingService`.

```python
from src.app.modules.billing.checkout_service import get_checkout_service
from src.app.modules.billing.schemas import CheckoutCreate

service = get_checkout_service(db)
session = await service.create_session(
    user_id=ctx.user_id,
    user_email=ctx.user.email,
    data=CheckoutCreate(
        plan_id=plan_id,
        billing_interval="monthly",
        success_url="http://localhost:3000/success",
        cancel_url="http://localhost:3000/cancel",
    ),
)
```

`CheckoutCreate` takes a `plan_id` plus a `billing_interval` of
`"monthly"`/`"yearly"` — there is no free-form `price_id`.

### DON'T: Call Stripe directly from routers

```python
# BAD - Stripe calls in router
@router.post("/checkout")
async def checkout(...):
    stripe.checkout.Session.create(...)  # Don't do this
```

### DO: Handle webhooks with proper signature verification

```python
from src.app.modules.billing.webhook import (
    ingest_webhook, verify_webhook_signature,
)

@router.post("/webhook")
async def webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    # verify_fn runs in an executor and raises WebhookVerificationError on a bad signature
    return await ingest_webhook(payload, sig_header, verify_fn=verify_webhook_signature)
```

`ingest_webhook` verifies against the **global** platform secret first, then
derives the tenant from the verified payload — never from a query param.

### The webhook idempotency contract

`_try_acquire_event_lock` returns `(EventLockResult, lock_token)` — a
**three**-valued result, and the three values need different HTTP answers:

| Result | Meaning | Route answers |
|---|---|---|
| `ACQUIRED` | this delivery owns the event | handler runs → 200 |
| `IN_FLIGHT` | another delivery holds a **live** claim | 409, Stripe retries |
| `ALREADY_DONE` | the event already settled | 200, no retry |

Never collapse these into a bool. An earlier version returned one, which made
both failure modes identical and produced two opposite bugs: a 200 for an
in-flight duplicate *lost* the event if the holder then failed, and a 409 for
a settled event made Stripe retry a finished payment for up to 72 hours.

`webhook_events` carries claim state, not just an id:

| Column | Meaning |
|---|---|
| `completed_at` | non-NULL = the handler ran to success |
| `claimed_by` | lock token of the delivery that owns the claim |
| `claimed_at` | when ownership was taken |

The DB fallback (used when Redis is down) is the **only** mutual exclusion
available then, so its takeover is a single conditional `UPDATE` whose
`rowcount` is the election. Do not reintroduce a `SELECT` that asks "is this
finished?" — two deliveries can both read the same unstamped row, and both
will conclude it is theirs. A claim younger than
`_WEBHOOK_ABANDONED_CLAIM_TTL_SECONDS` belongs to a live handler and must
answer `IN_FLIGHT`.

Releases are ownership-scoped on both legs (`compare_and_delete` in Redis,
`claimed_by = lock_token` in the DB) so a slow, failing delivery cannot erase
a successful one's terminal marker.

Schema note: `webhook_events` owns the claim columns directly in migration
layer `04_billing` — `completed_at`, `claimed_by`, `claimed_at` and the
`ix_webhook_events_abandoned` partial index are all declared in its
`create_table`. While no production database has run these layers, that is the
correct place per `src/migrations/AGENTS.md`; the "unless the layer is already
applied in production" clause is what would force a stacked revision later.

### DO: Cancel subscriptions through `UserSubscriptionService`

`lifecycle.py` has **no** `SubscriptionLifecycleService`; it is a module of
state-machine functions (`map_stripe_status`, `is_valid_transition`,
`can_cancel`, …). The mutating API is on `UserSubscriptionService`, and the
ownership check lives there so every caller gets it:

```python
from src.app.modules.billing.subscription_service import (
    get_user_subscription_service,
)

service = get_user_subscription_service(db)
# Raises Forbidden if the caller does not own the subscription
result = await service.cancel_subscription_for_user(
    subscription_id, user_id=ctx.user_id, is_superuser=ctx.is_superuser
)
```

### DO: Handle billing exceptions properly

```python
from src.app.modules.billing.exceptions import (
    PaymentRequiredError,
    WebhookVerificationError,
)
from src.app.shared.exceptions import ModelNotFoundError

# Domain errors propagate — there is NO central mapping table: every
# AppException subclass declares its own http_status/response_code, and
# core/errors.py registers ONE generic handler for the whole tree.
result = await service.cancel_subscription_for_user(sub_id, user_id=user_id)
```

## Module Structure & Service Charters

```
src/app/modules/billing/
├── __init__.py               # Module exports
├── router.py                 # Primary /billing endpoint controller
├── routers/                  # Modular sub-routers package
│   ├── checkouts.py          # /checkouts endpoints
│   ├── invoices.py           # /invoices endpoints
│   ├── prices.py             # /prices endpoints
│   └── products.py           # /products endpoints
├── service.py                # StripeSettingsService, ProductService, PricingService
├── subscription_service.py   # UserSubscriptionService, SubscriptionPlanService, BillingRecordService
├── tenant_scope.py           # TenantScopedService: the ONE tenant-resolution seam (shared base)
├── checkout_service.py       # CheckoutService (checkout session + portal URL validation)
├── lifecycle.py              # State-machine functions (map_stripe_status, is_valid_transition, can_cancel)
├── webhook/                  # Stripe webhook ingest and processing subpackage
│   ├── client.py             # Wire-level signature validation
│   ├── idempotency.py        # Redis SET NX lock, atomic DB claim takeover, terminal markers
│   ├── ingest.py             # Webhook ingest entrypoint (H1 verified-then-tenant ordering)
│   ├── dispatch.py           # Event router and registry
│   ├── locks.py              # Subscription lookup + per-subscription row lock
│   └── handlers/             # Modular event processors (checkout, subscriptions, invoices, refunds)
├── crud.py                   # TenantAwareCRUD repositories
├── models.py                 # SQLAlchemy models (UserSubscription, Product, Pricing, BillingRecord)
├── schemas.py                # Pydantic DTOs
└── exceptions.py             # Billing domain exceptions (PaymentRequiredError, WebhookVerificationError)
```

### Service File Charters

1. **`service.py` (Settings & Catalog):**
   * Owns `StripeSettingsService`, `ProductService` and `PricingService` — Stripe settings, customer/product management and pricing tiers. There is no `BillingService` facade.

2. **`subscription_service.py` (Internal Persistence Services):**
   * Owns `UserSubscriptionService`, `SubscriptionPlanService`, and `BillingRecordService` for database-backed subscription queries, plan listings, and payment history.

3. **`checkout_service.py` (Checkout Orchestration):**
   * Owns `CheckoutService` responsible for checkout session creation, customer-portal sessions with open-redirect-checked return URLs, and tenant pricing resolution.

4. **`lifecycle.py` (State Machine Engine):**
   * Module-level functions only — `map_stripe_status`, `is_valid_transition`, `validate_transition`, `can_cancel`, `is_access_granted` — enforcing valid transitions to prevent out-of-order resurrection of churned accounts. Cancellation itself is `UserSubscriptionService.cancel_subscription_for_user`.

5. **`tenant_scope.py` (Shared Tenant Scoping):**
   * `TenantScopedService` holds the ONE tenant-resolution seam for the module's services: `current_tenant_id` (the read-only view) and `_resolve_tenant` (the fail-closed resolution used before a write). `StripeSettingsService`, `ProductService`, `PricingService`, `SubscriptionPlanService` and `UserSubscriptionService` inherit it and set only their own `_default_entity_name`. Do NOT re-add the two accessors to a service — extend this base instead.

## Touch Points

- **Settings / catalog services**: `modules/billing/service.py::StripeSettingsService` / `ProductService` / `PricingService`
- **Billing gateway**: `clients/billing/stripe.py::PaymentGateway` / `StripeGateway`
- **Webhook ingest**: `modules/billing/webhook/ingest.py::ingest_webhook`
- **Checkout + portal**: `modules/billing/checkout_service.py::CheckoutService`
- **Subscription service**: `modules/billing/subscription_service.py::UserSubscriptionService`
- **Lifecycle state machine**: `modules/billing/lifecycle.py::map_stripe_status`
- **Tenant scoping seam**: `modules/billing/tenant_scope.py::TenantScopedService`
- **Product CRUD**: `modules/billing/crud.py::crud_products`
- **Pricing CRUD**: `modules/billing/crud.py::crud_pricing`
- **Subscription CRUD**: `modules/billing/crud.py::crud_user_subscriptions`
## JIT Index Hints

```bash
# Find billing endpoints
rg -n "router\.(post|get|patch|delete)" src/app/modules/billing/router.py

# Find Stripe integration points
rg -n "stripe\." src/app/modules/billing/

# Find webhook event handlers
rg -n "handle.*event|checkout\.session|invoice\." src/app/modules/billing/

# Find subscription lifecycle methods
rg -n "cancel|activate|upgrade|downgrade" src/app/modules/billing/
```

## Common Gotchas

- **Webhook signature**: Always verify webhook signatures before processing
- **Idempotency**: Webhook events may be delivered multiple times
- **Test mode**: Use Stripe test keys in development
- **Subscription states**: Handle pending, active, past_due, canceled states
- **Billing records**: Always create billing records for completed invoices
- **Trial periods**: Handle trial_end events for subscription activation

## Environment Variables

```bash
STRIPE_SECRET_KEY=sk_test_xxx
STRIPE_PUBLISHABLE_KEY=pk_test_xxx
STRIPE_WEBHOOK_SECRET=whsec_xxx
```

## Pre-PR Checks

```bash
uv run pytest tests/unit/modules/billing/ -v && \
uv run ruff check src/app/modules/billing/ && \
uv run mypy src/app/modules/billing/
```
