# Tasks: Phase 3 Core Services

**Input**: Design documents from `/specs/003-core-services/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/
**Tests**: The examples below include test tasks. Tests are OPTIONAL - only include them if explicitly requested in the feature specification.
**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/`, `tests/` at repository root
- **Web app**: `backend/src/`, `frontend/src/`
- **Mobile**: `api/src/`, `ios/src/` or `android/src/`
- Paths shown below assume single project - adjust based on plan.md structure

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Add jinja2 and httpx dependencies to pyproject.toml
- [ ] T002 Add email and webhook settings to src/app/core/config.py
- [ ] T003 [P] Add NotificationStatus and WebhookDeliveryStatus enums to src/app/core/db/models.py
- [ ] T004 [P] Create module directories for audit_logs, notifications, webhooks, feature_flags under src/app/modules/
- [ ] T005 [P] Create templates/email directory structure under src/app/templates/
- [ ] T006 [P] Update src/app/core/db/__init__.py to import new enums

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T007 Create Alembic migration 003_add_core_services_tables.py in migrations/versions/
- [ ] T008 [P] Implement event bus in src/app/core/events.py with sync and async dispatch
- [ ] T009 [P] Create audit decorator in src/app/core/audit.py for automatic logging
- [ ] T010 [P] Implement webhook signature generation in src/app/modules/webhooks/signatures.py
- [ ] T011 [P] Create email template loader in src/app/modules/notifications/templates.py
- [ ] T012 [P] Create feature flag helper functions in src/app/core/feature_flags.py
- [ ] T013 [P] Add ARQ task functions for async email delivery in src/app/modules/notifications/tasks.py
- [ ] T014 [P] Add ARQ task functions for async webhook delivery in src/app/modules/webhooks/tasks.py
- [ ] T015 [P] Verify PostgreSQL-only data storage compliance in src/app/core/db/ (Principle VII: No MongoDB or document stores used)
- [ ] T016 [P] Verify tenant middleware header resolution implementation in src/app/middleware/ (Principle VIII: X-Tenant-ID header exclusive resolution)
- [ ] T017 [P] Add queue size configuration for CRITICAL/NORMAL/LOW priorities in src/app/core/events.py (spec.md:FR-006a clarification)
- [ ] T018 [P] Add queue overflow handling strategy (drop/delay/priority) in src/app/core/events.py (spec.md:Edge Cases clarification)
- [ ] T019 [P] Add webhook signature verification in delivery endpoint logic (spec.md:FR-024 requirement)
- [ ] T020 [P] Clarify retry schedule as fixed intervals (1m, 5m, 15m, 30m, 1h) in src/app/modules/webhooks/tasks.py (plan.md:28 vs spec.md:FR-025 conflict)
- [ ] T021 [P] Implement default feature flags initialization on tenant creation in src/app/modules/tenants/service.py (spec.md:FR-030 requirement)
- [ ] T022 [P] Add tenant admin notification when webhook endpoint auto-disabled in src/app/modules/webhooks/service.py (spec.md:FR-026 requirement)
- [ ] T023 [P] Create email provider abstraction interface in src/app/core/email_provider.py (SMTP/SendGrid/AWS SES) (spec.md:FR-019 requirement)
- [ ] T024 [P] Verify ARQ task function registration/setup in src/app/core/queue/ (spec.md:Assumptions:6 validation)
- [ ] T025 [P] Add event metadata validation to ensure structure matches FR-005 (event type, timestamp, actor, tenant ID, payload) in src/app/core/events.py (spec.md:FR-005 requirement)
- [ ] T026 [P] Configure deduplication time window in NotificationService or environment settings (spec.md:FR-018 configurable time window requirement)
- [ ] T027 [P] Implement feature flag caching with invalidation strategy in FeatureFlagService (plan.md:31 mentions caching, spec.md:FR-033 <5ms)
- [ ] T028 [P] Add Jinja2 syntax validation for template overrides before insertion in NotificationService (data-model.md:678 column constraint)
- [ ] T029 [P] Add webhook delivery archival task (cleanup after 30 days) in src/app/modules/webhooks/tasks.py (spec.md:FR-027 requirement)
- [ ] T030 [P] Add frontend integration documentation for feature flags API in docs/user-guide/ (spec.md:FR-036 requirement)
- [ ] T031 [P] Clarify email delivery timing expectations (queue in 30s, deliver within 1-2 mins) between spec.md:NFR-002 (30s) and spec.md:SC-003 (1-2 mins)
- [ ] T032 [P] Add audit log retention enforcement or archival strategy (90 days minimum) in src/app/modules/audit_logs/service.py (spec.md:FR-012 requirement)
- [ ] T033 [P] Clarify feature flags permission model (super-admin vs tenant-admin) in implementation notes (contracts/feature-flags-api.yaml:460 vs contracts/notifications-api.yaml:522)
- [ ] T034 [P] Add notification retry scheduling in ARQ task function (verify T040 retry logic exists) (spec.md:FR-017 requirement validation)
- [ ] T035 [P] Expand audit decorator application scope to cover all write endpoints systematically or add verification task in tests (spec.md:FR-007 "all write operations", SC-001 "100% coverage")

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Automatic Activity Audit Trail (Priority: P1) 🎯 MVP

