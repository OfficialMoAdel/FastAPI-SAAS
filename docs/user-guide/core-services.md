# Core Services

This guide provides an overview of the core services available in the FastAPI SaaS , including Audit Logging, Notifications (Email), Webhooks, Feature Flags, and the Event Bus.

## Table of Contents

1. [Event Bus](#event-bus)
2. [Audit Logging](#audit-logging)
3. [Notifications](#notifications)
4. [Webhooks](#webhooks)
5. [Feature Flags](#feature-flags)

---

## Event Bus

The Event Bus is a central communication hub that allows different modules to communicate without direct dependencies. It supports both synchronous and asynchronous event dispatching.

### Architecture

The system uses a priority-based approach:
- **CRITICAL**: Handlers are executed synchronously (immediately) within the request/response cycle.
- **NORMAL**: Handlers are enqueued for background execution via ARQ.
- **LOW**: Handlers are enqueued for background execution (typically for non-urgent tasks like cleanup).

### Publishing Events

```python
from src.app.core.events import event_bus, EventPriority

# Publish a critical event
await event_bus.publish(
    "auth.login",
    {"user_id": str(user.id), "ip": request.client.host},
    priority=EventPriority.CRITICAL
)

# Publish a normal event
await event_bus.publish(
    "invoice.paid",
    {"invoice_id": invoice_id, "amount": 1000},
    priority=EventPriority.NORMAL
)
```

### Subscribing to Events

```python
from src.app.core.events import event_bus

@event_bus.subscribe("auth.login")
async def handle_login(data: dict):
    print(f"User {data['user_id']} logged in from {data['ip']}")
```

---

## Audit Logging

The Audit Logging service automatically tracks write operations across the system, providing an immutable trail for security and compliance.

### Using the Audit Decorator

The easiest way to log actions is by using the `@audit_action` decorator on your service methods or API endpoints.

```python
from src.app.modules.audit_logs.service import audit_action

@router.post("/items")
@audit_action(action="item.created", entity_type="item")
async def create_item(tenant_id: int, data: ItemCreate, db: AsyncSession = Depends(get_db)):
    item = Item(**data.dict(), tenant_id=tenant_id)
    db.add(item)
    await db.commit()
    return item
```

### Manual Logging

For more control, use the `AuditLogService` directly.

```python
from src.app.modules.audit_logs.service import AuditLogService

service = AuditLogService(db)
await service.create_log(
    tenant_id=tenant_id,
    action="resource.complex_operation",
    user_id=current_user.id,
    details={"key": "value"}
)
```

### Viewing Logs

Audit logs can be accessed via `GET /api/v1/audit-logs`. Admins can filter by user, action, date range, and entity.

---

## Notifications

The Notification service handles transactional emails using Jinja2 templates and background delivery.

### Sending Emails

```python
from src.app.modules.notifications.service import NotificationService

service = NotificationService(db)
await service.send_email(
    tenant_id=tenant_id,
    template_name="welcome",
    to_email="user@example.com",
    context={"name": "John Doe"}
)
```

### Email Templates

Templates are located in `src/app/templates/email/`. You can create new `.html` and `.txt` files there. Tenants can also override these templates via the API for custom branding.

---

## Webhooks

Webhooks allow tenants to subscribe to real-time events. When an event occurs in the system, a POST request is sent to the tenant's configured endpoint.

### Configuring Webhooks

Tenants can manage their webhook endpoints via `GET/POST /api/v1/webhooks`.

### Security

All webhooks include a signature in the `X-Webhook-Signature` header (HMAC-SHA256). Recipients should verify this signature using their secret to ensure authenticity.

---

## Feature Flags

Feature Flags allow you to toggle features on or off for specific tenants without redeploying code.

### Backend Usage

Check if a feature is enabled for a tenant:

```python
from src.app.modules.feature_flags.utils import is_feature_enabled

if await is_feature_enabled(db, tenant_id, "advanced_reporting"):
    # Execute feature-specific logic
    pass
```

### Frontend Integration

The frontend can retrieve all active flags for the current tenant by calling `GET /api/v1/feature-flags`.

#### Example (React/Vue)

```javascript
// Fetch flags on app initialization
const response = await fetch('/api/v1/feature-flags');
const { flags } = await response.json();

// Check flag in component
if (flags.dark_mode) {
  enableDarkMode();
}
```

### Managing Flags

Flags are stored as JSONB in the `Tenant` record. Admins can update flags globally or per-tenant using the `/api/v1/feature-flags` endpoints.
