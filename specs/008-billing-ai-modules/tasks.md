# Tasks: Billing & Payments and AI Features Modules

**Input**: Design documents from `/specs/008-billing-ai-modules/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/, research.md, quickstart.md

**Tests**: Required per Constitution Principle III (Test-Driven Quality). TDD approach with tests BEFORE implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

Based on plan.md structure:
- **Modules**: `src/app/modules/billing/`, `src/app/modules/ai/`
- **Tests**: `tests/unit/billing/`, `tests/unit/ai/`, `tests/integration/billing/`, `tests/integration/ai/`
- **Base patterns**: `src/app/modules/_base/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, dependencies, and module structure

- [x] T001 Add Stripe SDK dependency (`stripe>=8.0.0`) to requirements.txt
- [x] T002 [P] Add Ollama SDK dependency (`ollama>=0.3.0`) to requirements.txt
- [x] T003 [P] Add encryption dependency (`cryptography`) for Fernet encryption
- [x] T004 Create billing module directory structure at src/app/modules/billing/
- [x] T005 [P] Create AI module directory structure at src/app/modules/ai/
- [x] T006 [P] Create billing __init__.py at src/app/modules/billing/__init__.py
- [x] T007 [P] Create AI __init__.py at src/app/modules/ai/__init__.py
- [x] T008 Add environment variables for Stripe (ENCRYPTION_KEY) to .env.example
- [x] T009 [P] Add environment variables for Ollama (OLLAMA_HOST, SECRET_OLLAMA_API_KEY) to .env.example
- [x] T010 Create Fernet encryption utility in src/app/core/security/encryption.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**CRITICAL**: No user story work can begin until this phase is complete

### Database Migrations

- [x] T011 Create Alembic migration for stripe_settings table
- [x] T012 [P] Create Alembic migration for subscription_plans table
- [x] T013 [P] Create Alembic migration for products table
- [x] T014 Create Alembic migration for pricing table (depends on products)
- [x] T015 Create Alembic migration for user_subscriptions table (depends on subscription_plans)
- [x] T016 Create Alembic migration for billing_records table (depends on user_subscriptions)
- [x] T017 [P] Create Alembic migration for ai_chat_sessions table
- [x] T018 Create Alembic migration for ai_chat_messages table (depends on ai_chat_sessions)
- [x] T019 [P] Create Alembic migration for ai_settings table

### Base Models

- [x] T020 Create StripeSettings model in src/app/modules/billing/models.py
- [x] T021 [P] Create SubscriptionPlan model in src/app/modules/billing/models.py
- [x] T022 [P] Create Product model in src/app/modules/billing/models.py
- [x] T023 Create Pricing model in src/app/modules/billing/models.py (depends on Product)
- [x] T024 Create UserSubscription model in src/app/modules/billing/models.py (depends on SubscriptionPlan)
- [x] T025 Create BillingRecord model in src/app/modules/billing/models.py (depends on UserSubscription)
- [x] T026 [P] Create AIChatSession model in src/app/modules/ai/models.py
- [x] T027 Create AIChatMessage model in src/app/modules/ai/models.py (depends on AIChatSession)
- [x] T028 [P] Create AISettings model in src/app/modules/ai/models.py

### Base Schemas

- [x] T029 Create billing Pydantic schemas in src/app/modules/billing/schemas.py (all billing schemas)
- [x] T030 [P] Create AI Pydantic schemas in src/app/modules/ai/schemas.py (all AI schemas)

### Route Registration

- [x] T031 Register billing router in src/app/core/router.py
- [x] T032 [P] Register AI router in src/app/core/router.py

### Tenant/User Model Updates

- [x] T033 Add billing relationships to Tenant model (stripe_settings, subscription_plans, products)
- [x] T034 [P] Add AI relationships to Tenant model (ai_chat_sessions)
- [x] T035 Add billing relationships to User model (user_subscriptions, billing_records)
- [x] T036 [P] Add AI relationships to User model (ai_chat_sessions, ai_settings)

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Tenant Administrator Configures Stripe Integration (Priority: P1) - MVP

**Goal**: Enable tenant administrators to connect their Stripe account for payment processing

**Independent Test**: Configure Stripe credentials and verify webhook connectivity