**Goal**: Automatically track all write operations across the system with immutable audit logs for security and compliance.

**Independent Test**: Perform any write operation (create user, update post, delete resource) and verify an audit log entry is created with correct actor, action, resource, and timestamp in the database.

### Tests for User Story 1 (OPTIONAL - only if tests requested) ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T015 [P] [US1] Contract test for audit logs isolation in tests/contract/test_audit_isolation.py
- [ ] T016 [P] [US1] Unit test for audit decorator in tests/unit/test_audit_service.py
- [ ] T017 [P] [US1] Integration test for audit log queries in tests/integration/test_audit_api.py

### Implementation for User Story 1

- [ ] T018 [US1] Create AuditLog model in src/app/modules/audit_logs/models.py
- [ ] T019 [P] [US1] Create Pydantic schemas for audit logs in src/app/modules/audit_logs/schemas.py
- [ ] T020 [P] [US1] Implement AuditLogRepository using FastCRUD in src/app/modules/audit_logs/repository.py
- [ ] T021 [US1] Implement AuditService in src/app/modules/audit_logs/service.py
- [ ] T022 [US1] Create audit logs router in src/app/modules/audit_logs/router.py
- [ ] T023 [US1] Register audit logs router in src/app/api/v1/__init__.py
- [ ] T024 [US1] Apply audit decorator to all write endpoints across modules (users, posts, etc.)
- [ ] T025 [US1] Add RBAC permissions for audit_logs.read and audit_logs.export
- [ ] T026 [US1] Implement export functionality for CSV/JSON format

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Transactional Email Notifications (Priority: P1)

**Goal**: Send transactional emails for key events with Jinja2 templates and async ARQ delivery with automatic retry on failures.

**Independent Test**: Trigger account creation or password reset and verify email delivery with correct content, personalization, and branding. Check notifications table for delivery status.

### Tests for User Story 2 (OPTIONAL - only if tests requested) ⚠️

- [ ] T027 [P] [US2] Contract test for email deduplication in tests/contract/test_email_deduplication.py
- [ ] T028 [P] [US2] Unit test for email template rendering in tests/unit/test_notification_service.py
- [ ] T029 [P] [US2] Unit test for email deduplication logic in tests/unit/test_notification_service.py
- [ ] T030 [P] [US2] Integration test for email delivery in tests/integration/test_notifications_api.py

### Implementation for User Story 2

- [ ] T031 [P] [US2] Create Notification and EmailTemplateOverride models in src/app/modules/notifications/models.py
- [ ] T032 [P] [US2] Create Pydantic schemas for notifications in src/app/modules/notifications/schemas.py
- [ ] T033 [P] [US2] Implement NotificationRepository using FastCRUD in src/app/modules/notifications/repository.py
- [ ] T034 [US2] Implement NotificationService in src/app/modules/notifications/service.py with template rendering and deduplication
- [ ] T035 [US2] Create notifications router in src/app/modules/notifications/router.py
- [ ] T036 [US2] Register notifications router in src/app/api/v1/__init__.py
- [ ] T037 [US2] Create default Jinja2 templates in src/app/templates/email/ (base, welcome, password_reset, invitation, notification)
- [ ] T038 [US2] Implement email preview endpoint for template validation
- [ ] T039 [US2] Add RBAC permissions for notifications.send and notifications.read
- [ ] T040 [US2] Implement retry notification endpoint for manual retry

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase 5: User Story 3 - Webhook Event Subscriptions (Priority: P2)

