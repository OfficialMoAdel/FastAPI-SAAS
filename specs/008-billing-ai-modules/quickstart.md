# Quickstart: Billing & Payments and AI Features

**Branch**: `008-billing-ai-modules` | **Date**: 2026-01-08

---

## Prerequisites

- Python 3.11+
- PostgreSQL 15+ running
- Redis running (for rate limiting)
- Stripe account with API keys
- Ollama running locally or accessible endpoint
- Environment variables configured (see below)

---

## Environment Setup

Add to your `.env.local`:

```bash
# Stripe API keys
SECRET_STRIPE_SECRET_KEY=sk_test_...
SECRET_STRIPE_PUBLISHABLE_KEY=pk_test_...
SECRET_STRIPE_WEBHOOK_SECRET=whsec_...

# Ollama configuration
OLLAMA_HOST=http://localhost:11434
# Optional: API key if using authenticated endpoint
SECRET_OLLAMA_API_KEY=your-api-key
```

### Stripe CLI (for webhook testing)

```bash
# Install Stripe CLI
# macOS: brew install stripe/stripe-cli/stripe
# Windows: scoop install stripe

# Login and forward webhooks
stripe login
stripe listen --forward-to localhost:8000/api/v1/billing/webhooks/stripe
```

### Ollama Setup

```bash
# Install Ollama (macOS/Linux)
curl -fsSL https://ollama.com/install.sh | sh

# Pull default model
ollama pull gemma3

# Verify running
ollama list
```

---

## Quick Test

### Billing Module

#### 1. Create Subscription Plan

```bash
curl -X POST "http://localhost:8000/api/v1/billing/plans" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Pro Plan",
    "description": "Professional features for growing teams",
    "price_monthly_cents": 2999,
    "price_yearly_cents": 29990,
    "currency": "usd",
    "features": ["Unlimited projects", "Priority support", "API access"],
    "is_popular": true
  }'
```

Response:
```json
{
  "id": 1,
  "tenant_id": 1,
  "name": "Pro Plan",
  "description": "Professional features for growing teams",
  "price_monthly_cents": 2999,
  "price_yearly_cents": 29990,
  "currency": "usd",
  "features": ["Unlimited projects", "Priority support", "API access"],
  "sort_order": 0,
  "is_popular": true,
  "is_active": true,
  "created_at": "2026-01-08T10:00:00Z"
}
```

#### 2. List Available Plans

```bash
curl -X GET "http://localhost:8000/api/v1/billing/plans" \
  -H "X-Tenant-ID: 1"
```

#### 3. Create Checkout Session

```bash
curl -X POST "http://localhost:8000/api/v1/billing/checkout" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1" \
  -H "Content-Type: application/json" \
  -d '{
    "plan_id": 1,
    "billing_interval": "monthly",
    "success_url": "https://yourapp.com/billing/success",
    "cancel_url": "https://yourapp.com/billing/cancel"
  }'
```

Response:
```json
{
  "checkout_url": "https://checkout.stripe.com/c/pay/...",
  "session_id": "cs_test_..."
}
```

#### 4. Check Current Subscription

```bash
curl -X GET "http://localhost:8000/api/v1/billing/subscriptions/me" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1"
```

#### 5. View Billing History

```bash
curl -X GET "http://localhost:8000/api/v1/billing/history?page=1&limit=10" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1"
```

---

### AI Features Module

#### 1. Create Chat Session

```bash
curl -X POST "http://localhost:8000/api/v1/ai/sessions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Code Review Discussion",
    "model": "gemma3"
  }'
```

Response:
```json
{
  "id": 1,
  "user_id": 1,
  "title": "Code Review Discussion",
  "model": "gemma3",
  "created_at": "2026-01-08T10:00:00Z",
  "updated_at": null
}
```

#### 2. Send Message and Get AI Response

```bash
curl -X POST "http://localhost:8000/api/v1/ai/sessions/1/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "How do I optimize a Python function for performance?"
  }'
```