### Tests for User Story 1 (TDD - Write First, Must FAIL)

- [x] T037 [P] [US1] Unit test for StripeSettingsCRUD in tests/unit/billing/test_crud_stripe_settings.py
- [x] T038 [P] [US1] Unit test for StripeSettingsService in tests/unit/billing/test_stripe_settings_service.py
- [x] T039 [P] [US1] Unit test for webhook signature verification in tests/unit/billing/test_webhook.py
- [x] T040 [P] [US1] Integration test for Stripe settings endpoints in tests/integration/billing/test_stripe_settings_api.py
- [x] T041 [P] [US1] Contract test for Stripe webhook events in tests/contract/stripe/test_webhooks.py

### Implementation for User Story 1

- [x] T042 [US1] Create StripeSettingsCRUD in src/app/modules/billing/crud.py
- [x] T043 [US1] Create StripeSettingsService with encryption in src/app/modules/billing/service.py
- [x] T044 [US1] Implement GET /billing/stripe-settings endpoint in src/app/modules/billing/router.py
- [x] T045 [US1] Implement PUT /billing/stripe-settings endpoint in src/app/modules/billing/router.py
- [x] T046 [US1] Implement MANAGE_BILLING permission check for Stripe settings endpoints
- [x] T047 [US1] Create webhook signature verification utility in src/app/modules/billing/webhook.py
- [x] T048 [US1] Implement POST /billing/webhooks/stripe endpoint in src/app/modules/billing/router.py
- [x] T049 [US1] Implement webhook event routing (checkout.session.completed, invoice.*, subscription.*) in src/app/modules/billing/webhook.py
- [x] T050 [US1] Add webhook idempotency tracking (store processed event IDs) in src/app/modules/billing/router.py
- [x] T051 [US1] Add background task processing for webhooks in src/app/modules/billing/router.py

**Checkpoint**: Stripe integration configurable and webhooks functional

---

## Phase 4: User Story 2 - Admin Creates and Manages Subscription Plans (Priority: P1) - MVP

**Goal**: Allow tenant administrators to create subscription plans with pricing tiers and features

**Independent Test**: Create, update, and deactivate subscription plans

### Tests for User Story 2 (TDD - Write First, Must FAIL)

- [ ] T052 [P] [US2] Unit test for SubscriptionPlanCRUD in tests/unit/billing/test_subscription_plan_crud.py
- [x] T053 [P] [US2] Unit test for SubscriptionPlanService in tests/unit/billing/test_subscription_plan_service.py
- [x] T054 [P] [US2] Integration test for subscription plan endpoints in tests/integration/billing/test_subscription_plans_api.py

### Implementation for User Story 2

- [x] T055 [US2] Create SubscriptionPlanCRUD in src/app/modules/billing/crud.py
- [x] T056 [US2] Create SubscriptionPlanService in src/app/modules/billing/service.py
- [x] T057 [US2] Implement GET /billing/plans endpoint (list) in src/app/modules/billing/router.py
- [x] T058 [US2] Implement POST /billing/plans endpoint (create) in src/app/modules/billing/router.py
- [x] T059 [US2] Implement GET /billing/plans/{plan_id} endpoint in src/app/modules/billing/router.py
- [x] T060 [US2] Implement PATCH /billing/plans/{plan_id} endpoint in src/app/modules/billing/router.py
- [x] T061 [US2] Implement POST /billing/plans/{plan_id}/deactivate endpoint in src/app/modules/billing/router.py
- [x] T062 [US2] Add MANAGE_PRODUCTS_AND_PRICING permission check for plan management endpoints
- [x] T063 [US2] Add sort ordering logic for plan display in src/app/modules/billing/service.py

**Checkpoint**: Subscription plan management fully functional

---

## Phase 5: User Story 3 - Customer Completes Checkout and Subscribes (Priority: P1) - MVP

**Goal**: Enable customers to select a plan and complete the payment checkout flow

**Independent Test**: Select plan, complete checkout, verify active subscription

### Tests for User Story 3 (TDD - Write First, Must FAIL)

