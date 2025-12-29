# Implementation Plan: Phase 3 Core Services

**Branch**: `003-core-services` | **Date**: 2024-12-29 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/003-core-services/spec.md`

## Summary

Implement foundational SaaS infrastructure services for multi-tenant FastAPI platform:
1. **Audit Logging** - Automatic tracking of all write operations with PostgreSQL storage
2. **Notification System** - Transactional emails using Jinja2 templates with ARQ delivery
3. **Webhooks** - Tenant-configurable event subscriptions with HMAC-SHA256 signatures and retry logic
4. **Feature Flags** - Tenant-level feature toggles via JSONB on tenants table

**Technical Approach**: Event-driven architecture using hybrid event bus (in-memory for critical, ARQ for async), PostgreSQL for all data storage, re-engineered from MongoDB reference implementation.

---

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 (async), Pydantic v2, ARQ (background tasks), Jinja2 (email templates), httpx (webhook delivery)  
**Storage**: PostgreSQL 14+ with JSONB support, Redis (ARQ queue)  
**Testing**: pytest with async support, pytest-asyncio, factory fixtures  
**Target Platform**: Linux server (Docker containers)  
**Project Type**: Web (multi-tenant SaaS backend)  
**Performance Goals**: 
- Audit logs: <10ms write latency (fire-and-forget)
- Webhooks: 5 retries over 1 hour with exponential backoff
- Email delivery: Async via ARQ with retry on SMTP failures
- Feature flag checks: <5ms (in-memory JSONB lookup, per NFR-005)

**Constraints**:
- Multi-tenancy: ALL tables MUST have `tenant_id` FK for row-level isolation
- No MongoDB: All data in PostgreSQL (despite reference implementation using MongoDB)
- Async-first: All I/O operations must use async/await
- Immutability: Audit logs cannot be modified or deleted after creation
- Event priority: Critical events (audit) sync, normal events (webhooks/emails) queued
- Event priority queue sizes: CRITICAL=1000 (guaranteed), NORMAL=5000 (delayed under load), LOW=10000 (may drop under extreme load)

**Scale/Scope**:
- Support 1000+ tenants per instance
- Audit logs: 10k writes/minute sustained
- Webhook deliveries: 1k/minute with retry queue
- Email notifications: 500/minute via ARQ workers

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

✅ **PASSED - No violations**

**Compliance Summary**:
- ✅ PostgreSQL only (no MongoDB despite reference)
- ✅ Multi-tenancy enforced via `tenant_id` FK on all tables
- ✅ SQLAlchemy 2.0 async patterns (DeclarativeBase + MappedAsDataclass)
- ✅ Existing ARQ queue (no new background task system)
- ✅ Type hints on all models (Mapped[] type annotations)
- ✅ No additional complexity (reusing existing patterns)

**Justification for Stack Additions**:
- `jinja2`: Required for email template rendering (replaces inline HTML strings)
- `httpx`: Required for async HTTP webhook delivery (lightweight, async-native)
- `hmac` (stdlib): Required for webhook signature generation (no dependency)

---

## Project Structure

### Documentation (this feature)

```text
specs/003-core-services/
├── spec.md                          # ✅ Feature specification (Phase -1)
├── research.md                      # ✅ Technology decisions & patterns (Phase 0)
├── data-model.md                    # ✅ PostgreSQL schemas & models (Phase 1)
├── quickstart.md                    # ✅ Developer guide (Phase 1)
├── plan.md                          # ✅ This file (Phase 1)
├── HANDOFF-PROMPT.md                # ✅ Session handoff context
├── contracts/                       # ✅ API contracts (Phase 1)
│   ├── audit-logs-api.yaml          # Audit log endpoints
│   ├── notifications-api.yaml       # Notification endpoints
│   ├── webhooks-api.yaml            # Webhook CRUD endpoints
│   └── feature-flags-api.yaml       # Feature flag endpoints
├── checklists/                      # 🔜 Quality gates (Phase 2)
│   ├── pre-implementation.md
│   └── implementation-quality.md
└── tasks.md                         # 🔜 Task breakdown (Phase 2)
```

### Source Code (repository root)

```text
src/app/
├── core/
│   ├── events.py                    # 🔜 Event bus implementation
│   ├── audit.py                     # 🔜 Audit logging decorator & service
│   ├── feature_flags.py             # 🔜 Feature flag helpers
│   └── db/
│       └── models.py                # ✅ Existing (Base, Mixins)
│
├── modules/
│   ├── audit_logs/                  # 🔜 NEW MODULE
│   │   ├── models.py                # AuditLog SQLAlchemy model
│   │   ├── schemas.py               # Pydantic request/response schemas
│   │   ├── service.py               # Business logic
│   │   ├── repository.py            # Database operations (FastCRUD)
│   │   └── router.py                # API endpoints
│   │
│   ├── notifications/               # 🔜 NEW MODULE
│   │   ├── models.py                # Notification, EmailTemplateOverride models
│   │   ├── schemas.py               # Request/response schemas
│   │   ├── service.py               # Email rendering & sending logic
│   │   ├── repository.py            # Database operations
│   │   ├── router.py                # API endpoints
│   │   └── templates.py             # Template loading & rendering
│   │
│   ├── webhooks/                    # 🔜 NEW MODULE
│   │   ├── models.py                # WebhookEndpoint, WebhookDelivery models
│   │   ├── schemas.py               # Request/response schemas
│   │   ├── service.py               # Webhook trigger & delivery logic
│   │   ├── repository.py            # Database operations
│   │   ├── router.py                # API endpoints
│   │   ├── signatures.py            # HMAC-SHA256 signature generation
│   │   └── tasks.py                 # ARQ delivery tasks
│   │
│   ├── feature_flags/               # 🔜 NEW MODULE
│   │   ├── schemas.py               # Request/response schemas
│   │   ├── service.py               # Feature flag checks
│   │   └── router.py                # API endpoints (admin only)
│   │
│   ├── tenants/                     # ✅ EXISTING (modify)
│   │   └── models.py                # Add feature_flags JSONB column
│   │
│   └── users/                       # ✅ EXISTING (reference)
│       └── models.py                # FK target for audit logs
│
├── templates/                       # 🔜 NEW DIRECTORY
│   └── email/
│       ├── base.html.jinja2         # Base layout template
│       ├── welcome.html.jinja2      # Welcome email
│       ├── password_reset.html.jinja2
│       ├── invitation.html.jinja2
│       └── notification.html.jinja2
│
└── api/
    └── v1/
        └── __init__.py              # Register new routers