**Goal**: Configure webhook endpoints to receive real-time notifications with HMAC-SHA256 signatures and automatic retry logic.

**Independent Test**: Configure a webhook endpoint for "user.created" events, create a test user, and verify the webhook receives a POST request with event data in JSON format and HMAC signature.

### Tests for User Story 3 (OPTIONAL - only if tests requested) ⚠️

- [ ] T041 [P] [US3] Contract test for webhook signature verification in tests/contract/test_webhook_security.py
- [ ] T042 [P] [US3] Unit test for webhook retry logic in tests/unit/test_webhook_service.py
- [ ] T043 [P] [US3] Unit test for HMAC signature generation in tests/unit/test_webhook_signatures.py
- [ ] T044 [P] [US3] Integration test for webhook delivery with retries in tests/integration/test_webhook_delivery.py

### Implementation for User Story 3

- [ ] T045 [P] [US3] Create WebhookEndpoint and WebhookDelivery models in src/app/modules/webhooks/models.py
- [ ] T046 [P] [US3] Create Pydantic schemas for webhooks in src/app/modules/webhooks/schemas.py
- [ ] T047 [P] [US3] Implement WebhookRepository using FastCRUD in src/app/modules/webhooks/repository.py
- [ ] T048 [US3] Implement WebhookService in src/app/modules/webhooks/service.py with retry logic and endpoint management
- [ ] T049 [US3] Create webhooks router in src/app/modules/webhooks/router.py
- [ ] T050 [US3] Register webhooks router in src/app/api/v1/__init__.py
- [ ] T051 [US3] Implement test webhook endpoint for delivery validation
- [ ] T052 [US3] Implement automatic endpoint disable on 10 consecutive failures
- [ ] T053 [US3] Add RBAC permissions for webhooks.read and webhooks.write

**Checkpoint**: All user stories should now be independently functional

---

## Phase 6: User Story 4 - Tenant-Level Feature Flags (Priority: P2)

**Goal**: Toggle specific features on or off for organizations with JSONB storage for fast lookup without database queries.

**Independent Test**: Toggle a feature flag for a tenant and verify users in that tenant see/don't see feature accordingly. Check that changes take effect immediately without application restart.

### Tests for User Story 4 (OPTIONAL - only if tests requested) ⚠️

- [ ] T054 [P] [US4] Contract test for feature flag isolation in tests/contract/test_feature_flags.py
- [ ] T055 [P] [US4] Unit test for feature flag lookups in tests/unit/test_feature_flags.py
- [ ] T056 [P] [US4] Unit test for feature flag caching in tests/unit/test_feature_flags.py
- [ ] T057 [P] [US4] Integration test for feature flag updates in tests/integration/test_feature_flags_api.py

### Implementation for User Story 4

- [ ] T058 [US4] Add feature_flags JSONB column to Tenant model in src/app/modules/tenants/models.py
- [ ] T059 [P] [US4] Create Pydantic schemas for feature flags in src/app/modules/feature_flags/schemas.py
- [ ] T060 [US4] Implement FeatureFlagService in src/app/modules/feature_flags/service.py with caching
- [ ] T061 [US4] Create feature flags router in src/app/modules/feature_flags/router.py
- [ ] T062 [US4] Register feature flags router in src/app/api/v1/__init__.py
- [ ] T063 [US4] Implement bulk update endpoint for multiple flags
- [ ] T064 [US4] Add RBAC permissions for feature_flags.read and feature_flags.write (admin only)
- [ ] T065 [US4] Return 403 Forbidden with descriptive message for disabled features

**Checkpoint**: All user stories should now be independently functional

---

## Phase 7: User Story 5 - Event-Driven Module Decoupling (Priority: P3)

**Goal**: Central event bus that allows modules to publish and subscribe to events for loose coupling and independent feature development.

**Independent Test**: Publish a custom event and verify all registered subscribers (audit log, email notifications, webhooks) receive the event with correct data.

### Tests for User Story 5 (OPTIONAL - only if tests requested) ⚠️

