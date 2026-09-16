# Billing Module - Agent Guidance

## Package Identity

Stripe-integrated billing service handling subscriptions, checkout sessions, payment webhooks, and billing history. Manages subscription lifecycle including trials, cancellations, and plan changes.

## Setup & Run

```bash
# Run billing unit tests
uv run pytest tests/unit/billing/ -v

# Run billing integration tests
uv run pytest tests/integration/billing/ -v

# Test checkout session creation
curl -X POST http://localhost:8000/api/v1/billing/checkout \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"price_id": "price_xxx", "success_url": "http://localhost:3000/success", "cancel_url": "http://localhost:3000/cancel"}'
```

## Patterns & Conventions

### DO: Use BillingService for billing operations

```python
from src.app.modules.billing.service import BillingService

billing_service = BillingService(db)
checkout = await billing_service.create_checkout_session(
    user_id=user_id,
    price_id="price_xxx",
    success_url="http://localhost:3000/success",
    cancel_url="http://localhost:3000/cancel",
)
```

### DON'T: Call Stripe directly from routers

```python
# BAD - Stripe calls in router
@router.post("/checkout")
async def checkout(...):
    stripe.checkout.Session.create(...)  # Don't do this
```

### DO: Handle webhooks with proper signature verification

```python
from src.app.modules.billing.webhook import handle_stripe_webhook

@router.post("/webhook")
async def webhook(request: Request, db: AsyncSession = Depends(async_get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    return await handle_stripe_webhook(payload, sig_header, db)
```

### DO: Use subscription lifecycle service

```python
from src.app.modules.billing.lifecycle import SubscriptionLifecycleService

lifecycle = SubscriptionLifecycleService(db)
await lifecycle.cancel_subscription(subscription_id, cancel_at_period_end=True)
```

### DO: Handle billing exceptions properly

```python
from src.app.modules.billing.exceptions import (
    PaymentRequiredError,
    WebhookVerificationError,
)
from src.app.shared.exceptions import ModelNotFoundError

# Domain errors propagate — the central EXCEPTION_HANDLERS table in
# core/errors.py maps them (402 / 400 / 404). Never convert to HTTPException.
await billing_service.cancel_subscription(sub_id)
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
├── routes/                   # Backward-compatibility router re-export shims
├── service.py                # BillingService facade (customer portal, catalog sync)
├── subscription_service.py   # UserSubscriptionService, SubscriptionPlanService, BillingRecordService
├── checkout_service.py       # CheckoutService (checkout session creation & URL validation)
├── lifecycle.py              # SubscriptionLifecycleService (state machine & cancellation)
├── webhook/                  # Stripe webhook ingest and processing subpackage
│   ├── client.py             # Wire-level signature validation
│   ├── idempotency.py        # Distributed Redis lock & terminal DB processed marker
│   ├── ingest.py             # Webhook ingest entrypoint (H1 verified-then-tenant ordering)
│   ├── dispatch.py           # Event router and registry
│   └── handlers/             # Modular event processors (checkout, subscriptions, invoices, refunds)
├── crud.py                   # TenantAwareCRUD repositories
├── models.py                 # SQLAlchemy models (UserSubscription, Product, Pricing, BillingRecord)
├── schemas.py                # Pydantic DTOs
└── exceptions.py             # Billing domain exceptions (PaymentRequiredError, WebhookVerificationError)
```

### Service File Charters

1. **`service.py` (The Customer Facade):**
   * `BillingService` facade for customer portal sessions, customer management, and product catalog synchronization.

2. **`subscription_service.py` (Internal Persistence Services):**
   * Owns `UserSubscriptionService`, `SubscriptionPlanService`, and `BillingRecordService` for database-backed subscription queries, plan listings, and payment history.

3. **`checkout_service.py` (Checkout Orchestration):**
   * Owns `CheckoutService` responsible for checkout session creation, success/cancel URL validation, and tenant pricing resolution.

4. **`lifecycle.py` (State Machine Engine):**
   * Owns `SubscriptionLifecycleService`, subscription status mapping (`map_stripe_status`), and valid transition rules (`is_valid_transition`) preventing out-of-order resurrection of churned accounts.

## Touch Points

- **Billing service**: `modules/billing/service.py::BillingService`
- **Billing gateway**: `clients/billing/stripe.py::PaymentGateway` / `StripeGateway`
- **Webhook ingest**: `modules/billing/webhook/ingest.py::ingest_webhook`
- **Subscription service**: `modules/billing/subscription_service.py::UserSubscriptionService`
- **Lifecycle service**: `modules/billing/lifecycle.py::SubscriptionLifecycleService`
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
uv run pytest tests/unit/billing/ -v && \
uv run ruff check src/app/modules/billing/ && \
uv run mypy src/app/modules/billing/
```