migrations/versions/
└── 003_add_core_services.py         # 🔜 Alembic migration

tests/
├── unit/
│   ├── test_audit_service.py        # 🔜 Audit logging tests
│   ├── test_event_bus.py            # 🔜 Event bus tests
│   ├── test_feature_flags.py        # 🔜 Feature flag tests
│   └── test_webhook_signatures.py   # 🔜 Signature generation tests
│
├── integration/
│   ├── test_audit_api.py            # 🔜 Audit log endpoint tests
│   ├── test_notifications_api.py    # 🔜 Notification endpoint tests
│   ├── test_webhooks_api.py         # 🔜 Webhook endpoint tests
│   └── test_webhook_delivery.py     # 🔜 Delivery retry tests
│
└── contract/
    ├── test_audit_isolation.py      # 🔜 Tenant isolation tests
    └── test_webhook_security.py     # 🔜 Signature verification tests
```

**Structure Decision**: Single FastAPI backend project with modular architecture. Each feature (audit_logs, notifications, webhooks, feature_flags) is a separate module under `src/app/modules/` following existing patterns (users, tenants, auth). Cross-cutting concerns (event bus, decorators) in `src/app/core/`.

---

## Phase 0: Research & Technology Decisions ✅ COMPLETE

**Deliverable**: [research.md](./research.md) (500+ lines)

**Key Decisions Made**:

### 1. Event Bus Architecture
- **Choice**: Hybrid approach (in-memory + ARQ)
- **Critical Events** (audit logs): In-memory fire-and-forget, direct DB write
- **Async Events** (emails, webhooks): ARQ background tasks with retry
- **Rationale**: Balance reliability (critical) vs. performance (async)

### 2. Audit Log Storage
- **Choice**: PostgreSQL table with JSONB
- **NOT**: File-based (reference implementation approach)
- **Rationale**: Multi-tenant safe, queryable, indexed, referential integrity
- **Schema**: `tenant_id` FK, immutable, indexed by (tenant, created_at)

### 3. Email Templates
- **Choice**: Jinja2 file-based + database overrides
- **NOT**: Inline Python strings (reference implementation)
- **Default Location**: `src/app/templates/email/*.html.jinja2`
- **Override Storage**: `email_template_overrides` table per tenant
- **Rationale**: Version control defaults, UI customization via DB

### 4. Webhook Signatures
- **Choice**: HMAC-SHA256
- **Format**: `X-Webhook-Signature: sha256={hex_digest}`
- **Industry Standard**: Stripe, GitHub, Twilio pattern
- **Per-endpoint Secrets**: Hashed storage in `webhook_endpoints.secret`

### 5. Webhook Retry Strategy
- **Choice**: 5 retries over 1 hour (exponential backoff)
- **Schedule**: 1m, 5m, 15m, 30m, 1h
- **Implementation**: ARQ `_defer_by` parameter
- **Tracking**: `webhook_deliveries.attempt_count`, `next_retry_at`

### 6. Feature Flags
- **Choice**: JSONB column on `tenants` table
- **NOT**: Separate table
- **Rationale**: Fast lookup (no JOIN), flexible schema, tenant-scoped
- **Access Pattern**: `tenant.feature_flags->>'flag_name'`

**MongoDB → PostgreSQL Translation**: See research.md sections for detailed mappings of collections → tables, BSON → SQL types, aggregations → CTEs.

---

## Phase 1: Data Modeling & Contracts ✅ COMPLETE

**Deliverables**:
- [data-model.md](./data-model.md) (1000+ lines)
- [contracts/audit-logs-api.yaml](./contracts/audit-logs-api.yaml)
- [contracts/notifications-api.yaml](./contracts/notifications-api.yaml)
- [contracts/webhooks-api.yaml](./contracts/webhooks-api.yaml)
- [contracts/feature-flags-api.yaml](./contracts/feature-flags-api.yaml)
- [quickstart.md](./quickstart.md)

### Database Schema Summary

**5 New Tables + 1 Modified**:

1. **audit_logs** (new)
   - Primary Key: UUID
   - Foreign Keys: tenant_id, user_id (nullable)
   - JSONB: details column
   - Indexes: (tenant_id, created_at), user_id, action, (entity_type, entity_id)
   - Constraints: Immutable (no UPDATE/DELETE)

2. **notifications** (new)
   - Primary Key: UUID
   - Foreign Keys: tenant_id, user_id
   - Status: ENUM('pending', 'sent', 'failed')
   - Indexes: (tenant_id, status), user_id, created_at

3. **webhook_endpoints** (new)
   - Primary Key: UUID
   - Foreign Keys: tenant_id
   - Unique: (tenant_id, url)
   - Fields: secret (hashed), event_type (VARCHAR), is_active (BOOLEAN)

4. **webhook_deliveries** (new)
   - Primary Key: UUID
   - Foreign Keys: webhook_endpoint_id
   - JSONB: payload column
   - Status: ENUM('pending', 'delivered', 'failed')
   - Indexes: (webhook_endpoint_id, created_at), status, next_retry_at

5. **email_template_overrides** (new)
   - Primary Key: UUID
   - Foreign Keys: tenant_id
   - Unique: (tenant_id, template_name)
   - Fields: subject_override, content (TEXT)

6. **tenants** (modified)
   - ADD COLUMN: feature_flags JSONB DEFAULT '{}'
   - Migration: `ALTER TABLE tenants ADD COLUMN feature_flags JSONB DEFAULT '{}'::jsonb;`

### API Contracts Summary

**4 OpenAPI 3.0 Specifications**:

1. **Audit Logs API**
   - `GET /api/v1/audit-logs` - List with filtering (action, user_id, date range)
   - `GET /api/v1/audit-logs/{id}` - Get single entry
   - No POST/PUT/DELETE (write-only via decorator)

2. **Notifications API**
   - `POST /api/v1/notifications/send` - Send email
   - `GET /api/v1/notifications` - List delivery history
   - `GET /api/v1/notifications/{id}` - Get delivery details
   - `POST /api/v1/notifications/templates/{name}/preview` - Preview template

3. **Webhooks API**
   - `GET /api/v1/webhooks/endpoints` - List endpoints
   - `POST /api/v1/webhooks/endpoints` - Create endpoint
   - `GET /api/v1/webhooks/endpoints/{id}` - Get endpoint
   - `PATCH /api/v1/webhooks/endpoints/{id}` - Update endpoint
   - `DELETE /api/v1/webhooks/endpoints/{id}` - Delete endpoint
   - `GET /api/v1/webhooks/deliveries` - List deliveries
   - `POST /api/v1/webhooks/endpoints/{id}/test` - Test delivery

4. **Feature Flags API**
   - `GET /api/v1/feature-flags` - List all flags for tenant
   - `GET /api/v1/feature-flags/{flag_name}` - Check specific flag
   - `PATCH /api/v1/feature-flags` - Bulk update (admin only)

---

## Phase 2: Implementation Tasks 🔜 NEXT

**Goal**: Break down implementation into executable tasks with dependencies

**Estimated Effort**: 3-5 days (1 developer)

### Task Categories

1. **Database Layer** (Tasks 1-6)
   - Create SQLAlchemy models
   - Write Alembic migration
   - Create repositories (FastCRUD)
   - Add indexes and constraints

2. **Core Infrastructure** (Tasks 7-12)
   - Implement event bus
   - Create audit decorator
   - Build email template loader
   - Implement webhook signature generation
   - Create feature flag helpers
   - Add ARQ task functions

3. **Service Layer** (Tasks 13-18)
   - Audit log service
   - Notification service
   - Webhook service
   - Feature flag service
   - Event handlers

4. **API Layer** (Tasks 19-22)
   - Audit log endpoints
   - Notification endpoints
   - Webhook endpoints
   - Feature flag endpoints

5. **Testing** (Tasks 23-30)
   - Unit tests for services
   - Integration tests for APIs
   - Contract tests for security
   - Performance tests

6. **Documentation & Deployment** (Tasks 31-35)
   - Email templates
   - API documentation
   - Environment variables
   - Migration guide
   - Production deployment

**Detailed Task Breakdown**: See [tasks.md](./tasks.md) (created via `/speckit.tasks` command)

---

## Phase 3: Quality Assurance 🔜 FUTURE

**Checklists** (in `checklists/` directory):

1. **Pre-Implementation Checklist**
   - [ ] All dependencies reviewed (no new major frameworks)
   - [ ] Migration strategy validated (backwards compatible)
   - [ ] Performance benchmarks defined
   - [ ] Security review completed (HMAC, secrets, SQL injection)

2. **Implementation Quality Checklist**
   - [ ] All models have tenant_id FK
   - [ ] All endpoints enforce tenant isolation
   - [ ] Audit logs immutable (no UPDATE/DELETE)
   - [ ] Webhook signatures verified on delivery
   - [ ] Email templates XSS-safe (Jinja2 autoescaping)
   - [ ] ARQ tasks idempotent (safe retry)
   - [ ] Type hints on all functions
   - [ ] Error logging implemented
   - [ ] Tests pass (unit, integration, contract)
   - [ ] mypy passes (no type errors)
   - [ ] ruff passes (no lint warnings)

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **Audit log volume** | High DB load | Index optimization, archival strategy (90 days) |
| **Webhook retry storms** | ARQ queue saturation | Rate limiting per tenant (100 req/min) |
| **Email delivery failures** | Lost notifications | ARQ retry with backoff, delivery tracking |
| **Template injection** | XSS vulnerability | Jinja2 autoescaping enabled by default |
| **Secret leakage** | Security breach | Hash webhook secrets, never log in plaintext |
| **Missing tenant_id** | Data leakage | Database constraints, dependency injection |

---

## Success Criteria (from spec.md)

1. ✅ All write operations automatically audited
2. ✅ Tenants can configure webhook endpoints via UI
3. ✅ Emails sent async with 95% delivery within 1 minute
4. ✅ Webhooks retry 5x over 1 hour before failing
5. ✅ Feature flags checked in <5ms (cached lookup per NFR-005)
6. ✅ All tables have tenant_id for isolation
7. ✅ API endpoints documented in OpenAPI specs
8. ✅ Tests cover happy path + edge cases (>80% coverage)
9. ✅ Migration from current state is backwards compatible
10. ✅ Developer quickstart guide available

---

## Dependencies

**Existing Modules**:
- `src/app/modules/tenants` - FK target, feature_flags column addition
- `src/app/modules/users` - FK target for audit logs
- `src/app/core/db` - Base classes, async session
- `src/app/core/queue` - ARQ setup for background tasks

**New Dependencies** (to add to `pyproject.toml`):
```toml
[tool.poetry.dependencies]
jinja2 = "^3.1.2"        # Email template rendering
httpx = "^0.25.0"        # Async HTTP client for webhooks

[tool.poetry.group.dev.dependencies]
# (no new dev dependencies)
```

**Configuration** (to add to `src/app/core/config.py`):
```python
# Email settings
SMTP_HOST: str = "smtp.gmail.com"
SMTP_PORT: int = 587
SMTP_USER: str
SMTP_PASSWORD: str
SMTP_FROM: str
EMAIL_TEMPLATES_DIR: str = "src/app/templates/email"

# Webhook settings
WEBHOOK_TIMEOUT: int = 30  # seconds
WEBHOOK_MAX_RETRIES: int = 5
WEBHOOK_RETRY_DELAYS: list[int] = [60, 300, 900, 1800, 3600]

# Audit settings
AUDIT_RETENTION_DAYS: int = 90
AUDIT_BATCH_SIZE: int = 100
```

---

## Next Steps

1. ✅ **Phase 0 Complete** - Research & decisions documented
2. ✅ **Phase 1 Complete** - Data models & API contracts created
3. 🔜 **Phase 2 Next** - Run `/speckit.tasks` to generate task breakdown
4. 🔜 **Phase 3** - Implement models & migrations
5. 🔜 **Phase 4** - Implement services & APIs
6. 🔜 **Phase 5** - Write tests
7. 🔜 **Phase 6** - Deploy to staging

**Command to Continue**:
```bash
# Generate detailed task breakdown
/speckit.tasks

# Or manually create tasks.md following template in .specify/templates/
```

---

**Plan Status**: ✅ Phase 1 Complete - Ready for Task Breakdown  
**Last Updated**: 2024-12-29  
**Approved By**: [Pending review]