- [ ] T066 [P] [US5] Unit test for event bus dispatch in tests/unit/test_event_bus.py
- [ ] T067 [P] [US5] Unit test for subscriber failure isolation in tests/unit/test_event_bus.py
- [ ] T068 [P] [US5] Unit test for event bus priority queuing in tests/unit/test_event_bus.py
- [ ] T069 [P] [US5] Integration test for event publishing in tests/integration/test_event_bus.py

### Implementation for User Story 5

- [ ] T070 [US5] Integrate event bus with AuditService in src/app/modules/audit_logs/service.py
- [ ] T071 [US5] Integrate event bus with NotificationService in src/app/modules/notifications/service.py
- [ ] T072 [US5] Integrate event bus with WebhookService in src/app/modules/webhooks/service.py
- [ ] T073 [US5] Implement priority queuing for CRITICAL/NORMAL/LOW events
- [ ] T074 [US5] Ensure subscriber failure isolation (one failing subscriber doesn't prevent others)
- [ ] T075 [US5] Add event masking for sensitive fields (passwords, tokens) in logs

**Checkpoint**: All user stories should now be independently functional

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T076 [P] Create base Jinja2 template in src/app/templates/email/base.html.jinja2
- [ ] T077 [P] Add relationships to Tenant model for audit_logs, notifications, webhook_endpoints, email_template_overrides in src/app/modules/tenants/models.py
- [ ] T078 [P] Publish event on tenant creation, deletion, and configuration changes from Tenant module
- [ ] T079 [P] Update API documentation for all new endpoints
- [ ] T080 [P] Implement rate limiting for notifications (1000 emails/hour, 100 emails/minute)
- [ ] T081 [P] Implement rate limiting for webhook test endpoint (5 requests per hour)
- [ ] T082 [P] Add logging for all ARQ task execution
- [ ] T083 [P] Validate quickstart.md for developer onboarding
- [ ] T084 [P] Add environment variable validation in src/app/core/config.py
- [ ] T085 [P] Create API documentation summary in docs/user-guide/core-services.md
- [ ] T086 [P] Run migration rollback test to ensure reversibility

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-7)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2 → P3)
- **Polish (Phase 8)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P1)**: Can start after Foundational (Phase 2) - May integrate with US1 but should be independently testable
- **User Story 3 (P2)**: Can start after Foundational (Phase 2) - May integrate with US1/US2 but should be independently testable
- **User Story 4 (P2)**: Can start after Foundational (Phase 2) - May integrate with US1/US2/US3 but should be independently testable
- **User Story 5 (P3)**: Can start after Foundational (Phase 2) - Integrates with all other stories

### Within Each User Story

- Tests (if included) MUST be written and FAIL before implementation
- Models before services
- Services before endpoints
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel (within Phase 2)
- Once Foundational phase completes, all user stories can start in parallel (if team capacity allows)
- All tests for a user story marked [P] can run in parallel
- Models within a story marked [P] can run in parallel
- Different user stories can be worked on in parallel by different team members

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together (if tests requested):
Task: "Contract test for audit logs isolation in tests/contract/test_audit_isolation.py"
Task: "Unit test for audit decorator in tests/unit/test_audit_service.py"
Task: "Integration test for audit log queries in tests/integration/test_audit_api.py"

# Launch all models for User Story 1 together:
Task: "Create AuditLog model in src/app/modules/audit_logs/models.py"
Task: "Create Pydantic schemas for audit logs in src/app/modules/audit_logs/schemas.py"

# Launch services and endpoints:
Task: "Implement AuditLogRepository using FastCRUD in src/app/modules/audit_logs/repository.py"
Task: "Implement AuditService in src/app/modules/audit_logs/service.py"
Task: "Create audit logs router in src/app/modules/audit_logs/router.py"
Task: "Register audit logs router in src/app/api/v1/__init__.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP AND VALIDATE**: Test User Story 1 independently
5. Deploy/demo if ready

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Deploy/Demo (MVP!)
3. Add User Story 2 → Test independently → Deploy/Demo
4. Add User Story 3 → Test independently → Deploy/Demo
5. Add User Story 4 → Test independently → Deploy/Demo
6. Add User Story 5 → Test independently → Deploy/Demo
7. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1
   - Developer B: User Story 2
   - Developer C: User Story 3
   - Developer D: User Story 4
   - Developer E: User Story 5
3. Stories complete and integrate independently

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