- [ ] T064 [P] [US3] Unit test for UserSubscriptionCRUD in tests/unit/billing/test_user_subscription_crud.py
- [ ] T065 [P] [US3] Unit test for BillingRecordCRUD in tests/unit/billing/test_billing_record_crud.py
- [ ] T066 [P] [US3] Unit test for CheckoutService in tests/unit/billing/test_checkout_service.py
- [ ] T067 [P] [US3] Integration test for checkout endpoints in tests/integration/billing/test_checkout_api.py
- [ ] T068 [P] [US3] Contract test for Stripe checkout session in tests/contract/stripe/test_checkout_session.py

### Implementation for User Story 3

- [x] T069 [US3] Create UserSubscriptionCRUD in src/app/modules/billing/crud.py
- [x] T070 [US3] Create BillingRecordCRUD in src/app/modules/billing/crud.py
- [ ] T071 [US3] Create CheckoutService with Stripe integration in src/app/modules/billing/service.py
- [ ] T072 [US3] Implement POST /billing/checkout endpoint in src/app/modules/billing/router.py
- [x] T073 [US3] Implement GET /billing/subscriptions/me endpoint in src/app/modules/billing/router.py
- [ ] T074 [US3] Implement checkout.session.completed webhook handler in src/app/modules/billing/webhook.py
- [ ] T075 [US3] Implement invoice.payment_succeeded webhook handler in src/app/modules/billing/webhook.py
- [ ] T076 [US3] Implement invoice.payment_failed webhook handler in src/app/modules/billing/webhook.py
- [ ] T077 [US3] Implement charge.refunded webhook handler in src/app/modules/billing/webhook.py
- [ ] T078 [US3] Implement subscription status update logic (active, past_due, expired) in src/app/modules/billing/service.py
- [ ] T079 [US3] Add HTTP 402 PaymentError response handling in src/app/modules/billing/router.py
- [ ] T080 [US3] Add checkout session expiry (24 hours) in src/app/modules/billing/service.py

**Checkpoint**: Complete checkout flow with subscription activation

---

## Phase 6: User Story 7 - User Creates AI Chat Session (Priority: P1) - MVP

**Goal**: Enable users to start AI conversations and receive AI-generated responses

**Independent Test**: Create session, send message, receive AI response

### Tests for User Story 7 (TDD - Write First, Must FAIL)

- [ ] T081 [P] [US7] Unit test for AIChatSessionCRUD in tests/unit/ai/test_chat_session_crud.py
- [ ] T082 [P] [US7] Unit test for AIChatMessageCRUD in tests/unit/ai/test_chat_message_crud.py
- [x] T083 [P] [US7] Unit test for OllamaService in tests/unit/ai/test_ollama_service.py
- [x] T084 [P] [US7] Unit test for ChatService in tests/unit/ai/test_chat_service.py
- [x] T085 [P] [US7] Integration test for AI session endpoints in tests/integration/ai/test_chat_session_api.py
- [ ] T086 [P] [US7] Unit test for rate limiting middleware in tests/unit/ai/test_rate_limiting.py

### Implementation for User Story 7

- [ ] T087 [US7] Create rate limiting infrastructure in src/app/modules/ai/rate_limiter.py
- [x] T088 [US7] Create AIChatSessionCRUD in src/app/modules/ai/crud.py
- [x] T089 [US7] Create AIChatMessageCRUD in src/app/modules/ai/crud.py
- [x] T090 [US7] Create OllamaService with AsyncClient in src/app/modules/ai/ollama_service.py
- [x] T091 [US7] Create ChatService in src/app/modules/ai/service.py
- [x] T092 [US7] Implement POST /ai/sessions endpoint (create session) in src/app/modules/ai/router.py
- [x] T093 [US7] Implement GET /ai/sessions/{session_id} endpoint in src/app/modules/ai/router.py
- [x] T094 [US7] Implement POST /ai/sessions/{session_id}/messages endpoint in src/app/modules/ai/router.py
- [x] T095 [US7] Implement atomic message saving (user + assistant together) in src/app/modules/ai/service.py
- [ ] T096 [US7] Implement token estimation (4 chars per token) in src/app/modules/ai/service.py
- [ ] T097 [US7] Implement context window truncation (FIFO, preserve system prompt) in src/app/modules/ai/service.py
- [x] T098 [US7] Add HTTP 503 AIServiceError response handling in src/app/modules/ai/router.py
- [ ] T099 [US7] Add rate limiting middleware (60 requests/user/minute) in src/app/modules/ai/middleware.py
- [ ] T100 [US7] Add HTTP 429 RateLimitError response with Retry-After header in src/app/modules/ai/router.py

