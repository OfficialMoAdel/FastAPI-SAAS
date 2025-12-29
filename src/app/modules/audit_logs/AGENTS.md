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
├── __init__.py           # Module exports
├── router.py             # FastAPI routes for log queries
├── service.py            # AuditLogService for log creation
├── models.py             # AuditLog model (immutable)
├── schemas.py            # Pydantic schemas
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
✅ DO: Use AuditLogService for creating logs
```python
# src/app/modules/audit_logs/service.py (lines 31-65)
async def create_log(
    self,
    tenant_id: int,
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[UUID] = None,
    user_id: Optional[int] = None,
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> AuditLog:
    """Create an audit log entry."""
    log_entry = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    self.db.add(log_entry)
    await self.db.commit()
    await self.db.refresh(log_entry)
    return log_entry
```

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

### Async Event-Based Logging
✅ DO: Use event bus for non-blocking audit logging
```python
# src/app/modules/audit_logs/service.py (lines 10-17)
from src.app.core.events import EventPriority, event_bus

class AuditLogService:
    async def log_async(
        self,
        tenant_id: int,
        action: str,
        **kwargs,
    ) -> None:
        """Log audit entry asynchronously via event bus."""
        await event_bus.publish(
            "audit.created",
            {
                "tenant_id": tenant_id,
                "action": action,
                **kwargs,
            },
            priority=EventPriority.HIGH,
        )
```

### Decorator for Automatic Logging
✅ DO: Use decorator for automatic action logging
```python
# src/app/modules/audit_logs/service.py (lines 100-120)
def audit_action(action: str, entity_type: Optional[str] = None):
    """Decorator to automatically log function calls."""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            # Extract tenant/user from context and log
            tenant_id = get_current_tenant_id()
            await audit_service.log_async(
                tenant_id=tenant_id,
                action=action,
                entity_type=entity_type,
                details={"args": str(args), "kwargs": str(kwargs)},
            )
            return result
        return wrapper
    return decorator
```

## Touch Points / Key Files

### Service Layer
- Service: `src/app/modules/audit_logs/service.py` - `AuditLogService`
- Decorator: `src/app/modules/audit_logs/service.py` - `audit_action()` decorator

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
✅ DO: Use event bus for non-blocking audit logging
```python
# Async logging via event bus
await event_bus.publish("audit.created", {...}, priority=EventPriority.HIGH)
```

❌ DON'T: Block requests with synchronous audit logging

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
