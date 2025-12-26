# FastAPI SaaS Boilerplate - Agent Guidance

## Project Snapshot
FastAPI SaaS boilerplate with modular architecture, async/await patterns, and comprehensive auth/rate limiting. Uses SQLAlchemy 2.0 + Pydantic v2 + Redis + PostgreSQL. Sub-modules have their own AGENTS.md files.

## Root Setup Commands
```bash
# Install dependencies
uv sync

# Run development server
uv run uvicorn src.app.main:app --reload

# Type check all
uv run mypy src/

# Lint all
uv run ruff check

# Test all
uv run pytest

# Database migrations
uv run alembic upgrade head
```

## Universal Conventions
- **Code Style**: ruff for formatting, mypy for strict type checking
- **Async/Await**: All endpoints and database operations must be async
- **Imports**: Use `src.app.` absolute imports, never relative imports
- **Environment**: All config via Pydantic Settings, never hardcoded values
- **Commits**: Conventional Commits format (`feat:`, `fix:`, `docs:`, etc.)
- **PR Requirements**: Must pass type check, lint, and tests

## Security & Secrets
- Never commit tokens or API keys
- Use `.env.local` for local development secrets
- All secrets via environment variables with `SECRET_` prefix
- PII handling: Use Pydantic validators for sensitive data
- JWT tokens: Use refresh token pattern, store access tokens in Redis

## JIT Index - Directory Map

### Package Structure
- Core modules: `src/app/modules/` → [see src/app/modules/AGENTS.md](src/app/modules/AGENTS.md)
- Infrastructure: `src/app/core/` → [see src/app/core/AGENTS.md](src/app/core/AGENTS.md)
- API layer: `src/app/api/` → [see src/app/api/AGENTS.md](src/app/api/AGENTS.md)
- Test suite: `tests/` → [see tests/AGENTS.md](tests/AGENTS.md)
- Database migrations: `migrations/` → [see migrations/AGENTS.md](migrations/AGENTS.md)

### Quick Find Commands
- Find a module: `find src/app/modules -name "*.py" | head -20`
- Search for a function: `rg -n "def " src/app/modules/`
- Find API routes: `rg -n "router\.(get|post|put|delete)" src/app/modules/`
- Find database models: `rg -n "class.*Base" src/app/modules/`
- Find schemas: `rg -n "class.*Schema" src/app/modules/`
- Find tests: `find tests -name "*.py"`

### Module Pattern Commands
- List all modules: `ls src/app/modules/`
- Find module structure: `tree src/app/modules/{module_name}`
- Search module services: `rg -n "class.*Service" src/app/modules/`
- Find CRUD operations: `rg -n "FastCRUD" src/app/modules/`

## Definition of Done
- [ ] Code passes `uv run mypy src/` (no type errors)
- [ ] Code passes `uv run ruff check` (no lint errors)
- [ ] All tests pass: `uv run pytest`
- [ ] Database migrations created if needed
- [ ] Documentation updated for new endpoints
- [ ] Error handling implemented with proper HTTP status codes
- [ ] Pydantic schemas validate all input/output

## Core Dependencies
- FastAPI: Web framework
- SQLAlchemy 2.0: Async ORM
- Pydantic v2: Data validation
- FastCRUD: Database operations
- Redis: Caching and job queue
- ARQ: Background job processing
- Alembic: Database migrations
- CRUDAdmin: Admin panel

## Development Workflow
1. Create feature branch from `main`
2. Implement module following existing patterns
3. Add tests with pytest
4. Run type checking and linting
5. Test database migrations
6. Create PR with conventional commit message
7. Ensure CI/CD passes before merge

## Coding Standards & Rules (STRICT)

### Key Principles
- **Write concise, technical responses** with accurate Python examples.
- **Use Service Classes** for module business logic (Service Layer). Prefer functions only for small pure utilities.
- **Prefer iteration and modularization** over code duplication.
- **Naming:** Use descriptive variable names with auxiliary verbs (e.g., `is_active`, `has_permission`).
- **Files:** Use lowercase with underscores for directories and files (e.g., `routers/user_routes.py`).
- **Pattern:** Use the Receive an Object, Return an Object (RORO) pattern where applicable.

### Python & FastAPI
- Use `def` for pure synchronous functions and `async def` for asynchronous operations.
- **Type Hints:** Use type hints for ALL function signatures.
- **Validation:** Prefer Pydantic V2 models over raw dictionaries.
- **Structure:** Organize modules into: `router`, `service`, `models`, `schemas`, `crud`.
- **Syntax:** Use concise, one-line syntax for simple conditional statements (e.g., `if condition: do_something()`).

### Error Handling
- **Early Returns:** Handle errors and edge cases at the beginning of functions to avoid nesting.
- **Guard Clauses:** Use guard clauses to handle preconditions early.
- **Exceptions:** Use `HTTPException` for expected errors; implement custom error factories for consistency.
- **Logging:** Implement proper error logging.

### Dependencies & Stack
- **Framework:** FastAPI
- **Validation:** Pydantic V2
- **Database:** SQLAlchemy 2.0 (Async) + asyncpg
- **Caching:** Redis

### FastAPI-Specific Guidelines
- **Routes:** Use declarative route definitions with clear return type annotations.
- **Lifespan:** Minimize `app.on_event`; prefer lifespan context managers.
- **Middleware:** Use middleware for logging, error monitoring, and performance optimization.
- **Async:** Optimize for performance using async functions for I/O-bound tasks.
- **Pydantic:** Use `BaseModel` for consistent input/output validation.

### Performance Optimization
- Minimize blocking I/O operations.
- Implement caching for static and frequently accessed data (Redis).
- Use lazy loading techniques for large datasets.
