# Notifications Module - Agent Guidance

## Package Identity

Email notification service with template support, tenant overrides, and delivery tracking. Uses Jinja2 for templates and ARQ for background delivery.

## Setup & Run

```bash
# No separate install - uses root dependencies
uv run pytest tests/integration/modules/notifications/test_api.py -v

# View email templates
ls -la src/app/templates/email/
```

## Patterns & Conventions

### DO: Use template rendering pattern

```python
from src.app.modules.notifications.service import NotificationService

service = NotificationService(db)
subject, html = await service.render_template(
    tenant_id=tenant_id,
    template_name="welcome",
    context={"user_name": "John", "app_url": "https://app.example.com"},
)
```

### DON'T: Hardcode email content

```python
# BAD - Hardcoded HTML in Python
async def send_welcome(self, email: str):
    html = f"<html><body>Welcome {email}!</body></html>"  # Don't do this
```

### DO: Use tenant template overrides

```python
# Tenant-specific templates stored in EmailTemplateOverride table
# Falls back to file-based templates in src/app/templates/email/
```

### DO: Handle async delivery

```python
# BackgroundTasks used for non-blocking email delivery
@router.post("/send", status_code=202)
async def send_notification(..., background_tasks: BackgroundTasks):
    # Returns 202 Accepted - delivery happens async
```

## Module Structure

```
src/app/modules/notifications/
├── __init__.py           # Exports NotificationService and email providers
├── router.py             # /notifications endpoints
├── service.py            # NotificationService, the acceptance sequence, and outbox event handling
├── template_cache.py     # email_template:<name> cache key family (read/write/invalidate)
├── email_provider.py     # EmailProvider, SMTPProvider, InMemoryEmailProvider seam
├── models.py             # Notification, EmailTemplateOverride
└── schemas.py            # Pydantic schemas

## Seams

- **Acceptance sequence** — `NotificationService._accept_notification()` is the one
  ordered path into the email pipeline: preference gate (auth-critical templates
  bypass quiet hours) -> rate-limit refusal (FAILED audit row, then
  `RateLimitExceededError`) -> render, persist PENDING, enqueue. Both doors run it:
  the direct door `send_email()` (returns `Notification | None`) and the event door
  `handle_notification_request_event()` (returns nothing, logs). The doors differ
  only in session/RLS setup and return shape, never in the steps.
- **Template cache** — `template_cache.py` owns the
  `build_tenant_key("email_template:<name>", tenant_id)` key family and its 1-hour
  TTL; read/write/invalidate go through it rather than rebuilding the key by hand.

## Touch Points

- **Service logic**: `service.py::NotificationService`
- **Email templates**: `src/app/templates/email/*.html.jinja2`
- **Models**: `modules/notifications/models.py::Notification`
- **Event integration**: `src.app.core.events` for async delivery

## JIT Index Hints

```bash
# Find notification endpoints
rg -n "router\.(get|post)" src/app/modules/notifications/

# Find template files
rg -n "\.html\.jinja2" src/app/modules/notifications/

# Find email sends
rg -n "send_email" src/app/modules/

# Find template overrides
rg -n "EmailTemplateOverride" src/app/modules/
```

## Common Gotchas

- **Template names**: Use underscores (`welcome_email`, not `welcome email`)
- **Status 202**: POST /send returns 202 Accepted, not 200 OK
- **Template lookup**: Checks override table first, then file system
- **Rate limits**: Email sending is rate-limited via `rate_limiter`

## Pre-PR Checks

```bash
uv run pytest tests/integration/modules/notifications/test_api.py -v && \
uv run ruff check src/app/modules/notifications/
```
