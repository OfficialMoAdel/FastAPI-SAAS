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
    SubscriptionNotFoundError,
    PaymentFailedError,
    WebhookVerificationError,
)

try:
    await billing_service.cancel_subscription(sub_id)
except SubscriptionNotFoundError:
    raise HTTPException(404, "Subscription not found")
except PaymentFailedError as e:
    raise HTTPException(402, str(e))
```

## Module Structure

```
src/app/modules/billing/
├── __init__.py           # Module exports
├── router.py             # /billing endpoints
├── service.py            # BillingService class
├── lifecycle.py          # SubscriptionLifecycleService
├── webhook.py            # Stripe webhook handlers
├── crud.py               # CRUD operations
├── models.py             # SQLAlchemy models
├── schemas.py            # Pydantic schemas
└── exceptions.py         # Billing-specific exceptions
```

## Touch Points

- **Billing service**: `modules/billing/service.py::BillingService`
- **Webhook handler**: `modules/billing/webhook.py::handle_stripe_webhook`
- **Lifecycle service**: `modules/billing/lifecycle.py::SubscriptionLifecycleService`
- **Product CRUD**: `modules/billing/crud.py::crud_product`
- **Pricing CRUD**: `modules/billing/crud.py::crud_pricing`
- **Subscription CRUD**: `modules/billing/crud.py::crud_user_subscription`

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
