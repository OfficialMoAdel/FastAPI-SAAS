# Audit Logs Module - Agent Guidance

## Package Identity

Immutable audit logging system for tracking all system actions with tenant isolation. Stores action history for compliance, security monitoring, and debugging. Uses PostgreSQL with JSONB for flexible details storage.

## Setup & Run

```bash
# Test audit logs module
uv run pytest tests/test_audit.py -v

# Type check module
uv run mypy src/app/modules/audit_logs/

# Lint module
uv run ruff check src/app/modules/audit_logs/
```

## Patterns & Conventions

### Module Structure
```
src/app/modules/audit_logs/
├── __init__.py           # Module exports (exports audit, AuditGuarantee, SYSTEM_TENANT_ID, AuditLogService)
├── trail.py              # Canonical typed audit() interface with auto-tenant & sanitizer
├── router.py             # FastAPI routes for log queries
├── service.py            # AuditLogService for querying + the single delivery writer (buffer_audit_log)
├── models.py             # AuditLog model (immutable)
├── schemas.py            # Pydantic schemas (AuditLogEventData, etc.)
└── AGENTS.md             # This file
```

### Immutable Audit Log
✅ DO: Audit logs are immutable - no UPDATE or DELETE
```python
# src/app/modules/audit_logs/models.py (lines 16-36)
class AuditLog(Base):
    """Audit log for tracking all system actions.

    Immutable table - no UPDATE or DELETE operations allowed.
    Used for compliance, security monitoring, and debugging.
    """
    __tablename__ = "audit_logs"

    # Use UUID v7 for time-ordered IDs
    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        insert_default=uuid7,
        server_default=text("gen_random_uuid()"),
        init=False,  # Database-generated
    )
```

### Create Audit Log Entry

Use `await audit(...)` from the module facade for feature audit events. It owns validation, attribution, sanitization, and delivery policy; see Delivery Policy below. `AuditLogService.create_log()` remains a low-level direct-write API used internally by exports.

### Log Query with Filters
✅ DO: Support filtering by action, user, entity, date range
```python
# src/app/modules/audit_logs/router.py (lines 24-75)
@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    action: Optional[str] = Query(None),
    user_id: Optional[UUID] = Query(None),
    entity_type: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
) -> AuditLogListResponse:
    """List audit logs for the current tenant with filters."""
    query = select(AuditLog).where(AuditLog.tenant_id == current_tenant.id)

    if action:
        query = query.where(AuditLog.action == action)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if start_date:
        query = query.where(AuditLog.created_at >= start_date)
    if end_date:
        query = query.where(AuditLog.created_at <= end_date)

    query = query.order_by(AuditLog.created_at.desc())
    # ... pagination and execution
```

### Stream audit exports

Use `await AuditLogService(db).stream_export(criteria, actor=user_id)` to obtain an async byte generator. Build `AuditExportCriteria` with the authorized tenant ID and optional action, user, entity type, and inclusive date filters. `criteria.user_id` selects records; `actor` identifies the requester in the export audit event. Callers must authorize access before invoking the service.

The service owns counting, the 100,000-record limit, SQL ordering, cursor lifetime, projection, CSV/JSON/JSONL encoding, and export auditing. It rejects oversized exports before returning the generator, orders rows by `created_at` then `id`, and streams in batches of 1,000 with a SQL limit. CSV cells retain spreadsheet-formula escaping. The router only adapts request criteria and returns `StreamingResponse` with the existing content type and filename.

The `audit_logs.export` event records an accepted request, not a completed download. Its `record_count` is the preflight count, not a snapshot guarantee: concurrent writes can change the streamed rows. The current export excludes its own event; prior export events remain eligible.

Keep the supplied database session open until consumption finishes. If a non-HTTP consumer stops early, close the generator with `aclose()` or `contextlib.aclosing`. The generator closes its result cursor in `finally`; stream failures propagate rather than returning a successful empty export.

Verify with:

```bash
uv run pytest tests/unit/modules/audit_logs/ tests/integration/modules/audit_logs/ tests/contract/test_audit_isolation.py
```

Unit tests cover format compatibility, filters, limits, actor attribution, and cleanup. SQLite-backed tests cover query behavior; PostgreSQL cursor/RLS behavior and a full 100,000-row download require separate environment-backed verification.

### Async Audit Delivery

The canonical `audit()` call awaits buffer acceptance or its database fallback. It does not publish to the event bus. Legacy event handlers adapt old publishers through the same seam using best-effort delivery.

## Touch Points / Key Files

### Service Layer
- Service: `src/app/modules/audit_logs/service.py` - `AuditLogService` and delivery writer
- Seam: `src/app/modules/audit_logs/trail.py` - `audit()` and `AuditGuarantee`

### Models
- AuditLog: `src/app/modules/audit_logs/models.py` - Immutable audit log model
- Indexes: `tenant_id`, `user_id`, `action`, `created_at`

### Router
- Router: `src/app/modules/audit_logs/router.py` - Query endpoints at `/api/v1/audit-logs`

### Schemas
- Response: `AuditLogResponse` - Single log entry
- List: `AuditLogListResponse` - Paginated list with filters

## JIT Index Hints