**Checkpoint**: AI chat with message send/receive functional

---

## Phase 7: User Story 4 - Customer Views Billing History (Priority: P2)

**Goal**: Enable customers to view their billing history and transaction records

**Independent Test**: View paginated billing history with transaction details

### Tests for User Story 4 (TDD - Write First, Must FAIL)

- [ ] T101 [P] [US4] Unit test for BillingHistoryService in tests/unit/billing/test_billing_history_service.py
- [ ] T102 [P] [US4] Integration test for billing history endpoint in tests/integration/billing/test_billing_history_api.py

### Implementation for User Story 4

- [ ] T103 [US4] Create BillingHistoryService in src/app/modules/billing/service.py
- [x] T104 [US4] Implement GET /billing/history endpoint with pagination in src/app/modules/billing/router.py
- [ ] T105 [US4] Add billing record status filtering in src/app/modules/billing/crud.py

**Checkpoint**: Billing history viewable with pagination

---

## Phase 8: User Story 5 - Customer Cancels Subscription (Priority: P2)

**Goal**: Enable customers to cancel their subscription with end-of-period access

**Independent Test**: Initiate cancellation, verify subscription status change, confirm access until period end

### Tests for User Story 5 (TDD - Write First, Must FAIL)

- [ ] T106 [P] [US5] Unit test for CancellationService in tests/unit/billing/test_cancellation_service.py
- [ ] T107 [P] [US5] Unit test for subscription lifecycle transitions in tests/unit/billing/test_subscription_lifecycle.py
- [ ] T108 [P] [US5] Integration test for cancellation endpoint in tests/integration/billing/test_cancellation_api.py

### Implementation for User Story 5

- [ ] T109 [US5] Create CancellationService in src/app/modules/billing/service.py
- [x] T110 [US5] Implement POST /billing/subscriptions/me/cancel endpoint in src/app/modules/billing/router.py
- [ ] T111 [US5] Implement customer.subscription.updated webhook handler in src/app/modules/billing/webhook.py
- [ ] T112 [US5] Implement customer.subscription.deleted webhook handler in src/app/modules/billing/webhook.py
- [ ] T113 [US5] Add subscription lifecycle state transitions (active→canceled, canceled→expired) in src/app/modules/billing/service.py

**Checkpoint**: Subscription cancellation flow complete

---

## Phase 9: User Story 6 - Admin Creates Products with Pricing Tiers (Priority: P2)

**Goal**: Enable administrators to create products with multiple pricing tiers

**Independent Test**: Create product, add pricing tiers, view product with pricing

### Tests for User Story 6 (TDD - Write First, Must FAIL)

- [ ] T114 [P] [US6] Unit test for ProductCRUD in tests/unit/billing/test_product_crud.py
- [ ] T115 [P] [US6] Unit test for PricingCRUD in tests/unit/billing/test_pricing_crud.py
- [ ] T116 [P] [US6] Unit test for ProductService in tests/unit/billing/test_product_service.py
- [ ] T117 [P] [US6] Integration test for product endpoints in tests/integration/billing/test_products_api.py

### Implementation for User Story 6

- [x] T118 [US6] Create ProductCRUD in src/app/modules/billing/crud.py
- [x] T119 [US6] Create PricingCRUD in src/app/modules/billing/crud.py
- [ ] T120 [US6] Create ProductService in src/app/modules/billing/service.py
- [ ] T121 [US6] Implement GET /billing/products endpoint in src/app/modules/billing/router.py
- [ ] T122 [US6] Implement POST /billing/products endpoint in src/app/modules/billing/router.py
- [ ] T123 [US6] Implement GET /billing/products/{product_id} endpoint in src/app/modules/billing/router.py
- [ ] T124 [US6] Implement PATCH /billing/products/{product_id} endpoint in src/app/modules/billing/router.py
- [ ] T125 [US6] Implement POST /billing/products/{product_id}/pricing endpoint in src/app/modules/billing/router.py

**Checkpoint**: Product and pricing management complete

---

## Phase 10: User Story 8 - User Manages Multiple AI Chat Sessions (Priority: P2)

