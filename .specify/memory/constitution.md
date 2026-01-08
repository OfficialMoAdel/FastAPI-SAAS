<!--
  ============================================================================
  SYNC IMPACT REPORT
  ============================================================================
  Version Change: 1.1.0 → 1.2.0 (MINOR - Quality and Documentation standards formalized)

  Modified Principles:
    - Principle III: Test-Driven Quality → Enhanced with 80% coverage minimum, TDD workflow, Arrange-Act-Assert pattern

  Added Sections:
    - Principle IX: Documentation Standards (Google docstrings, OpenAPI, WHY-focused comments)
    - External Documentation References subsection (reference project guidance)

  Removed Sections: None

  Templates Validation:
    ✅ .specify/templates/plan-template.md - "Constitution Check" section compatible with new testing/docs standards
    ✅ .specify/templates/spec-template.md - Requirements section aligns with documentation standards
    ✅ .specify/templates/tasks-template.md - TDD workflow phases (tests first) align with Principle III

  Follow-up TODOs: None
  ============================================================================
-->

# FastAPI SaaS Boilerplate Constitution

## Core Principles

### I. Async-First Architecture

All endpoints, database operations, and I/O-bound tasks MUST use `async/await` patterns.

**Rationale**: FastAPI's performance advantage comes from async execution. Blocking calls defeat this purpose and cause thread starvation under load.

**Requirements**:

- Use `async def` for all route handlers and database operations
- Use `asyncio.gather()` for concurrent I/O aggregation
- Use async HTTP clients (`httpx`, `aiohttp`) for external requests
- NEVER use blocking I/O in async contexts without `run_in_executor`

### II. Strict Type Safety

All function signatures MUST include type hints. Code MUST pass `mypy` strict mode.

**Rationale**: Type hints catch bugs at development time, improve IDE support, and serve as living documentation. Strict typing reduces runtime errors and maintenance burden.

**Requirements**:

- Type hints for ALL function parameters and return values
- Pydantic v2 models for ALL request/response validation
- No `Any` types without explicit justification
- CI/CD MUST fail on type errors (`uv run mypy src/`)

### III. Test-Driven Quality (NON-NEGOTIABLE)

All production code MUST have corresponding tests. Tests MUST pass before merge. Test coverage MUST be minimum 80%.

**Rationale**: Tests are the safety net that enables confident refactoring and prevents regressions. Untested code is a liability. TDD ensures tests drive implementation, not the reverse.

**Requirements**:

- **Test Coverage**: Minimum 80% code coverage required
- **TDD Approach**: Write tests first, then implement (red-green-refactor)
- **Test Structure**: Use Arrange-Act-Assert (AAA) pattern for all tests
- **Test Framework**: Pytest for all tests (`uv run pytest`)
- Unit tests for business logic in `tests/unit/`
- Integration tests for API endpoints in `tests/integration/`
- Contract tests for external interfaces in `tests/contract/`
- All tests MUST pass: `uv run pytest`
- Code review MUST verify test coverage for new features

### IV. Modular Service Architecture

Business logic MUST reside in Service classes. Modules MUST follow the standard structure at `src/app/modules/{feature_name}`.

**Rationale**: Service layer separation enables testability, reusability, and clear responsibility boundaries. Consistent structure reduces cognitive load.

**Requirements**:

- New features MUST be in `src/app/modules/{feature_name}/`
- Each module MUST contain: `router.py`, `service.py`, `models.py`, `schemas.py`, `crud.py`
- Route handlers MUST delegate to services (no business logic in routes)
- Services MUST be dependency-injectable
- Use absolute imports (`src.app.`) - NEVER relative imports

### V. Security by Default

Secrets MUST NEVER be committed. All sensitive data MUST use environment variables.

**Rationale**: Security breaches from leaked credentials are catastrophic and irreversible. Prevention is the only acceptable strategy.

**Requirements**:

- All secrets via environment variables with `SECRET_` prefix
- NEVER hardcode tokens, API keys, or passwords
- Use `.env.local` for local development (gitignored)
- JWT refresh token pattern with Redis token storage
- PII MUST use Pydantic validators for sanitization
- Pre-commit hooks MUST scan for credential patterns

### VI. PostgreSQL-Only Data Storage (NON-NEGOTIABLE)

All data MUST persist in PostgreSQL. NO MongoDB or other NoSQL databases.

**Rationale**: Single database technology simplifies operations, enables transactional consistency, and aligns with row-level multi-tenancy strategy.

**Requirements**:

- All data MUST use PostgreSQL with SQLAlchemy 2.0 async
- NO MongoDB, no document stores, no other NoSQL databases
- Use JSONB columns for flexible/semi-structured data needs
- Use Alembic for ALL schema migrations

### VII. Multi-Tenancy via Row-Level Isolation (NON-NEGOTIABLE)

Multi-tenant data isolation MUST use row-level isolation with `tenant_id` foreign key.

**Rationale**: Row-level isolation provides strong data separation while maintaining a single database, simplifying operations and reducing costs.

**Requirements**:

- All tenant-scoped tables MUST include `tenant_id` foreign key column
- CRUD operations MUST filter by `tenant_id` for tenant data
- The `Tenant` module manages tenant lifecycle exclusively
- Other modules link to tenants via `tenant_id` foreign key
- Existing tables (like Users) MUST be altered carefully - NEVER drop existing data

### VIII. Tenant Resolution via HTTP Headers (NON-NEGOTIABLE)