```bash
# Find audit log creation
rg -n "create_log|audit_log|AuditLog" src/app/

# Find audit decorators
rg -n "@audit_action|audit_action\(" src/app/

# Find audit event publishing
rg -n "audit\.|event_bus\.publish.*audit" src/app/

# Find audit queries
rg -n "/audit-logs" src/app/modules/audit_logs/

# Find audit models
rg -n "class AuditLog" src/app/modules/audit_logs/
```

## Common Gotchas

### Immutable Records
✅ DO: Never update or delete audit logs
❌ DON'T: Add UPDATE/DELETE operations to AuditLog model

### Performance
✅ DO: Use the canonical `audit()` interface for typed, non-blocking audit logging
```python
from src.app.modules.audit_logs import audit

# Typed, sanitized audit logging
await audit(
    action="user.created",
    entity_type="user",
    entity_id=user.id,
    details={"username": user.username},
)
```

❌ DON'T: Hand-build raw `event_bus.publish("audit.log", {...})` dicts

### Tenant Isolation
✅ DO: Always filter by `tenant_id`
```python
query = select(AuditLog).where(AuditLog.tenant_id == current_tenant.id)
```

### Action Naming
✅ DO: Use dot-notation for actions (e.g., `user.created`, `post.updated`)
```python
# Standard action naming
ACTION_USER_CREATED = "user.created"
ACTION_USER_LOGIN = "user.login"
ACTION_USER_LOGOUT = "user.logout"
ACTION_POST_CREATED = "post.created"
ACTION_SETTINGS_UPDATED = "settings.updated"
```

### Details Payload
✅ DO: Store relevant context in details JSONB
```python
# Include relevant context
details = {
    "old_value": old_user.model_dump(),
    "new_value": new_user.model_dump(),
    "changed_fields": ["email", "name"],
    "request_id": request.state.request_id,
}
```

## Delivery Policy (AuditGuarantee)

Route feature audit writes through `await audit()` — it validates, sanitizes,
stamps `created_at`, and delivers through one writer:

```python
from src.app.modules.audit_logs import AuditGuarantee, audit

await audit(
    action="user.created",
    entity_type="user",
    entity_id=user.id,
    details={"username": user.username},
    guarantee=AuditGuarantee.REQUIRED,
)
```

Delivery pipeline (single persistence path, B4):
1. `trail.audit()` — tenant resolution (explicit → context → logged `SYSTEM_TENANT_ID`
   sentinel with `_tenant_fallback: system` stamp), `LogSanitizer` sanitization,
   `AuditLogEventData` validation, `created_at` stamping.
2. `service.buffer_audit_log(payload)` — the ONE writer: Redis RPUSH+LTRIM
   (`audit_log_buffer`, newest MAXLEN=10000 kept) → on Redis failure, ONE fallback
   `_direct_insert_log(payload)` with its own `local_session()` that commits and
   PROPAGATES failures. Neither touches a caller's business session.

Guarantees (`src/app/modules/audit_logs/trail.py`):
- `AuditGuarantee.REQUIRED` — writer errors propagate to the caller (buffer
  acceptance or independent DB fallback commit; NOT crash-proof or transactional
  with business writes).
- `AuditGuarantee.BEST_EFFORT` — writer errors are logged (`audit_delivery_failed`,
  error type only) and swallowed; cancellation still propagates.
- `guarantee=None` (default) — maps from `settings.auth.AUDIT_LOG_FAILURE_MODE`
  directly: `fail_closed` → REQUIRED, `fail_open` → BEST_EFFORT.

**REQUIRED ≠ durable.** `REQUIRED` means the writer's buffer acceptance (or its
independent fallback commit) succeeded — not crash-proof persistence, and not
atomicity with the caller's business writes. Design callers so a lost trail is
a loud, countable event, never a silent gap: worker RLS bypass uses
record-then-proceed (`core/worker/rls_context.py`) and emits the error-level
`audit_bypass_unaudited` event (alert on it, count it) when the
`system.rls_bypass` record cannot be written. Never gate a job on audit infra;
never swallow a missing trail without that metric.

Callers provide business facts only — no per-caller try/except, no fire-and-forget
task machinery, no `priority=` parameter. The fire-and-forget event bus is OFF the
canonical path; the legacy handlers `handle_audit_log_event` ("audit.log") and
`handle_login_issued_event` ("auth.login_issued") remain as thin adapters calling
`audit(..., guarantee=BEST_EFFORT)` so older publishers keep working.

❌ DON'T: Publish raw `event_bus.publish("audit.log", {...})` dicts, call
`buffer_audit_log`/`_direct_insert_log` directly from feature code, or wrap `audit()`
in caller-side swallow/re-raise logic — the failure policy lives once, at the seam.

## Pre-PR Checks

```bash
# Test audit log creation and querying
uv run pytest tests/test_audit.py -v

# Type check
uv run mypy src/app/modules/audit_logs/

# Lint
uv run ruff check src/app/modules/audit_logs/

# Test audit decorator
uv run pytest tests/test_audit_decorator.py -v

# Verify immutability
uv run python -c "
from src.app.modules.audit_logs.models import AuditLog
print('AuditLog model loaded - immutable pattern verified')
"
```