Response:
```json
{
  "user_message": {
    "id": 1,
    "session_id": 1,
    "role": "user",
    "content": "How do I optimize a Python function for performance?",
    "token_count": 12,
    "created_at": "2026-01-08T10:01:00Z"
  },
  "assistant_message": {
    "id": 2,
    "session_id": 1,
    "role": "assistant",
    "content": "Here are several strategies to optimize Python functions...",
    "token_count": 245,
    "created_at": "2026-01-08T10:01:03Z"
  }
}
```

#### 3. List Available AI Models

```bash
curl -X GET "http://localhost:8000/api/v1/ai/models" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1"
```

Response:
```json
[
  {
    "id": "gemma3",
    "name": "Gemma 3",
    "description": "Google's efficient open model",
    "context_window": 8192
  },
  {
    "id": "llama3",
    "name": "Llama 3",
    "description": "Meta's powerful open model",
    "context_window": 8192
  }
]
```

#### 4. Update AI Settings

```bash
curl -X PUT "http://localhost:8000/api/v1/ai/settings" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3",
    "temperature": 0.8,
    "max_tokens": 4096,
    "system_prompt": "You are a helpful coding assistant."
  }'
```

#### 5. Get Session with Message History

```bash
curl -X GET "http://localhost:8000/api/v1/ai/sessions/1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1"
```

---

## Running Tests

```bash
# All tests
uv run pytest

# Just billing module tests
uv run pytest tests/unit/billing/ tests/integration/billing/ -v

# Just AI module tests
uv run pytest tests/unit/ai/ tests/integration/ai/ -v

# With coverage
uv run pytest --cov=src --cov-fail-under=80
```

---

## API Documentation

After running the server, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## Common Issues

### Billing Module

#### "Stripe API key not configured"
- Ensure `SECRET_STRIPE_SECRET_KEY` is set in environment
- Verify key starts with `sk_test_` (test) or `sk_live_` (production)

#### "Webhook signature verification failed"
- Ensure `SECRET_STRIPE_WEBHOOK_SECRET` matches webhook endpoint secret
- If using Stripe CLI: copy webhook secret from CLI output

#### "No active subscription"
- User hasn't completed checkout flow
- Check `user_subscriptions` table for subscription status

#### "Stripe customer not found"
- Customer ID in database doesn't match Stripe
- Re-sync customer data or create new checkout session

### AI Module

#### "AI service unavailable" (503)
- Ollama not running or unreachable
- Check `OLLAMA_HOST` configuration
- Verify model is pulled: `ollama list`

#### "Model not available"
- Requested model not installed in Ollama
- Pull model: `ollama pull <model_name>`

#### "Context too long"
- Message history exceeds model context window
- System auto-truncates oldest messages
- Consider starting new session for fresh context

#### "Rate limit exceeded"
- Tenant has exceeded AI request quota
- Wait for quota reset or upgrade subscription

---

## Webhook Events

The billing module handles these Stripe webhook events:

| Event | Action |
|-------|--------|
| `checkout.session.completed` | Create BillingRecord, activate subscription |
| `invoice.payment_succeeded` | Create BillingRecord with success status |
| `invoice.payment_failed` | Create BillingRecord with failure, mark subscription past_due |
| `customer.subscription.updated` | Sync subscription status changes |
| `customer.subscription.deleted` | Mark subscription as expired |

Test webhooks locally:
```bash
stripe trigger checkout.session.completed
stripe trigger invoice.payment_succeeded
stripe trigger customer.subscription.deleted
```

---

## Database Migrations

After implementation, run migrations:

```bash
# Generate migration
alembic revision --autogenerate -m "Add billing and AI tables"

# Apply migration
alembic upgrade head

# Verify tables created
psql -d your_database -c "\dt *billing*"
psql -d your_database -c "\dt *ai*"
```

Expected tables:
- `stripe_settings`
- `subscription_plans`
- `user_subscriptions`
- `products`
- `pricing`
- `billing_records`
- `ai_chat_sessions`
- `ai_chat_messages`
- `ai_settings`
