# Webhooks Module - Agent Guidance

## Package Identity

Webhook event subscription and delivery system for real-time event notifications. Supports HMAC-SHA256 signatures, retry logic with exponential backoff, delivery status tracking, and multi-tenant isolation.

## Setup & Run

```bash
# Test webhooks module
uv run pytest tests/unit/modules/webhooks/test_webhooks.py -v

# Type check module
uv run mypy src/app/modules/webhooks/

# Lint module
uv run ruff check src/app/modules/webhooks/

# Test webhook delivery
uv run pytest tests/test_webhook_delivery.py -v
```

## Patterns & Conventions

### Module Structure
```
src/app/modules/webhooks/
├── __init__.py           # Module exports
├── router.py             # FastAPI routes for endpoint management
├── service.py            # WebhookService for delivery logic
├── models.py             # WebhookEndpoint, WebhookDelivery models
├── schemas.py            # Pydantic schemas for API
└── AGENTS.md             # This file
```

### Webhook Endpoint Creation
✅ DO: Create endpoints with auto-generated secrets
```python
# src/app/modules/webhooks/router.py (lines 68-90)
@router.post("/endpoints", response_model=WebhookEndpointResponse, status_code=201)
async def create_webhook_endpoint(
    request: WebhookEndpointCreate,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
) -> WebhookEndpointResponse:
    service = WebhookService(db)
    endpoint = await service.create_endpoint(
        tenant_id=current_tenant.id,
        url=request.url,
        event_types=request.event_types,
        secret=request.secret,  # Auto-generated if None
    )
    return WebhookEndpointResponse.model_validate(endpoint)
```

### Trigger Webhooks for Events
✅ DO: Use WebhookService to trigger webhooks
```python
# src/app/modules/webhooks/service.py (lines 74-120)
async def trigger_webhooks(
    self,
    tenant_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> list[WebhookDelivery]:
    """Trigger webhooks for a specific event."""
    endpoints = await self.get_active_endpoints(tenant_id, event_type)
    deliveries = []

    for endpoint in endpoints:
        delivery = await self.create_delivery(endpoint, event_type, payload)
        deliveries.append(delivery)

        # Queue delivery asynchronously
        await self._enqueue_delivery(delivery.id)

    return deliveries
```

### HMAC Signature Generation
✅ DO: Generate signatures for webhook payloads
```python
# src/app/modules/webhooks/service.py (lines 150-170)
def _generate_signature(self, payload: bytes, secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload."""
    return hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
```

### Delivery Status Tracking
✅ DO: Track delivery attempts and status
```python
# src/app/modules/webhooks/models.py (lines 80-130)
class WebhookDelivery(Base, UUIDMixin):
    """Record of webhook delivery attempt."""

    __tablename__ = "webhook_deliveries"

    endpoint_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        Enum(WebhookDeliveryStatus),
        default=WebhookDeliveryStatus.PENDING,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, default=0, nullable=False
    )
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    response_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

### Retry Logic
✅ DO: Implement exponential backoff for failed deliveries
```python
# src/app/modules/webhooks/service.py (lines 29-31)
# Retry schedule: 1m, 5m, 15m, 30m, 1h (5 retries over 1 hour)
WEBHOOK_RETRY_DELAYS = [60, 300, 900, 1800, 3600]

async def _schedule_retry(self, delivery: WebhookDelivery) -> None:
    """Schedule retry for failed delivery."""
    if delivery.attempt_count < len(WEBHOOK_RETRY_DELAYS):
        delay = WEBHOOK_RETRY_DELAYS[delivery.attempt_count]
        delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
        delivery.attempt_count += 1
        await self.db.commit()
```

## Touch Points / Key Files

### Service Layer
- Service: `src/app/modules/webhooks/service.py` - Core webhook logic
- Constants: `WEBHOOK_RETRY_DELAYS` - Retry schedule

### Models
- Endpoint: `src/app/modules/webhooks/models.py` - `WebhookEndpoint`
- Delivery: `src/app/modules/webhooks/models.py` - `WebhookDelivery`
- Status: `src/app/modules/webhooks/models.py` - `WebhookDeliveryStatus` enum

### Router
- Endpoints: `src/app/modules/webhooks/router.py` - CRUD for webhook endpoints
- Deliveries: `src/app/modules/webhooks/router.py` - Delivery history and retry

### Schemas
- Create: `WebhookEndpointCreate` - Endpoint creation request
- Response: `WebhookEndpointResponse` - Endpoint details with secret
- Delivery: `WebhookDeliveryResponse` - Delivery status and response

## JIT Index Hints

```bash
# Find webhook endpoints
rg -n "router.*webhooks|/webhooks" src/app/modules/webhooks/

# Find webhook triggers
rg -n "trigger_webhooks" src/app/

# Find signature generation
rg -n "_generate_signature|HMAC" src/app/modules/webhooks/

# Find delivery status
rg -n "WebhookDeliveryStatus" src/app/modules/webhooks/

# Find retry logic
rg -n "_schedule_retry|WEBHOOK_RETRY" src/app/modules/webhooks/
```

## Common Gotchas

### Signature Verification
✅ DO: Verify signatures on receiving endpoint
```python
# Client-side verification example
import hmac, hashlib

def verify_webhook(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
```

### Secret Storage
✅ DO: Hash or encrypt webhook secrets
❌ DON'T: Store secrets in plain text (placeholder in current code)

### Async Delivery
✅ DO: Queue deliveries asynchronously via ARQ
❌ DON'T: Make synchronous HTTP calls in request handlers (current `_enqueue_delivery` is placeholder)

### Event Types
✅ DO: Use dot-notation for event types (e.g., `user.created`, `order.completed`)
```python
# Common event types
EVENT_USER_CREATED = "user.created"
EVENT_USER_UPDATED = "user.updated"
EVENT_ORDER_COMPLETED = "order.completed"
```

### Delivery Timeouts
✅ DO: Set reasonable timeouts for webhook calls
```python
# Recommended timeout configuration
TIMEOUT = httpx.Timeout(10.0, connect=5.0)  # 10s total, 5s connect
```

## Pre-PR Checks

```bash
# Test webhook creation and listing
uv run pytest tests/unit/modules/webhooks/test_webhooks.py -v

# Test webhook delivery
uv run pytest tests/test_webhook_delivery.py -v

# Type check
uv run mypy src/app/modules/webhooks/

# Lint
uv run ruff check src/app/modules/webhooks/

# Test signature generation
uv run python -c "
from src.app.modules.webhooks.service import WebhookService
service = WebhookService(None)
sig = service._generate_signature(b'test', 'secret')
print(f'Signature: {sig}')
"
```
