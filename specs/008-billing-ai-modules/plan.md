# Implementation Plan: Billing & Payments and AI Features Modules

**Branch**: `008-billing-ai-modules` | **Date**: 2026-01-08 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/008-billing-ai-modules/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Implement Phase 3 (Billing & Payments) and Phase 5 (AI Features) modules for the FastAPI SaaS boilerplate. Billing module provides Stripe integration for payment processing, subscription plan management, product pricing, and billing records. AI module provides chat session management, message handling with token tracking, and user AI preferences. Both modules follow the `_base` modular pattern (BaseService, BaseCreate, BaseRead, BaseUpdate) and integrate with existing multi-tenant architecture via row-level `tenant_id` isolation.

## Technical Context

**Language/Version**: Python 3.11-3.12
**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2, Stripe SDK, httpx (for Ollama API)
**Storage**: PostgreSQL with JSONB for flexible fields (plan features, AI settings)
**Testing**: pytest with pytest-asyncio, minimum 80% coverage
**Target Platform**: Linux server (Docker containerized)
**Project Type**: web (backend API)
**Performance Goals**: Checkout <30s for 95% of transactions, chat response <5s, billing history <2s
**Constraints**: 100 concurrent AI sessions per tenant, webhook processing 99% success on first attempt
**Scale/Scope**: Multi-tenant SaaS, 9 new database tables, 2 new modules (billing, ai)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Compliance Notes |
|-----------|--------|------------------|
| I. Async-First Architecture | ✅ PASS | All endpoints, Stripe API calls (via HTTPXClient), Ollama API calls (via AsyncClient) use async/await |
| II. Strict Type Safety | ✅ PASS | All functions typed, Pydantic v2 for request/response validation, OpenAPI contracts defined |
| III. Test-Driven Quality | ✅ PASS | TDD approach, 80% coverage target, AAA pattern for tests, test directories defined |
| IV. Modular Service Architecture | ✅ PASS | New modules at `src/app/modules/billing/` and `src/app/modules/ai/` following `_base` patterns |
| V. Security by Default | ✅ PASS | Stripe secrets via env vars (`SECRET_STRIPE_*`), webhook signature validation, no credential logging |
| VI. PostgreSQL-Only | ✅ PASS | All 9 tables in PostgreSQL, JSONB for features/settings, no NoSQL dependencies |
| VII. Multi-Tenancy Row-Level | ✅ PASS | All tables include `tenant_id` FK, CRUD filters by tenant, data-model.md confirms isolation |
| VIII. Tenant Resolution Headers | ✅ PASS | Uses `X-Tenant-ID` header via existing middleware/DI pattern per API contracts |
| IX. Documentation Standards | ✅ PASS | Google-style docstrings, OpenAPI specs in contracts/, quickstart.md complete |

**Gate Result**: ✅ PASS - Post-design re-check complete. All principles satisfied. Ready for `/speckit.tasks`.

## Project Structure

### Documentation (this feature)

```text
specs/008-billing-ai-modules/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── billing-api.yaml # OpenAPI spec for billing endpoints
│   └── ai-api.yaml      # OpenAPI spec for AI endpoints
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/app/modules/
├── billing/                    # NEW: Billing & Payments module
│   ├── __init__.py
│   ├── models.py              # StripeSettings, BillingRecord, SubscriptionPlan, Product, Pricing, UserSubscription
│   ├── schemas.py             # Pydantic v2 request/response models
│   ├── crud.py                # Database operations (inherits _base patterns)
│   ├── service.py             # Business logic (Stripe integration, checkout, webhooks)
│   ├── router.py              # API endpoints
│   └── AGENTS.md              # Module-specific agent context
│
├── ai/                        # NEW: AI Features module
│   ├── __init__.py
│   ├── models.py              # AIChatSession, AIChatMessage, AISettings
│   ├── schemas.py             # Pydantic v2 request/response models
│   ├── crud.py                # Database operations (inherits _base patterns)
│   ├── service.py             # Business logic (Ollama integration, token management)
│   ├── router.py              # API endpoints
│   └── AGENTS.md              # Module-specific agent context
│
└── _base/                     # Existing: Base patterns for inheritance
    ├── service.py             # BaseService with standard CRUD
    ├── schemas.py             # BaseCreate, BaseRead, BaseUpdate
    └── README.md              # Documentation

tests/
├── unit/
│   ├── billing/               # NEW: Unit tests for billing module
│   └── ai/                    # NEW: Unit tests for AI module
├── integration/
│   ├── billing/               # NEW: Integration tests for billing API
│   └── ai/                    # NEW: Integration tests for AI API
└── contract/
    └── stripe/                # NEW: Contract tests for Stripe webhooks
```

**Structure Decision**: Backend API module structure following existing `src/app/modules/{feature}/` pattern. Two new modules (`billing`, `ai`) inherit from `_base` patterns. Tests organized by type (unit/integration/contract) with feature subdirectories.

## Complexity Tracking

> **No violations detected in Constitution Check.** All principles satisfied. No complexity justifications required.
