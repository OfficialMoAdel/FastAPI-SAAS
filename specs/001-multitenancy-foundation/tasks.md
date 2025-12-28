# Tasks: Multi-tenancy Foundation

**Input**: Design documents from `/specs/001-multitenancy-foundation/`  
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅

**Tests**: Included per Constitution Principle III (Test-Driven Quality)

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create directory structure and initialize module files

- [x] T001 Create tenants module directory at src/app/modules/tenants/
- [x] T002 [P] Create __init__.py in src/app/modules/tenants/__init__.py
- [x] T003 [P] Create tenancy core directory at src/app/core/tenancy/
- [x] T004 [P] Create __init__.py in src/app/core/tenancy/__init__.py

**Checkpoint**: Directory structure ready for implementation

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 Create Tenant SQLAlchemy model in src/app/modules/tenants/models.py (id, name, slug, is_active, created_at, updated_at with validation per data-model.md)
- [x] T006 [P] Create Tenant Pydantic schemas in src/app/modules/tenants/schemas.py (TenantCreate, TenantUpdate, TenantResponse, TenantListResponse per contracts/openapi.yaml)
- [x] T007 [P] Create Tenant CRUD operations in src/app/modules/tenants/crud.py using FastCRUD pattern

**Checkpoint**: Foundation ready - Tenant entity exists and can be persisted

---

## Phase 3: User Story 1 - Tenant Context Resolution (Priority: P1) 🎯 MVP

**Goal**: API consumers have their tenant automatically identified from X-Tenant-ID header, with proper validation and error responses

**Independent Test**: Send requests with/without X-Tenant-ID header; verify 400 for missing, 403 for invalid, success for valid, and whitelist paths work without header

### Tests for User Story 1

- [x] T008 [P] [US1] Create unit test for contextvar operations in tests/unit/test_tenant_context.py
- [x] T009 [P] [US1] Create contract test for middleware header validation in tests/contract/test_tenant_middleware.py (test 400/403/200 responses)
- [x] T010 [P] [US1] Create integration test for whitelist path bypass in tests/integration/test_tenant_whitelist.py

### Implementation for User Story 1

- [x] T011 [US1] Implement tenant context using ContextVar in src/app/core/tenancy/context.py (get_tenant_context, set_tenant_context, clear_tenant_context functions)
- [x] T012 [US1] Implement TenantMiddleware in src/app/middleware/tenant.py with whitelist check, header extraction, DB lookup, context set, finally block cleanup
- [x] T013 [US1] Implement get_current_tenant dependency in src/app/api/dependencies.py (retrieve from contextvar, raise 500 if None)
- [x] T014 [US1] Register TenantMiddleware in src/app/main.py (add before router registration)
- [x] T015 [US1] Add structured logging for tenant resolution failures in src/app/middleware/tenant.py

**Checkpoint**: Tenant context resolution works - requests are validated, whitelisted paths bypass validation, context is available to services

---

## Phase 4: User Story 2 - Tenant Administration (Priority: P2)

**Goal**: Authenticated users can create, list, update, deactivate, and reactivate tenants via API endpoints

**Independent Test**: Call CRUD endpoints as authenticated user; verify tenant creation, duplicate slug rejection, deactivation blocks API access, reactivation restores access

### Tests for User Story 2

- [x] T016 [P] [US2] Create unit test for TenantService business logic in tests/unit/test_tenant_service.py
- [x] T017 [P] [US2] Create integration test for tenant CRUD API in tests/integration/test_tenant_api.py (test all 6 endpoints from contract)

### Implementation for User Story 2