Tenant context MUST be resolved strictly via HTTP header `X-Tenant-ID`. NO subdomain routing.

**Rationale**: Header-based resolution is explicit, testable, and works consistently across all deployment environments without DNS complexity.

**Requirements**:

- Tenant resolution MUST use `X-Tenant-ID` HTTP header exclusively
- NO subdomain-based tenant routing
- Middleware validates the tenant header
- Services/CRUD consume tenant via Dependency Injection (`Depends`)
- Use explicit `Depends` injection for tenant context - never implicit resolution

### IX. Documentation Standards

All code MUST be documented following consistent standards for maintainability and API discoverability.

**Rationale**: Clear documentation reduces onboarding time, enables self-service API consumption, and captures institutional knowledge. Explaining WHY decisions were made is more valuable than describing WHAT the code does.

**Requirements**:

- **Docstrings**: Google style docstrings for all public functions, classes, and modules
- **API Docs**: Auto-generated from OpenAPI specification (FastAPI generates automatically)
- **Inline Comments**: MUST explain WHY, not WHAT (code should be self-documenting for WHAT)
- **Module README**: Each module in `src/app/modules/` SHOULD have a brief README explaining its purpose
- **Type Annotations**: Serve as primary documentation for function signatures (see Principle II)

## Architectural Patterns

### Clean Architecture & Domain-Driven Design

The codebase follows Clean Architecture principles with DDD tactical patterns.

**Explicit over Implicit**:

- Use Dependency Injection (`Depends`) to access Tenant Context
- All dependencies MUST be explicitly declared
- No global state or implicit context propagation

**Middleware Responsibility**:

- Middleware validates the Tenant header
- Middleware does NOT inject business logic
- Services and CRUD layers consume context via dependencies

**Separation of Concerns**:

- The `Tenant` module manages tenant lifecycle exclusively
- Other modules MUST NOT manage tenant state
- Each module owns its domain logic

## Technical Standards

**Code Quality Gates**:

- Linting: `uv run ruff check` MUST pass with zero errors
- Type checking: `uv run mypy src/` MUST pass with zero errors
- Testing: `uv run pytest` MUST pass with 100% success rate
- Test Coverage: `uv run pytest --cov=src --cov-fail-under=80`
- Formatting: ruff format applied consistently

**Code Style**:

- Use snake_case for Python identifiers
- Use explicit types on all function signatures
- Follow existing naming conventions in the codebase

**Database Standards**:

- SQLAlchemy 2.0 async ORM with asyncpg driver
- Alembic for all schema migrations
- Primary keys: `BIGINT GENERATED ALWAYS AS IDENTITY` or UUIDv7
- Timestamps: ALWAYS use `TIMESTAMPTZ`
- JSONB for flexible settings fields
- Row-Level Security (RLS) on tenant-scoped tables where appropriate

**Data Safety**:

- Existing tables MUST be altered carefully
- NEVER drop existing data during migrations
- Migrations MUST be reversible where possible
- Test migrations against production-like data before deployment

**API Standards**:

- FastAPI with declarative route definitions
- Pydantic v2 for all input/output validation
- Consistent error handling with `HTTPException`
- OpenAPI documentation auto-generated

## External Documentation References

The following external resources describe business logic patterns we adapt to our stack. Use as logical guides only - ignore implementation details that conflict with our PostgreSQL/SQLAlchemy stack.

**Reference Project**: `sajanv88/full_stack_fastapi_react_template`

Key topics:
- Clean Architecture Pattern Implementation
- Domain Layer Design
- Use Cases Layer Organization
- Multi-Tenancy System (adapt to header-based, not subdomain)
- JWT Token Management with Refresh Tokens
- Role-Based Access Control (RBAC)
- Repository Pattern Implementation

**Note**: Reference uses MongoDB/Beanie - adapt all patterns to PostgreSQL/SQLAlchemy 2.0 async.

## Development Workflow

**Branch Strategy**:

1. Create feature branch from `main`
2. Implement following module patterns in `src/app/modules/`
3. Write tests FIRST (TDD) with pytest
4. Run quality gates (lint, type-check, test, coverage)
5. Create PR with Conventional Commits format
6. CI/CD MUST pass before merge

**Commit Standards**:

- Format: `type(scope): description`
- Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`
- Example: `feat(auth): add JWT refresh token endpoint`

**Definition of Done**:

- [ ] Code passes type checking
- [ ] Code passes linting
- [ ] All tests pass
- [ ] Test coverage ≥80%
- [ ] Database migrations created (if schema changed)
- [ ] Error handling with proper HTTP status codes
- [ ] Pydantic schemas validate all I/O
- [ ] Tenant isolation verified for multi-tenant features
- [ ] Google-style docstrings for public APIs

## Governance

**Amendment Process**:

1. Propose change via PR to this constitution
2. Document rationale and impact assessment
3. Update affected templates and documentation
4. Require team review and approval
5. Increment version according to semantic rules

**Versioning Policy**:

- MAJOR: Backward-incompatible principle changes or removals
- MINOR: New principles added or existing materially expanded
- PATCH: Clarifications, wording fixes, non-semantic refinements

**Compliance**:

- All PRs MUST verify compliance with these principles
- Constitution supersedes informal practices
- Complexity beyond these standards MUST be explicitly justified
- See `AGENTS.md` for runtime development guidance

**Version**: 1.2.0 | **Ratified**: 2025-12-27 | **Last Amended**: 2026-01-03
