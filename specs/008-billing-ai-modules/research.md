# Research: Billing & Payments and AI Features Modules

**Branch**: `008-billing-ai-modules` | **Date**: 2026-01-08
**Status**: Complete

## Executive Summary

This document captures technical research for implementing Phase 3 (Billing & Payments) and Phase 5 (AI Features) modules. All technical decisions align with the project constitution (async-first, PostgreSQL-only, row-level multi-tenancy). No blocking issues identified.

---

## Billing & Payments Module

### Stripe Python SDK Integration

**Library**: `stripe` (official Python SDK)
**Async Support**: Full async support via `AsyncClient` pattern with `httpx` backend

#### Key Patterns

**1. Client Initialization (async-compatible)**
```python
from stripe import StripeClient

# Per-tenant Stripe client with async HTTP backend
import stripe
http_client = stripe.HTTPXClient()  # Supports async/sync

client = StripeClient(
    api_key=settings.SECRET_STRIPE_SECRET_KEY,
    http_client=http_client
)
```

**2. Async API Operations**
```python
from stripe import StripeClient

client = StripeClient(api_key)

async def create_customer(email: str, name: str) -> str:
    customer = await client.v1.customers.create_async(
        params={
            "email": email,
            "name": name,
            "metadata": {"tenant_id": str(tenant_id)}
        }
    )
    return customer.id
```

**3. Webhook Signature Verification**
```python
from stripe import Webhook, SignatureVerificationError

def verify_webhook(payload: bytes, sig_header: str, secret: str):
    try:
        event = Webhook.construct_event(payload, sig_header, secret)
        return event
    except SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
```

**4. Subscription Management**
```python
# Create subscription with trial
subscription = await client.v1.subscriptions.create_async(
    params={
        "customer": customer_id,
        "items": [{"price": price_id}],
        "payment_behavior": "default_incomplete",
        "expand": ["latest_invoice.payment_intent"],
        "metadata": {"tenant_id": str(tenant_id)}
    }
)

# Cancel subscription (end-of-period)
await client.v1.subscriptions.update_async(
    subscription_id,
    params={"cancel_at_period_end": True}
)
```

#### Webhook Events to Handle

| Event Type | Action |
|------------|--------|
| `checkout.session.completed` | Create BillingRecord, activate subscription |
| `invoice.payment_succeeded` | Create BillingRecord with success status |
| `invoice.payment_failed` | Create BillingRecord with failure status, update UserSubscription to `past_due` |
| `customer.subscription.updated` | Sync UserSubscription status |
| `customer.subscription.deleted` | Mark UserSubscription as `expired` |

### Database Schema Decisions

**Amount Storage**: Store in cents (integer) with currency code
- Rationale: Avoids floating-point precision issues
- Pattern: `amount_cents: int`, `currency: str` (ISO 4217, lowercase)

**JSONB Usage**:
- `SubscriptionPlan.features`: List of feature strings
- `Pricing.features`: List of feature strings for pricing tier
- `StripeSettings`: Encrypted credentials in JSONB

**Foreign Keys**:
- All tables include `tenant_id` FK for row-level isolation
- `BillingRecord` links to `user_id` and optionally `subscription_id`
- `UserSubscription` links to `user_id` and `subscription_plan_id`

---

## AI Features Module

### Ollama API Integration

**Library**: `ollama` (official Python SDK)
**Async Support**: Full async via `AsyncClient`

#### Key Patterns

**1. Async Client Initialization**
```python
from ollama import AsyncClient

# Service-level client configuration
async_client = AsyncClient(
    host=settings.OLLAMA_HOST,  # Default: http://localhost:11434
    headers={"Authorization": f"Bearer {settings.SECRET_OLLAMA_API_KEY}"}
)
```

**2. Chat Completion (Non-streaming)**
```python
from ollama import AsyncClient

async def generate_response(
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2048
) -> tuple[str, int]:
    response = await AsyncClient().chat(
        model=model,
        messages=messages,
        options={
            "temperature": temperature,
            "num_predict": max_tokens
        }
    )
    content = response.message.content
    # Token count from response metadata
    tokens = response.get("eval_count", 0)
    return content, tokens
```

**3. Streaming Responses (for future enhancement)**
```python
async def stream_response(model: str, messages: list[dict]):
    async for part in await AsyncClient().chat(
        model=model,
        messages=messages,
        stream=True
    ):
        yield part['message']['content']
```

**4. Error Handling**
```python
import ollama

try:
    response = await async_client.chat(model=model, messages=messages)
except ollama.ResponseError as e:
    if e.status_code == 404:
        raise HTTPException(status_code=503, detail=f"Model '{model}' not available")
    raise HTTPException(status_code=503, detail="AI service unavailable")
except Exception:
    raise HTTPException(status_code=503, detail="AI service unavailable")
```

