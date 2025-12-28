# Implementation Tasks: RBAC Foundation

**Branch**: `002-rbac-foundation`
**Spec**: `specs/002-rbac-foundation/spec.md`

## Phase 1: Setup
*Goal: Initialize module structure and core configuration.*

- [x] T001 Create module directory structure at src/app/modules/rbac/
- [x] T002 Create core directory structure at src/app/core/rbac/
- [x] T003 [P] Create empty __init__.py files in new directories to make them packages

## Phase 2: Foundational (Blocking)
*Goal: Database models, global definitions, and initial seeding. Must be complete before any user stories.*

- [x] T004 Define global Permission Enum in src/app/core/rbac/permissions.py
- [x] T005 [P] Implement Permission, Role, RolePermission, and UserRole models in src/app/modules/rbac/models.py
- [x] T006 [P] Create base Pydantic schemas (Permission, RoleBase) in src/app/modules/rbac/schemas.py
- [x] T007 Generate Alembic migration for RBAC tables
- [x] T008 Implement seeding logic (Raw SQL) for Permissions and System Roles in the migration file
- [x] T009 Implement requires_permission dependency stub in src/app/core/rbac/dependencies.py

## Phase 3: User Story 3 - Permission-Based Access Control (P1)
*Goal: Core security mechanism. Implemented early to protect US1 endpoints.*

- [x] T010 [US3] Implement full requires_permission logic with Redis caching in src/app/core/rbac/dependencies.py
- [x] T011 [US3] Create unit tests for permission checker isolation in tests/unit/test_rbac_dependency.py
- [x] T012 [P] [US3] Implement Redis cache key generation utility in src/app/core/rbac/utils.py

## Phase 4: User Story 1 - Tenant Admin Role Management (P1)
*Goal: Allow admins to manage custom roles.*

- [x] T013 [P] [US1] Define RoleCreate, RoleUpdate, RoleOut schemas in src/app/modules/rbac/schemas.py
- [x] T014 [US1] Implement RoleService (create, update, delete, list) with system role protection in src/app/modules/rbac/service.py
- [x] T015 [US1] Implement Role CRUD endpoints protected by requires_permission in src/app/modules/rbac/router.py
- [x] T016 [US1] Register RBAC router in src/app/main.py (or api router)
- [x] T017 [US1] Create integration tests for Role CRUD (success and failure cases) in tests/integration/test_rbac_roles.py

## Phase 5: User Story 2 - Assigning Roles to Users (P2)
*Goal: Assign roles to users and ensure immediate effect.*

- [x] T018 [P] [US2] Add UserRoleAssignment schema in src/app/modules/rbac/schemas.py
- [x] T019 [US2] Implement assign_role_to_user and remove_role_from_user in src/app/modules/rbac/service.py
- [x] T020 [US2] Implement cache invalidation logic (Event-Driven) in service methods in src/app/modules/rbac/service.py
- [x] T021 [US2] Add role assignment endpoints to src/app/modules/rbac/router.py
- [x] T022 [US2] Create integration tests for cross-tenant assignment rejection in tests/integration/test_rbac_assignment.py

## Phase 6: Polish & Cross-Cutting
*Goal: Final verification and performance checks.*

- [ ] T023 Verify 1000+ concurrent permission checks (Benchmark) in tests/performance/test_rbac_perf.py
- [ ] T024 Ensure 403 Forbidden is returned for all unauthorized access in tests/contract/test_rbac_security.py
- [ ] T025 [P] Run full test suite and linting
- [ ] T026 Verify system role seeding (Admin/Member) and permissions in tests/integration/test_rbac_seeding.py
- [ ] T027 Benchmark role creation latency (<100ms) in tests/performance/test_rbac_perf.py

## Implementation Strategy
- **MVP**: Complete Phases 1, 2, 3, and 4. This provides a working system where roles can be managed and permissions checked, even if assignments are done manually via DB initially.
- **Incremental**: US3 (Checking) is built before US1 (Management) because US1 needs the protection mechanism.

## Progress Summary
- **Phase 1**: ✅ Complete
- **Phase 2**: ✅ Complete
- **Phase 3**: ✅ Complete
- **Phase 4**: ✅ Complete
- **Phase 5**: ✅ Complete
- **Phase 6**: ⏳ Pending (Performance & Polish)

## Test Coverage
- **Unit Tests**: 15 tests in `tests/unit/test_rbac_dependency.py`
- **Integration Tests**: 9 tests in `tests/integration/test_rbac_roles.py`
- **Assignment Tests**: 5 tests in `tests/integration/test_rbac_assignment.py`
- **Total**: 29 passing tests

## Dependencies
1. Phase 1 & 2 (Models/Seeding) -> BLOCKS ALL
2. Phase 3 (Checking) -> BLOCKS Phase 4 (Endpoints need protection)
3. Phase 4 (Roles) -> BLOCKS Phase 5 (Need roles to assign)

## Parallel Execution Examples
- **US1**: Developer A implements `RoleService` (T014) while Developer B writes `test_rbac_roles.py` (T017) based on the schemas.
- **US3**: Cache utility (T012) can be written independently of the main dependency logic (T010).