**Goal**: Enable users to organize and access multiple AI conversations

**Independent Test**: List sessions, view session history, delete session

### Tests for User Story 8 (TDD - Write First, Must FAIL)

- [ ] T126 [P] [US8] Unit test for session list/pagination in tests/unit/ai/test_session_management.py
- [ ] T127 [P] [US8] Unit test for session deletion with cascade in tests/unit/ai/test_session_delete.py
- [ ] T128 [P] [US8] Integration test for session management endpoints in tests/integration/ai/test_session_management_api.py

### Implementation for User Story 8

- [x] T129 [US8] Implement GET /ai/sessions endpoint (list with pagination) in src/app/modules/ai/router.py
- [x] T130 [US8] Implement PATCH /ai/sessions/{session_id} endpoint (update title) in src/app/modules/ai/router.py
- [x] T131 [US8] Implement DELETE /ai/sessions/{session_id} endpoint in src/app/modules/ai/router.py
- [x] T132 [US8] Implement GET /ai/sessions/{session_id}/messages endpoint (paginated history) in src/app/modules/ai/router.py
- [ ] T133 [US8] Add cascade delete for session messages in src/app/modules/ai/crud.py
- [ ] T134 [US8] Add concurrent session limit (100 per tenant) in src/app/modules/ai/middleware.py

**Checkpoint**: Multi-session management complete

---

## Phase 11: User Story 9 - User Configures AI Preferences (Priority: P3)

**Goal**: Enable users to customize their AI experience with model and parameter preferences

**Independent Test**: View settings, modify preferences, verify settings applied to new sessions

### Tests for User Story 9 (TDD - Write First, Must FAIL)

- [ ] T135 [P] [US9] Unit test for AISettingsCRUD in tests/unit/ai/test_ai_settings_crud.py
- [ ] T136 [P] [US9] Unit test for AISettingsService in tests/unit/ai/test_ai_settings_service.py
- [x] T137 [P] [US9] Integration test for AI settings endpoints in tests/integration/ai/test_ai_settings_api.py

### Implementation for User Story 9

- [x] T138 [US9] Create AISettingsCRUD in src/app/modules/ai/crud.py
- [x] T139 [US9] Create AISettingsService in src/app/modules/ai/service.py
- [x] T140 [US9] Implement GET /ai/settings endpoint in src/app/modules/ai/router.py
- [x] T141 [US9] Implement PUT /ai/settings endpoint in src/app/modules/ai/router.py
- [ ] T142 [US9] Implement GET /ai/models endpoint (dynamic from Ollama) in src/app/modules/ai/router.py
- [x] T143 [US9] Add default AI settings (model=gemma3, temperature=0.7, max_tokens=2048) in src/app/modules/ai/service.py
- [ ] T144 [US9] Integrate user AI settings with chat session creation in src/app/modules/ai/service.py

**Checkpoint**: AI preferences fully configurable

---

## Phase 12: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T145 [P] Create billing module AGENTS.md at src/app/modules/billing/AGENTS.md
- [ ] T146 [P] Create AI module AGENTS.md at src/app/modules/ai/AGENTS.md
- [ ] T147 [P] Add logging for billing operations in src/app/modules/billing/service.py
- [ ] T148 [P] Add logging for AI operations in src/app/modules/ai/service.py
- [ ] T149 [P] Run quickstart.md validation for billing module
- [ ] T150 [P] Run quickstart.md validation for AI module
- [ ] T151 Security review: verify no credentials in logs or API responses
- [ ] T152 [P] Performance review: verify <30s checkout, <5s AI response, <2s billing history

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-11)**: All depend on Foundational phase completion
  - P1 stories (US1, US2, US3, US7) form the MVP
  - P2 stories (US4, US5, US6, US8) add secondary functionality
  - P3 story (US9) adds preference customization
- **Polish (Phase 12)**: Depends on all desired user stories being complete

### User Story Dependencies