#### Token Management Strategy

**Context Window Truncation**:
- Default context window: 4096 tokens (configurable per model)
- Reserved for response: 25% of window
- Strategy: FIFO removal of oldest messages, preserve system prompt

```python
def truncate_context(
    messages: list[dict],
    max_tokens: int = 4096,
    reserve_for_response: float = 0.25
) -> list[dict]:
    available_tokens = int(max_tokens * (1 - reserve_for_response))

    # Always keep system prompt (first message if role=system)
    system_msgs = [m for m in messages if m["role"] == "system"]
    user_assistant_msgs = [m for m in messages if m["role"] != "system"]

    # Estimate tokens (rough: 4 chars per token)
    def estimate_tokens(msg: dict) -> int:
        return len(msg.get("content", "")) // 4

    system_tokens = sum(estimate_tokens(m) for m in system_msgs)
    remaining = available_tokens - system_tokens

    # Keep most recent messages that fit
    result = []
    for msg in reversed(user_assistant_msgs):
        msg_tokens = estimate_tokens(msg)
        if remaining >= msg_tokens:
            result.insert(0, msg)
            remaining -= msg_tokens
        else:
            break

    return system_msgs + result
```

### Database Schema Decisions

**Message Storage**:
- Full message history stored in `ai_chat_messages` table
- Token count per message for analytics/billing potential
- Ordering via `created_at` timestamp (TIMESTAMPTZ)

**AI Settings (User-level)**:
- `model`: Selected model identifier (e.g., "gemma3", "llama3")
- `temperature`: Float 0.0-2.0 (default 0.7)
- `max_tokens`: Integer (default 2048)
- `system_prompt`: Custom system prompt text (nullable)

**JSONB Usage**:
- `AISettings`: Store as single JSONB column per user for flexibility

---

## Integration Patterns

### FastAPI Dependency Injection

**Database Session Pattern**:
```python
from typing import Annotated, AsyncGenerator
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()

SessionDep = Annotated[AsyncSession, Depends(get_db)]
```

**Tenant Context Pattern**:
```python
from typing import Annotated
from fastapi import Depends, Header, HTTPException

async def get_tenant_id(x_tenant_id: str = Header(...)) -> int:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    return int(x_tenant_id)

TenantDep = Annotated[int, Depends(get_tenant_id)]
```

**Service Injection Pattern**:
```python
from typing import Annotated
from fastapi import Depends

def get_billing_service(
    db: SessionDep,
    tenant_id: TenantDep
) -> BillingService:
    return BillingService(db=db, tenant_id=tenant_id)

BillingServiceDep = Annotated[BillingService, Depends(get_billing_service)]
```

### Background Tasks

**Webhook Processing**:
```python
from fastapi import BackgroundTasks

@router.post("/webhooks/stripe")
async def handle_stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    billing_service: BillingServiceDep
):
    # Verify signature synchronously
    event = await billing_service.verify_webhook(request)

    # Process in background to return 200 quickly
    background_tasks.add_task(
        billing_service.process_webhook_event,
        event
    )

    return {"received": True}
```

---

## Security Considerations

### Stripe Credentials

- Store in environment variables: `SECRET_STRIPE_SECRET_KEY`, `SECRET_STRIPE_WEBHOOK_SECRET`
- Per-tenant Stripe Connect possible (future): store account IDs in `StripeSettings`
- Never log or expose API keys in responses

### AI Content Safety

- System prompts can include safety guidelines
- Consider rate limiting AI requests per user/tenant
- Log AI interactions for audit (without PII in logs)

---

## Technical Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Stripe SDK | `stripe` official SDK with `HTTPXClient` | Async support, typed, well-maintained |
| Ollama SDK | `ollama` official SDK | Native async, simple API |
| Amount storage | Cents (integer) | Avoid floating-point issues |
| Token estimation | Character-based (4 chars/token) | Simple, sufficient for truncation |
| Webhook processing | Background tasks | Fast response to Stripe |
| AI error handling | Fail immediately | Per spec clarification |

---

## Dependencies to Add

```toml
# pyproject.toml additions
[project.dependencies]
stripe = ">=8.0.0"
ollama = ">=0.3.0"
```

---

## Open Questions (Resolved)

| Question | Resolution |
|----------|------------|
| User subscription tracking | UserSubscription entity with lifecycle states |
| AI unavailability handling | Fail immediately with error, user retries |
| Token limit handling | Auto-truncate oldest messages, preserve system prompt |

All questions resolved during specification clarification phase.
