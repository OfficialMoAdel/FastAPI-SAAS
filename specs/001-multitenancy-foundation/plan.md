# Implementation Plan: Multi-tenancy Foundation

**Branch**: `001-multitenancy-foundation` | **Date**: 2025-12-28 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/001-multitenancy-foundation/spec.md`

## Summary

Establish a robust, mandatory, header-based multi-tenancy foundation using PostgreSQL row-level isolation with `tenant_id` foreign keys. The implementation includes a Tenant domain module, request-scoped tenant context via contextvars, middleware for X-Tenant-ID header validation with whitelisted paths, and a safe migration strategy that backfills existing users to a default "System" tenant without data loss.

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: FastAPI 0.109+, SQLAlchemy 2.0 (async), Pydantic v2, FastCRUD, asyncpg  
**Storage**: PostgreSQL with asyncpg driver  
**Testing**: pytest, pytest-asyncio  
**Target Platform**: Linux server (containerized)  
**Project Type**: Single backend API  
**Performance Goals**: <5ms tenant resolution, <50ms request rejection, 1000 concurrent requests  
**Constraints**: Header-based only (no subdomains), no RBAC in Phase 1, zero data loss during migration  
**Scale/Scope**: Multi-tenant SaaS foundation, existing user migration required

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Async-First Architecture | ✅ PASS | All DB operations use async SQLAlchemy, middleware is async |
| II. Strict Type Safety | ✅ PASS | All functions will have type hints, Pydantic v2 schemas |
| III. Test-Driven Quality | ✅ PASS | Unit + integration tests required per DoD |
| IV. Modular Service Architecture | ✅ PASS | Tenant module at `src/app/modules/tenants/` |
| V. Security by Default | ✅ PASS | No hardcoded secrets, auth required for tenant mgmt |
| VI. PostgreSQL-Only | ✅ PASS | All data in PostgreSQL, no MongoDB |
| VII. Multi-Tenancy Row-Level | ✅ PASS | `tenant_id` FK on User table, row-level isolation |
| VIII. Tenant Resolution Headers | ✅ PASS | X-Tenant-ID header, no subdomains |

**Gate Status**: ✅ PASSED - Proceed to Phase 0

## Project Structure

### Documentation (this feature)

```text
specs/001-multitenancy-foundation/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── openapi.yaml
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
src/app/
├── modules/
│   └── tenants/                    # NEW: Tenant domain module
│       ├── __init__.py
│       ├── router.py               # Tenant CRUD endpoints
│       ├── service.py              # Tenant business logic
│       ├── models.py               # Tenant SQLAlchemy model
│       ├── schemas.py              # Pydantic schemas
│       └── crud.py                 # FastCRUD operations
│   └── users/
│       └── models.py               # MODIFIED: Add tenant_id FK
├── core/
│   └── tenancy/                    # NEW: Tenant context infrastructure
│       ├── __init__.py
│       └── context.py              # Contextvar for tenant context
├── middleware/
│   └── tenant.py                   # NEW: Tenant validation middleware
├── api/
│   └── dependencies.py             # MODIFIED: Add get_current_tenant()
└── main.py                         # MODIFIED: Register middleware + router

migrations/
└── versions/
    └── xxxx_add_tenants_and_user_tenant_id.py  # NEW: Safe migration with backfill

tests/
├── unit/
│   └── test_tenant_service.py      # NEW
├── integration/
│   └── test_tenant_api.py          # NEW
└── contract/
    └── test_tenant_middleware.py   # NEW
```

**Structure Decision**: Single backend API following existing module pattern at `src/app/modules/`. Tenant context infrastructure goes in `src/app/core/tenancy/` per separation of concerns.

## Complexity Tracking

> No constitution violations requiring justification.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| N/A | N/A | N/A |
