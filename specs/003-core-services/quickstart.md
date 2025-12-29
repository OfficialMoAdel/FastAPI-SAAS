# Phase 3 Core Services - Developer Quickstart Guide

**Version:** 1.0  
**Last Updated:** 2024  
**Target Audience:** Backend developers integrating with core services

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [Event Bus](#event-bus)
3. [Audit Logging](#audit-logging)
4. [Notifications](#notifications)
5. [Webhooks](#webhooks)
6. [Feature Flags](#feature-flags)
7. [Testing](#testing)
8. [Troubleshooting](#troubleshooting)

---

## 🎯 Overview

This guide shows you how to use the 4 core services in your FastAPI application:

| Service | Purpose | Use When |
|---------|---------|----------|
| **Event Bus** | Pub/Sub for module decoupling | Triggering actions across modules |
| **Audit Logging** | Track all write operations | Any CREATE/UPDATE/DELETE operation |
| **Notifications** | Send transactional emails | User actions requiring email |
| **Webhooks** | Event subscriptions for tenants | External integrations needed |
| **Feature Flags** | Tenant-level feature toggles | Gradual rollouts, tier-based access |

---

## 🚌 Event Bus

### Architecture

The event bus uses a **hybrid approach**:
- **CRITICAL events** (audit logs) → In-memory, fire-and-forget, sync handlers
- **NORMAL/LOW events** (emails, webhooks) → ARQ background tasks with retry

### Publishing Events

```python
from src.app.core.events import event_bus

# Example 1: Publish a critical event (audit log)
await event_bus.publish(
    event="user.created",
    data={
        "user_id": str(user.id),
        "email": user.email,
        "tenant_id": str(tenant.id),
        "created_by": str(current_user.id)
    },
    priority="CRITICAL"  # Executes sync handlers immediately
)

# Example 2: Publish a normal event (email notification)
await event_bus.publish(
    event="post.published",
    data={
        "post_id": str(post.id),
        "author_id": str(post.author_id),
        "tenant_id": str(post.tenant_id),
        "title": post.title
    },
    priority="NORMAL"  # Enqueued to ARQ for background processing
)

# Example 3: Publish a low-priority event (webhook)
await event_bus.publish(
    event="user.updated",
    data={
        "user_id": str(user.id),
        "changes": {"email": "new@example.com"},
        "tenant_id": str(tenant.id)
    },
    priority="LOW"  # Enqueued with lower priority
)
```

### Subscribing to Events

```python
from src.app.core.events import event_bus

# Example 1: Sync handler for critical events
@event_bus.subscribe("user.created", priority="CRITICAL")
async def log_user_creation(data: dict):
    """This runs immediately when event is published."""
    await audit_log_service.create_log(
        action="user.created",
        tenant_id=data["tenant_id"],
        user_id=data["created_by"],
        entity_type="user",
        entity_id=data["user_id"],
        details={"email": data["email"]}
    )

# Example 2: Async handler for normal events
@event_bus.subscribe("user.created", priority="NORMAL")
async def send_welcome_email(data: dict):
    """This runs in background via ARQ."""
    await notification_service.send_email(
        tenant_id=UUID(data["tenant_id"]),
        template_name="welcome",
        to_email=data["email"],
        context={"user_id": data["user_id"]}
    )

# Example 3: Multiple subscribers to same event
@event_bus.subscribe("post.published", priority="LOW")
async def trigger_webhooks(data: dict):
    """Trigger tenant webhooks for post publication."""
    await webhook_service.trigger_event(
        tenant_id=UUID(data["tenant_id"]),
        event_type="post.published",
        payload=data
    )
```

### Event Naming Conventions

Follow this pattern: `{entity}.{action}`

**Standard Events:**
```python
# User events
"user.created"
"user.updated"
"user.deleted"
"user.password_changed"

# Post events
"post.created"
"post.updated"
"post.deleted"
"post.published"

# Tenant events
"tenant.created"
"tenant.settings_updated"

# Payment events (future)
"payment.succeeded"
"payment.failed"
"subscription.created"
```

### Registering Event Handlers

**In module startup** (e.g., `src/app/modules/users/__init__.py`):

```python
from src.app.core.events import event_bus
from .handlers import send_welcome_email, log_user_creation

def register_user_events():
    """Register all user module event handlers."""
    event_bus.subscribe("user.created", priority="CRITICAL")(log_user_creation)
    event_bus.subscribe("user.created", priority="NORMAL")(send_welcome_email)
    event_bus.subscribe("user.password_changed", priority="NORMAL")(send_password_changed_email)

# Call in main.py startup
register_user_events()
```

---

## 📝 Audit Logging

### Automatic Logging (Recommended)

Use the `@audit_action` decorator to automatically log actions:

```python
from src.app.modules.audit_logs.service import audit_action
from src.app.api.dependencies import get_current_user, get_current_tenant

@router.post("/posts", response_model=PostResponse)
@audit_action("post.created", entity_type="post")
async def create_post(
    data: PostCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant)
):
    """
    Decorator automatically logs:
    - Action: post.created
    - Entity Type: post
    - Entity ID: <returned post.id>
    - Tenant ID: from tenant dependency
    - User ID: from current_user dependency
    - Details: Request data
    """
    post = await post_service.create(db, tenant.id, current_user.id, data)
    return post
```

### Manual Logging

For complex scenarios, use the service directly:

```python
from src.app.modules.audit_logs.service import AuditLogService
from src.app.api.dependencies import get_request_context

audit_log_service = AuditLogService(db)

@router.patch("/posts/{post_id}")
async def update_post(
    post_id: UUID,
    data: PostUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
    request: Request = Depends(get_request_context)
):
    # Get original post
    original_post = await db.get(Post, post_id)
    
    # Update post
    updated_post = await post_service.update(db, post_id, data)
    
    # Log with before/after details
    await audit_log_service.create_log(
        action="post.updated",
        tenant_id=tenant.id,
        user_id=current_user.id,
        entity_type="post",
        entity_id=post_id,
        details={
            "before": {
                "title": original_post.title,
                "content": original_post.content[:100]  # Truncate
            },
            "after": {
                "title": updated_post.title,
                "content": updated_post.content[:100]
            },
            "changed_fields": list(data.dict(exclude_unset=True).keys())
        },
        ip_address=request.client.host,
        user_agent=request.headers.get("User-Agent")
    )
    
    return updated_post
```

### Querying Audit Logs

```python
from src.app.modules.audit_logs.service import AuditLogService

audit_log_service = AuditLogService(db)

# Example 1: Get all logs for a tenant
logs = await audit_log_service.get_logs(
    tenant_id=tenant.id,
    limit=50,
    offset=0
)

# Example 2: Filter by user
user_logs = await audit_log_service.get_logs(
    tenant_id=tenant.id,
    user_id=user.id,
    limit=100
)

# Example 3: Filter by action
creation_logs = await audit_log_service.get_logs(
    tenant_id=tenant.id,
    action="post.created",
    start_date=datetime.utcnow() - timedelta(days=7)
)

# Example 4: Filter by entity
entity_history = await audit_log_service.get_logs(
    tenant_id=tenant.id,
    entity_type="post",
    entity_id=post_id
)
```

### Audit Log Best Practices

**✅ DO:**
- Log all CREATE, UPDATE, DELETE operations
- Include meaningful details (changed fields, values)
- Capture IP address and user agent
- Use structured data in `details` JSONB field
- Truncate large content fields (e.g., post content)

**❌ DON'T:**
- Log passwords or sensitive credentials
- Log GET/LIST operations (too noisy)
- Include PII in unencrypted fields
- Store huge payloads (>10KB) in details

**Example: Good Audit Details**

```python
# ✅ Good: Structured, meaningful, truncated
details = {
    "action_type": "bulk_delete",
    "deleted_count": 15,
    "filters": {"status": "draft", "created_before": "2024-01-01"},
    "affected_ids": post_ids[:10],  # First 10 only
    "total_affected": len(post_ids)
}

# ❌ Bad: Dumps entire objects
details = {
    "posts": [post.dict() for post in deleted_posts]  # Too large!
}
```

---

## 📧 Notifications

### Sending Email Notifications

```python
from src.app.modules.notifications.service import NotificationService

notification_service = NotificationService(db)

# Example 1: Send welcome email
await notification_service.send_email(
    tenant_id=tenant.id,
    template_name="welcome",
    to_email=user.email,
    context={
        "user_name": user.name,
        "login_url": f"https://{tenant.slug}.example.com/login"
    }
)

# Example 2: Send password reset email
await notification_service.send_email(
    tenant_id=tenant.id,
    template_name="password_reset",
    to_email=user.email,
    context={
        "reset_token": reset_token,
        "reset_url": f"https://{tenant.slug}.example.com/reset?token={reset_token}",
        "expires_in_hours": 24
    }
)

# Example 3: Send custom notification
await notification_service.send_email(
    tenant_id=tenant.id,
    template_name="post_published",
    to_email=author.email,
    context={
        "author_name": author.name,
        "post_title": post.title,
        "post_url": f"https://{tenant.slug}.example.com/posts/{post.id}",
        "published_at": post.published_at.isoformat()
    }
)
```

### Email Templates

**Default templates location:** `src/app/templates/email/`

**Template structure:**

```jinja2
{# src/app/templates/email/welcome.html.jinja2 #}
{% extends "base.html.jinja2" %}

{% block content %}
<h1>Welcome to {{ tenant_name }}, {{ user_name }}!</h1>

<p>We're excited to have you on board.</p>

<p>
  <a href="{{ login_url }}" style="background: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
    Get Started
  </a>
</p>

<p>If you have any questions, reply to this email.</p>
{% endblock %}
```

**Available context variables (injected automatically):**
- `tenant_name` - Current tenant name
- `tenant_slug` - Current tenant slug
- `base_url` - Tenant-specific base URL
- Any variables passed in `context` parameter

### Customizing Templates per Tenant

Tenants can override default templates via the API:

```python
# Admin creates custom template for their tenant
@router.post("/email-templates")
async def create_template_override(
    data: EmailTemplateOverrideCreate,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(require_admin)
):
    """
    Create custom email template for tenant.
    Falls back to default if not overridden.
    """
    override = await notification_service.create_template_override(
        tenant_id=tenant.id,
        template_name=data.template_name,
        subject_override=data.subject,
        content=data.content  # Full HTML content
    )
    return override
```

**Template override example:**

```python
# Tenant customizes welcome email
override_data = {
    "template_name": "welcome",
    "subject": "🎉 Welcome to Acme Corp!",
    "content": """
        <html>
        <body style="font-family: Arial;">
            <img src="https://acme.com/logo.png" alt="Logo" />
            <h1>Hi {{ user_name }}, welcome aboard!</h1>
            <p>Our custom onboarding message here...</p>
            <a href="{{ login_url }}">Login Now</a>
        </body>
        </html>
    """
}
```

### Checking Notification Status

```python
# Get notification history
notifications = await notification_service.get_notifications(
    tenant_id=tenant.id,
    limit=20
)

for notification in notifications:
    print(f"Email: {notification.to_email}")
    print(f"Status: {notification.status}")  # pending, sent, failed
    print(f"Sent At: {notification.sent_at}")
    if notification.error_message:
        print(f"Error: {notification.error_message}")
```

---

## 🪝 Webhooks

### Subscribing to Webhooks (Tenant API)

Tenants create webhook endpoints to receive events:

```python
@router.post("/webhooks/endpoints")
async def create_webhook_endpoint(
    data: WebhookEndpointCreate,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant)
):
    """
    Allow tenant to subscribe to events.
    """
    endpoint = await webhook_service.create_endpoint(
        tenant_id=tenant.id,
        url=data.url,  # e.g., https://customer.com/api/webhooks
        event_type=data.event_type,  # e.g., "user.created"
        secret=data.secret  # For signature verification
    )
    return endpoint
```

**Example webhook endpoint creation:**

```python
webhook_data = {
    "url": "https://my-app.com/webhooks/saas-events",
    "event_type": "post.published",
    "secret": "whsec_abc123xyz..."  # Strong random string
}
```

### Triggering Webhooks (Internal)

When an event occurs, trigger webhooks for all subscribed endpoints:

```python
from src.app.modules.webhooks.service import WebhookService

webhook_service = WebhookService()

# In your business logic (e.g., after publishing a post)
@router.patch("/posts/{post_id}/publish")
async def publish_post(
    post_id: UUID,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant)
):
    # Publish the post
    post = await post_service.publish(db, post_id)
    
    # Trigger webhooks for all subscribed endpoints
    await webhook_service.trigger_event(
        tenant_id=tenant.id,
        event_type="post.published",
        payload={
            "event": "post.published",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {
                "post_id": str(post.id),
                "title": post.title,
                "author_id": str(post.author_id),
                "published_at": post.published_at.isoformat(),
                "url": f"https://{tenant.slug}.example.com/posts/{post.id}"
            }
        }
    )
    
    return post
```

### Webhook Signature Verification (Customer Side)

Customers receiving webhooks should verify signatures:

**Python example (customer's webhook receiver):**

```python
import hmac
import hashlib
from fastapi import Request, HTTPException

@app.post("/webhooks/saas-events")
async def receive_webhook(request: Request):
    """Customer endpoint receiving webhooks from our SaaS."""
    
    # Get signature from header
    signature = request.headers.get("X-Webhook-Signature")
    timestamp = request.headers.get("X-Webhook-Timestamp")
    webhook_id = request.headers.get("X-Webhook-ID")
    
    # Get raw body
    body = await request.body()
    
    # Verify signature
    secret = "whsec_abc123xyz..."  # Their webhook secret
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        body,
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(f"sha256={expected_signature}", signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Verify timestamp (prevent replay attacks)
    webhook_timestamp = int(timestamp)
    current_timestamp = int(datetime.utcnow().timestamp())
    if abs(current_timestamp - webhook_timestamp) > 300:  # 5 minutes
        raise HTTPException(status_code=401, detail="Timestamp too old")
    
    # Process webhook
    data = await request.json()
    print(f"Received event: {data['event']}")
    print(f"Post ID: {data['data']['post_id']}")
    
    return {"status": "received"}
```

### Webhook Retry Logic

Webhooks are automatically retried on failure using exponential backoff:

**Retry Schedule:**
1. **Attempt 1:** Immediate (on event trigger)
2. **Attempt 2:** After 1 minute
3. **Attempt 3:** After 5 minutes
4. **Attempt 4:** After 15 minutes
5. **Attempt 5:** After 30 minutes
6. **Attempt 6:** After 1 hour

**Success criteria:** HTTP status 200-299  
**Failure criteria:** HTTP status >= 300, timeout (30s), network error

### Monitoring Webhook Deliveries

```python
@router.get("/webhooks/deliveries")
async def get_webhook_deliveries(
    endpoint_id: UUID | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant)
):
    """Get delivery history for webhooks."""
    deliveries = await webhook_service.get_deliveries(
        tenant_id=tenant.id,
        endpoint_id=endpoint_id,
        status=status,  # pending, delivered, failed
        limit=50
    )
    
    return {
        "deliveries": [
            {
                "id": str(d.id),
                "event_type": d.event_type,
                "status": d.status,
                "attempt_count": d.attempt_count,
                "response_status": d.response_status,
                "created_at": d.created_at.isoformat(),
                "next_retry_at": d.next_retry_at.isoformat() if d.next_retry_at else None
            }
            for d in deliveries
        ]
    }
```

---

## 🚩 Feature Flags

### Checking Feature Flags

```python
from src.app.core.feature_flags import check_feature
from src.app.api.dependencies import get_current_tenant

@router.get("/analytics")
async def get_analytics(
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant)
):
    """Analytics endpoint (requires feature flag)."""
    
    # Check if advanced analytics is enabled
    if not await check_feature(tenant, "advanced_analytics", default=False):
        raise HTTPException(
            status_code=403,
            detail="Advanced analytics not enabled for your plan"
        )
    
    # Return analytics data
    analytics = await analytics_service.get_dashboard(db, tenant.id)
    return analytics
```

### Common Feature Flags

**Tier-based features:**

```python
# Free tier
{
    "api_access": False,
    "advanced_analytics": False,
    "custom_branding": False,
    "webhook_limit": 0,
    "email_templates": False
}

# Pro tier
{
    "api_access": True,
    "advanced_analytics": True,
    "custom_branding": False,
    "webhook_limit": 10,
    "email_templates": True
}

# Enterprise tier
{
    "api_access": True,
    "advanced_analytics": True,
    "custom_branding": True,
    "webhook_limit": 100,
    "email_templates": True,
    "sso": True,
    "audit_retention_days": 365
}
```

### Setting Feature Flags (Admin Only)

```python
@router.patch("/tenants/{tenant_id}/feature-flags")
async def update_feature_flags(
    tenant_id: UUID,
    flags: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Update tenant feature flags (superadmin only)."""
    
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    
    # Merge with existing flags
    current_flags = tenant.feature_flags or {}
    current_flags.update(flags)
    
    tenant.feature_flags = current_flags
    await db.commit()
    
    # Audit the change
    await audit_log_service.create_log(
        action="tenant.feature_flags_updated",
        tenant_id=tenant_id,
        user_id=current_user.id,
        entity_type="tenant",
        entity_id=tenant_id,
        details={"updated_flags": flags}
    )
    
    return {"feature_flags": tenant.feature_flags}
```

### Using Flags for Gradual Rollout

```python
# Enable new feature for beta tenants only
BETA_TENANTS = [
    UUID("550e8400-e29b-41d4-a716-446655440000"),  # Tenant A
    UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8"),  # Tenant B
]

@router.get("/new-feature")
async def new_feature_endpoint(
    tenant: Tenant = Depends(get_current_tenant)
):
    """New feature in beta testing."""
    
    # Check if enabled via flag OR beta tenant
    is_enabled = (
        await check_feature(tenant, "new_feature_beta") or
        tenant.id in BETA_TENANTS
    )
    
    if not is_enabled:
        raise HTTPException(403, "Feature not available")
    
    return {"message": "New feature data"}
```

### Feature Flag Dependency Injection

Create a reusable dependency:

```python
from fastapi import Depends, HTTPException
from src.app.api.dependencies import get_current_tenant
from src.app.core.feature_flags import check_feature

def require_feature(feature_name: str, default: bool = False):
    """Dependency to require a feature flag."""
    async def dependency(tenant: Tenant = Depends(get_current_tenant)):
        if not await check_feature(tenant, feature_name, default):
            raise HTTPException(
                status_code=403,
                detail=f"Feature '{feature_name}' not enabled"
            )
        return tenant
    return dependency

# Usage in endpoints
@router.get("/analytics", dependencies=[Depends(require_feature("advanced_analytics"))])
async def get_analytics():
    # Only runs if feature is enabled
    pass
```

---

## 🧪 Testing

### Testing Event Bus

```python
import pytest
from src.app.core.events import event_bus

@pytest.mark.asyncio
async def test_event_publishing():
    """Test event publishing and subscription."""
    
    # Track if handler was called
    called = []
    
    @event_bus.subscribe("test.event", priority="CRITICAL")
    async def test_handler(data: dict):
        called.append(data)
    
    # Publish event
    await event_bus.publish(
        "test.event",
        {"test": "data"},
        priority="CRITICAL"
    )
    
    # Verify handler was called
    assert len(called) == 1
    assert called[0]["test"] == "data"
```

### Testing Audit Logging

```python
@pytest.mark.asyncio
async def test_audit_logging(db_session, test_tenant, test_user):
    """Test audit log creation."""
    
    from src.app.modules.audit.service import AuditService
    
    audit_service = AuditService()
    
    # Log an action
    await audit_service.log_action(
        db=db_session,
        action="test.action",
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        entity_type="test",
        entity_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
        details={"test": "data"}
    )
    
    # Query logs
    logs = await audit_service.get_logs(
        db=db_session,
        tenant_id=test_tenant.id,
        action="test.action"
    )
    
    assert len(logs) == 1
    assert logs[0].action == "test.action"
    assert logs[0].details["test"] == "data"
```

### Testing Notifications

```python
@pytest.mark.asyncio
async def test_email_sending(db_session, test_tenant, mocker):
    """Test email notification sending."""
    
    # Mock ARQ queue
    mock_enqueue = mocker.patch("src.app.core.queue.enqueue_task")
    
    from src.app.modules.notifications.service import NotificationService
    
    notification_service = NotificationService()
    
    # Send email
    await notification_service.send_email(
        db=db_session,
        tenant_id=test_tenant.id,
        template_name="welcome",
        to_email="test@example.com",
        context={"user_name": "Test User"}
    )
    
    # Verify task was enqueued
    mock_enqueue.assert_called_once()
    call_args = mock_enqueue.call_args[1]
    assert call_args["template_name"] == "welcome"
    assert call_args["to_email"] == "test@example.com"
```

### Testing Webhooks

```python
@pytest.mark.asyncio
async def test_webhook_delivery(db_session, test_tenant, httpx_mock):
    """Test webhook delivery with mocked HTTP client."""
    
    from src.app.modules.webhooks.service import WebhookService
    
    webhook_service = WebhookService()
    
    # Create webhook endpoint
    endpoint = await webhook_service.create_endpoint(
        db=db_session,
        tenant_id=test_tenant.id,
        url="https://example.com/webhook",
        event_type="test.event",
        secret="test_secret"
    )
    
    # Mock successful HTTP response
    httpx_mock.add_response(
        method="POST",
        url="https://example.com/webhook",
        status_code=200
    )
    
    # Trigger event
    await webhook_service.trigger_event(
        db=db_session,
        tenant_id=test_tenant.id,
        event_type="test.event",
        payload={"test": "data"}
    )
    
    # Verify delivery was created
    deliveries = await webhook_service.get_deliveries(
        db=db_session,
        tenant_id=test_tenant.id
    )
    
    assert len(deliveries) == 1
    assert deliveries[0].status == "delivered"
```

### Testing Feature Flags

```python
@pytest.mark.asyncio
async def test_feature_flags(db_session, test_tenant):
    """Test feature flag checking."""
    
    from src.app.core.feature_flags import check_feature
    
    # Initially no flags set
    assert not await check_feature(test_tenant, "advanced_analytics")
    
    # Enable flag
    test_tenant.feature_flags = {"advanced_analytics": True}
    await db_session.commit()
    
    # Check flag is enabled
    assert await check_feature(test_tenant, "advanced_analytics")
    
    # Check with default
    assert not await check_feature(test_tenant, "nonexistent_flag", default=False)
    assert await check_feature(test_tenant, "nonexistent_flag", default=True)
```

---

## 🔧 Troubleshooting

### Event Bus Not Firing

**Problem:** Event published but handlers not called

**Solutions:**
1. Verify handler is registered:
   ```python
   # In main.py or module __init__.py
   from src.app.modules.users.handlers import register_user_events
   register_user_events()  # Must be called at startup!
   ```

2. Check priority matches:
   ```python
   # Handler priority must match publish priority
   @event_bus.subscribe("user.created", priority="CRITICAL")
   async def handler(data): ...
   
   # Publish with matching priority
   await event_bus.publish("user.created", {...}, priority="CRITICAL")
   ```

3. Check ARQ worker is running (for NORMAL/LOW priority):
   ```bash
   # Start ARQ worker
   uv run arq src.app.core.queue.WorkerSettings
   ```

### Audit Logs Not Appearing

**Problem:** Actions not being logged

**Solutions:**
1. Verify decorator is applied:
   ```python
   @audit_action("post.created", entity_type="post")  # Must be ABOVE route decorator
   @router.post("/posts")
   async def create_post(...): ...
   ```

2. Check event bus is publishing:
   ```python
   # Decorator publishes "audit.log" event
   # Verify handler is registered for this event
   ```

3. Check database for logs:
   ```sql
   SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 10;
   ```

### Emails Not Sending

**Problem:** Emails stuck in "pending" status

**Solutions:**
1. Check ARQ worker is running
2. Verify SMTP settings in `.env`:
   ```bash
   EMAIL_HOST=smtp.gmail.com
   EMAIL_PORT=587
   EMAIL_USERNAME=your-email@gmail.com
   EMAIL_PASSWORD=your-app-password
   EMAIL_FROM=noreply@example.com
   ```

3. Check notification error messages:
   ```python
   notifications = await notification_service.get_notifications(db, tenant_id)
   for n in notifications:
       if n.error_message:
           print(f"Error: {n.error_message}")
   ```

4. Test SMTP connection:
   ```python
   import aiosmtplib
   
   async def test_smtp():
       smtp = aiosmtplib.SMTP(hostname="smtp.gmail.com", port=587)
       await smtp.connect()
       await smtp.starttls()
       await smtp.login("user", "pass")
       print("SMTP connection successful!")
   ```

### Webhooks Not Delivering

**Problem:** Webhook deliveries showing "failed" status

**Solutions:**
1. Check customer endpoint is reachable:
   ```bash
   curl -X POST https://customer.com/webhook \
     -H "Content-Type: application/json" \
     -d '{"test": "data"}'
   ```

2. Verify signature is correct (customer side):
   - Use `hmac.compare_digest()` for timing-safe comparison
   - Ensure secret matches exactly

3. Check retry schedule:
   ```python
   deliveries = await webhook_service.get_deliveries(db, tenant_id)
   for d in deliveries:
       print(f"Attempts: {d.attempt_count}")
       print(f"Next retry: {d.next_retry_at}")
       print(f"Response: {d.response_status} - {d.response_body}")
   ```

4. Increase timeout if customer endpoint is slow:
   ```python
   # In webhook delivery ARQ task
   response = await httpx.post(..., timeout=60.0)  # Increase from 30s
   ```

### Feature Flags Not Working

**Problem:** Flag enabled but check returns False

**Solutions:**
1. Verify flag is set in database:
   ```sql
   SELECT feature_flags FROM tenants WHERE id = 'tenant-uuid';
   ```

2. Check JSONB syntax:
   ```python
   # Correct
   tenant.feature_flags = {"advanced_analytics": True}
   
   # Wrong (string instead of bool)
   tenant.feature_flags = {"advanced_analytics": "true"}  # ❌
   ```

3. Ensure tenant is refreshed:
   ```python
   await db.refresh(tenant)  # Reload from DB
   flag_enabled = await check_feature(tenant, "advanced_analytics")
   ```

---

## 📚 Additional Resources

### API Documentation
- OpenAPI specs: `specs/003-core-services/contracts/`
- Interactive docs: `http://localhost:8000/docs` (after server start)

### Data Models
- Database schemas: `specs/003-core-services/data-model.md`
- Migration files: `src/migrations/versions/`

### Code Examples
- Event handlers: `src/app/modules/*/handlers.py`
- Service classes: `src/app/modules/*/service.py`
- API endpoints: `src/app/modules/*/router.py`

### Testing
- Test examples: `tests/integration/test_core_services.py`
- Fixtures: `tests/conftest.py`

---

**Questions? Issues?**  
Check the troubleshooting section or refer to the technical specification at `specs/003-core-services/spec.md`.