| Story | Priority | Dependencies | Module |
|-------|----------|--------------|--------|
| US1 - Stripe Integration | P1 | Foundational only | Billing |
| US2 - Subscription Plans | P1 | Foundational only | Billing |
| US3 - Checkout & Subscribe | P1 | US1 (Stripe), US2 (Plans) | Billing |
| US4 - Billing History | P2 | US3 (creates billing records) | Billing |
| US5 - Cancel Subscription | P2 | US3 (creates subscriptions) | Billing |
| US6 - Products & Pricing | P2 | Foundational only | Billing |
| US7 - AI Chat Session | P1 | Foundational only | AI |
| US8 - Manage Sessions | P2 | US7 (creates sessions) | AI |
| US9 - AI Preferences | P3 | US7 (uses settings in chat) | AI |

### Within Each User Story

- Models before services
- Services before endpoints
- CRUD before business logic
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational migration tasks for independent tables can run in parallel
- Once Foundational phase completes:
  - US1 and US2 can run in parallel (different functionality)
  - US7 can run in parallel with billing stories (different module)
  - US6 is independent of US1-US5 and can run in parallel

---

## Parallel Example: Foundational Phase

```bash
# Launch all independent migrations together:
Task: "Create Alembic migration for stripe_settings table"
Task: "Create Alembic migration for subscription_plans table"
Task: "Create Alembic migration for products table"
Task: "Create Alembic migration for ai_chat_sessions table"
Task: "Create Alembic migration for ai_settings table"

# After those complete, launch dependent migrations:
Task: "Create Alembic migration for pricing table"
Task: "Create Alembic migration for user_subscriptions table"
Task: "Create Alembic migration for ai_chat_messages table"
```

## Parallel Example: MVP Stories

```bash
# After Foundational, launch US1 + US2 + US7 in parallel:
Developer A: User Story 1 (Stripe Integration) - T037-T051 (tests + impl)
Developer B: User Story 2 (Subscription Plans) - T052-T063 (tests + impl)
Developer C: User Story 7 (AI Chat Session) - T081-T100 (tests + impl)

# After US1 + US2 complete, launch US3:
Developer A/B: User Story 3 (Checkout) - T064-T080 (tests + impl)
```

---

## Implementation Strategy

### MVP First (Phase 1-6 + Phase 7 = P1 Stories)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: US1 - Stripe Integration
4. Complete Phase 4: US2 - Subscription Plans
5. Complete Phase 5: US3 - Checkout & Subscribe
6. Complete Phase 6: US7 - AI Chat Session
7. **STOP and VALIDATE**: Test all P1 stories independently
8. Deploy/demo MVP if ready

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1 + US2 → Stripe + Plans configured → Partial billing MVP
3. Add US3 → Full checkout flow → Billing MVP complete
4. Add US7 → AI chat functional → AI MVP complete
5. Add US4, US5, US6 → Enhanced billing features
6. Add US8, US9 → Enhanced AI features
7. Polish → Production ready

### Parallel Team Strategy

With 3 developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US1 → US3 → US4 → US5 (Billing flow)
   - Developer B: US2 → US6 (Products/Plans)
   - Developer C: US7 → US8 → US9 (AI flow)
3. Stories complete and integrate independently

---

## Summary

| Metric | Value |
|--------|-------|
| **Total Tasks** | 152 |
| **Setup Tasks** | 10 (10 completed) |
| **Foundational Tasks** | 26 (26 completed) |
| **US1 (Stripe Integration)** | 15 (8 completed) |
| **US2 (Subscription Plans)** | 12 (8 completed) |
| **US3 (Checkout & Subscribe)** | 17 (3 completed) |
| **US4 (Billing History)** | 5 (1 completed) |
| **US5 (Cancel Subscription)** | 8 (1 completed) |
| **US6 (Products & Pricing)** | 12 (2 completed) |
| **US7 (AI Chat Session)** | 20 (12 completed) |
| **US8 (Manage Sessions)** | 9 (4 completed) |
| **US9 (AI Preferences)** | 10 (6 completed) |
| **Polish Tasks** | 8 (0 completed) |
| **Total Test Tasks** | 34 |
| **MVP Scope (P1 Stories)** | US1, US2, US3, US7 (64 story tasks + 36 setup/foundational) |
| **Parallel Opportunities** | 40+ tasks can run in parallel within phases |

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Billing and AI modules are independent - can develop in parallel
- All schemas follow `_base` module patterns (BaseCreate, BaseRead, BaseUpdate)
- All services inherit from BaseService pattern