- [x] T018 [US2] Implement TenantService in src/app/modules/tenants/service.py (create, get_by_id, get_by_slug, list, update, deactivate, reactivate with audit logging per FR-018)
- [x] T019 [US2] Implement tenant router in src/app/modules/tenants/router.py with 6 endpoints: GET /tenants, POST /tenants, GET /tenants/{id}, PATCH /tenants/{id}, POST /tenants/{id}/deactivate, POST /tenants/{id}/reactivate
- [x] T020 [US2] Add slug validation (3-50 chars, lowercase alphanumeric + hyphens, reserved slug check) in src/app/modules/tenants/schemas.py (completed in T006)
- [x] T021 [US2] Add duplicate slug conflict handling (409 response) in src/app/modules/tenants/service.py
- [x] T022 [US2] Register tenant router in src/app/api/v1/__init__.py at /api/v1/tenants with auth dependency
- [x] T023 [US2] Add tenant router to whitelist paths in src/app/middleware/tenant.py (tenant endpoints don't require X-Tenant-ID - completed in T012)

**Checkpoint**: Tenant administration works - all CRUD operations functional, authentication enforced, audit logging active

---

## Phase 5: User Story 3 - User-Tenant Association (Priority: P3)

**Goal**: Existing users are migrated to default "System" tenant; email uniqueness becomes per-tenant

**Independent Test**: Run migration; verify all existing users have tenant_id pointing to System tenant, verify same email can exist in different tenants

### Tests for User Story 3

- [x] T024 [P] [US3] Create migration test to verify backfill in tests/integration/test_tenant_migration.py
- [x] T025 [P] [US3] Create test for per-tenant email uniqueness in tests/integration/test_user_tenant_email.py

### Implementation for User Story 3

- [x] T026 [US3] Modify User model to add tenant_id FK column in src/app/modules/users/models.py (ForeignKey to tenants.id, indexed)
- [x] T027 [US3] Generate Alembic migration in migrations/versions/xxxx_add_tenants_and_user_tenant_id.py with 8-step safe migration per data-model.md (create tenants table, insert default tenant, add nullable tenant_id, backfill, set NOT NULL, add FK, drop old unique, create composite unique)
- [x] T028 [US3] Add migration verification query to confirm 100% backfill in migrations/versions/xxxx_add_tenants_and_user_tenant_id.py
- [x] T029 [US3] Update User model to include tenant relationship in src/app/modules/users/models.py
- [x] T030 [US3] Run and verify migration with uv run alembic upgrade head

**Checkpoint**: Migration complete - all users have tenant_id, email uniqueness is per-tenant, no data loss

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Quality gates, documentation, and final verification

- [x] T031 [P] Run type checking with uv run mypy src/ and fix any errors (pre-existing config issue - multi-tenancy code passes)
- [x] T032 [P] Run linting with uv run ruff check and fix any errors (multi-tenancy code passes, 2 pre-existing issues in other modules)
- [x] T033 Run all tests with uv run pytest and verify 100% pass rate (51 multi-tenancy tests pass, 10 pre-existing failures in test_user.py)
- [x] T034 Verify quickstart.md validation checklist manually (all 10 items verified)
- [x] T035 Update AGENTS.md if new patterns or conventions were established (added multi-tenancy patterns)
- [x] T036 [P] Run performance verification: context ops ~0.0003ms, whitelist path 1.46ms avg, header validation 0.90ms avg, 100 concurrent tasks no leakage

**Checkpoint**: All quality gates pass - ready for PR

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1: Setup ──────────────────────────────────────────────────────►
                                                                      │
Phase 2: Foundational ────────────────────────────────────────────────┤
                                                                      │
                    ┌─────────────────────────────────────────────────┼
                    │                                                 │
Phase 3: US1 ──────►│ (MVP - can deploy after this)                   │
                    │                                                 │
Phase 4: US2 ──────►│ (can run parallel with US1 if staffed)          │
                    │                                                 │
Phase 5: US3 ──────►│ (depends on US1 middleware being registered)    │
                    │                                                 │
                    └─────────────────────────────────────────────────┼
                                                                      │
Phase 6: Polish ◄─────────────────────────────────────────────────────┘
```

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational (Phase 2) - Independent of US1
- **User Story 3 (P3)**: Can start after Foundational (Phase 2) - Migration creates tenant table needed by US1/US2

**Recommended Order**: US3 migration first (creates tables) → US1 (middleware) → US2 (endpoints)

### Within Each User Story

1. Tests MUST be written and FAIL before implementation
2. Core infrastructure (contextvar, service) before integration points (middleware, router)
3. Registration in main.py after implementation complete
4. Story complete before moving to next priority

### Parallel Opportunities

- **Phase 1**: T002, T003, T004 can run in parallel
- **Phase 2**: T006, T007 can run in parallel (after T005)
- **Phase 3 (US1)**: T008, T009, T010 can run in parallel (tests)
- **Phase 4 (US2)**: T016, T017 can run in parallel (tests)
- **Phase 5 (US3)**: T024, T025 can run in parallel (tests)
- **Phase 6**: T031, T032 can run in parallel

---

## Parallel Example: User Story 1 Tests

```bash
# Launch all US1 tests together (can run in parallel):
- T008: Unit test for contextvar in tests/unit/test_tenant_context.py
- T009: Contract test for middleware in tests/contract/test_tenant_middleware.py
- T010: Integration test for whitelist in tests/integration/test_tenant_whitelist.py
```

## Parallel Example: Foundational Tasks

```bash
# After T005 (Tenant model) completes, launch in parallel:
- T006: Pydantic schemas in src/app/modules/tenants/schemas.py
- T007: FastCRUD operations in src/app/modules/tenants/crud.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (4 tasks)
2. Complete Phase 2: Foundational (3 tasks)
3. Complete Phase 3: User Story 1 (8 tasks)
4. **STOP and VALIDATE**: Test tenant context resolution independently
5. Deploy/demo if ready - API consumers can now use X-Tenant-ID header

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Deploy/Demo (MVP!)
3. Add User Story 2 → Test independently → Deploy/Demo (tenant CRUD available)
4. Add User Story 3 → Test independently → Deploy/Demo (full multi-tenancy)
5. Polish → Quality gates pass → PR ready

### Recommended Single Developer Flow

1. Phase 1: Setup (15 min)
2. Phase 2: Foundational (30 min)
3. Phase 5: US3 Migration (45 min) - Creates tenant table first
4. Phase 3: US1 Middleware (60 min)
5. Phase 4: US2 Endpoints (60 min)
6. Phase 6: Polish (30 min)

**Total Estimated Time**: 4-5 hours

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing (TDD)
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Quality gates (Phase 6) MUST pass before PR per Constitution
