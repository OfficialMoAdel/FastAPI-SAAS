# Implementation Plan: [FEATURE]

**Branch**: `002-rbac-foundation` | **Date**: 2025-12-28 | **Spec**: `/specs/002-rbac-foundation/spec.md`
**Input**: Feature specification from `/specs/002-rbac-foundation/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Implement a secure, tenant-isolated Role-Based Access Control (RBAC) system. Features include Role management (CRUD) scoped per Tenant, Global Permission definitions (Enum-based), and User-Role assignment. Uses a normalized `role_permissions` table and immediate cache invalidation for security.

## Technical Context

**Language/Version**: Python 3.11+ (FastAPI)
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.0 (Async), Alembic, Redis (for caching)
**Storage**: PostgreSQL (with asyncpg)
**Testing**: pytest (Unit, Integration, Contract)
**Target Platform**: Linux server (Dockerized)
**Project Type**: Web API (Modular Monolith)
**Performance Goals**: < 100ms permission checks (cached), 1000+ concurrent requests
**Constraints**: Row-Level Multi-Tenancy (Strict), Async-First, No Audit Logging (Phase 3)
**Scale/Scope**: ~10 new endpoints, 4 new tables, Global Permission Enum

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Async-First Architecture
- [x] **Verified**: All RBAC database operations and checks will use `async/await`.

### II. Strict Type Safety
- [x] **Verified**: Pydantic v2 models used for all inputs/outputs. Mypy strict compliance required.

### III. Test-Driven Quality
- [x] **Verified**: Plan includes Unit, Integration, and Contract tests.

### IV. Modular Service Architecture
- [x] **Verified**: New module `src/app/modules/rbac` follows standard layout.

### V. Security by Default
- [x] **Verified**: Permissions in code (Enum), Cache invalidation on update.

### VI. PostgreSQL-Only Data Storage
- [x] **Verified**: RBAC tables (Roles, Permissions) stored in Postgres.

### VII. Multi-Tenancy via Row-Level Isolation
- [x] **Verified**: `Role` table includes `tenant_id`. Queries filtered by current tenant.

### VIII. Tenant Resolution via HTTP Headers
- [x] **Verified**: Uses existing middleware/dependency for `X-Tenant-ID`.

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/app/modules/rbac/
├── __init__.py          # Module exposure
├── models.py            # SQLAlchemy models (Permission, Role, UserRole)
├── schemas.py           # Pydantic models (RoleCreate, RoleUpdate, RoleOut)
├── service.py           # Business logic (Role management, Assignments)
├── router.py            # API endpoints
└── crud.py              # Database operations (FastCRUD)

src/app/core/
├── rbac/
│   ├── permissions.py   # Global Permission Enum
│   └── dependencies.py  # Permission checker (Depends)
```

**Structure Decision**: Standard modular architecture with core dependencies for cross-cutting concerns (permission checking).


## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|

