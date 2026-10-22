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
- **Auth-critical mail has its OWN rate-limit bucket** (`service.py::_rate_limited`): the
  keys are `notif:{min,hour}:{auth|general}:{tenant_id}`. The `auth` bucket is for the
  templates in `AUTH_CRITICAL_TEMPLATES` (`email_verification`, `magic_link`,
  `password_reset`); everything else uses `general`. Without the split, a tenant that
  exhausted its own email budget also blocked its own password-reset / magic-link /
  email-verification mail — a tenant could lock out its own users' authentication.
  This is a **separate bucket, not a bypass**: the auth bucket has the same size as
  the general one, so an auth flood is still bounded, and the tenant's TOTAL budget
  becomes 2x. It is safe to widen because the auth PATHS are themselves budgeted
  upstream (`magic_link_send` is 3/min per email hash, `auth` is 5/min per IP).
  The carve-out is keyed on the TEMPLATE, not on `user_id` — a rate bucket is about
  tenant mail volume, not about one user's preferences.
- **Template overrides are COMPILE-CHECKED before they are persisted**
  (`service.py::NotificationService._validate_override_content`, called from
  `create_template_override`). Broken Jinja is rejected with a `ValidationError` (400)
  at the admin edge instead of 500-ing every send of that template. The check uses the
  SAME sandboxed `jinja_env` the renderer uses, so a template accepted here renders
  there. It proves COMPILATION only — a template referencing an undefined variable
  compiles fine and renders to an empty string at send time, which is a content
  mistake, not a broken template, and is deliberately NOT rejected here.
- **The recovery sweep only touches a PROVABLY-LOST enqueue**
  (`service.py::recover_stuck_notifications`). The ARQ job id IS the notification id
  (`_create_and_enqueue` enqueues with `_job_id=str(notification.id)`), so liveness is
  one `arq:job:{id}` lookup and needs no schema column. A row whose job still exists is
  queued or deferred and MUST NOT be swept — sweeping it during a long worker outage
  destroys deliverable mail. When Redis cannot answer, the sweep degrades to its
  age-only behaviour (and logs it), because never sweeping would leak PENDING rows
  forever — the outbox's bounded-terminal-state contract forbids that.
- **A failed notification carries a machine-readable `error_kind`**
  (`Notification.error_kind`, defined in migration layer `07_observability`). The kinds are
  the stable `ERROR_KIND_*` constants in `service.py`: `rate_limited`, `enqueue_error`,
  `queue_unavailable`, `recovery_window_exhausted`, `send_error`. Operators filter on
  the KIND, not on the subject text — the sentinel subject (`"Rate limited: ..."`) is
  for humans and mixes into history sorted by subject. The column is NULL on a
  non-failed row, and historical rows keep NULL because their cause is genuinely
  unknown; do not backfill it.
- **`except ValueError` around quiet hours is correct because the column is text, not a gap**: `should_send_notification` (`service.py:608-633`) reads the raw ORM row and does `time.fromisoformat(prefs.quiet_hours_start)` / `..._end` inside a `try` whose only handler is `except ValueError` (`:631`). It looks too narrow, and it was: on a database built by `alembic` those columns were `sa.Time(timezone=True)` (MEASURED in `perf-results/RESULT-20261018-9cmn/`), so they read back as `datetime.time` and `fromisoformat` raised an **uncaught `TypeError`** that escaped the whole function — the caller got an error instead of a notification, and `UserPreferencesRead` did not catch it either because Pydantic coerces `time` into the `str` it declares. `FastAPI_saas-nqk6` fixed the schema instead: layer 05 now declares `sa.String(length=5)`. The database is dropped and rebuilt (`DROP SCHEMA public CASCADE` + `alembic upgrade head`) because the schema is not shipped yet — if layer 05 is ever applied in production, the rule reverses and the fix becomes a correction plus a revision that alters the existing columns. **DO NOT widen the handler to `(TypeError, ValueError)`** — with a text column `fromisoformat` can only raise `ValueError`, so catching `TypeError` would be handling an impossible scenario and would mask the exact regression `tests/unit/migrations/test_user_preferences_quiet_hours_parity.py` exists to catch.

## Pre-PR Checks

```bash
uv run pytest tests/integration/modules/notifications/test_api.py -v && \
uv run ruff check src/app/modules/notifications/
```
