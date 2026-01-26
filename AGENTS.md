# 🧠 Project Memory & Task Tracking (Beads)

This project uses **Beads (`bd`)** for persistent task tracking. Do NOT use temporary markdown lists or plans in your context window.

## 🛠️ Your Workflow Rules:

1.  **Start of Session:**

    - ALWAYS run `bd ready` first to see what tasks are unblocked and ready for you.
    - Run `bd list --status in_progress` to resume unfinished work.

2.  **During Work:**

    - If you find a bug: `bd create "Bug description" -t bug`
    - If you discover a new sub-task: `bd create "Task name" -p <priority>` and link it using `bd dep add`.
    - Update progress: `bd update <id> --status in_progress`.

3.  **End of Session (CRITICAL):**
    - Never leave without syncing.
    - Close finished tasks: `bd close <id> --reason "Implemented X"`.
    - Ensure all new findings are recorded in `bd`.

## ⚡ Quick Commands Reference:

- `bd ready` -> What should I do now?
- `bd create "Title" -d "Details"` -> Add new task.
- `bd list` -> See all tasks.
- `bd show <id>` -> See details of a task.

## 🔗 Advanced Dependencies:

bd dep add bd-123 --blocks bd-456      # Blocking
bd create "Task" --parent bd-789       # Epic child
bd create "Bug" --discovered-from bd-5  # Found during work

## 🔍 Discovery Commands:

bd blocked                    # Show blocked tasks
bd dep tree <id>             # Dependency graph
bd ready --json              # Machine-readable
bd list --assignee @me       # My work

## 📦 Epics Pattern:

bd create "Feature X" -t epic
bd create "Sub-task" --parent bd-<epic-id>

## 👥 Multi-Agent:

bd update bd-123 --status in_progress --assignee @me  # Claim work
bd list --status in_progress                          # Check others

## ⚡ Session Management:

**Rule:** One issue per session → prevents context window panic
1. `bd ready` → pick ONE
2. `bd update <id> --status in_progress` 
3. Implement only that
4. `bd close <id>`
5. Exit (fresh context)

## 🤖 Automation:

bd ready --json | jq -r '.[0].id'  # Get first ready task 

## 📊 Priority & Status:

**Priority:** 0=Critical, 1=High, 2=Medium, 3=Low
**Status:** open → in_progress → closed (or blocked)

bd create "Task" -p 0  # Critical priority
bd update bd-123 --status blocked --reason "Waiting for API"

## 🔄 Sync (CRITICAL):

bd sync  # Manual sync before: git pull/push, switching machines, multi-agent


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
  - Security: `src/app/core/security/` → [see src/app/core/security/AGENTS.md](src/app/core/security/AGENTS.md)
  - Database: `src/app/core/db/` → [see src/app/core/db/AGENTS.md](src/app/core/db/AGENTS.md)
- API layer: `src/app/api/` → [see src/app/api/AGENTS.md](src/app/api/AGENTS.md)
- Middleware: `src/app/middleware/` → [see src/app/middleware/AGENTS.md](src/app/middleware/AGENTS.md)
- Test suite: `tests/` → [see tests/AGENTS.md](tests/AGENTS.md)
- Database migrations: `src/migrations/` → [see src/migrations/AGENTS.md](src/migrations/AGENTS.md)

### Module Documentation

All 21 modules have dedicated AGENTS.md files:

| Module | Location | Purpose |
|--------|----------|---------|
| API Keys | `src/app/modules/api_keys/AGENTS.md` | Programmatic access |
| AI | `src/app/modules/ai/AGENTS.md` | AI integration |
| Audit Logs | `src/app/modules/audit_logs/AGENTS.md` | Immutable logging |
| Auth | `src/app/modules/auth/AGENTS.md` | Authentication |
| Billing | `src/app/modules/billing/AGENTS.md` | Stripe billing |
| Feature Flags | `src/app/modules/feature_flags/AGENTS.md` | Toggle management |
| Files | `src/app/modules/files/AGENTS.md` | File storage |
| Health | `src/app/modules/health/AGENTS.md` | Health checks |
| Magic Link | `src/app/modules/magic_link/AGENTS.md` | Passwordless auth |
| Notifications | `src/app/modules/notifications/AGENTS.md` | Email delivery |
| Passkey | `src/app/modules/passkey/AGENTS.md` | WebAuthn auth |
| Posts | `src/app/modules/posts/AGENTS.md` | Blog posts |
| Rate Limits | `src/app/modules/rate_limits/AGENTS.md` | Rate limiting |
| RBAC | `src/app/modules/rbac/AGENTS.md` | Permissions |
| SSO | `src/app/modules/sso/AGENTS.md` | OAuth providers |
| SSO Settings | `src/app/modules/sso_settings/AGENTS.md` | SSO config |
| Storage | `src/app/modules/storage/AGENTS.md` | Cloud storage |
| Storage Settings | `src/app/modules/storage_settings/AGENTS.md` | Storage config |
| Tasks | `src/app/modules/tasks/AGENTS.md` | Background jobs |
| Tenants | `src/app/modules/tenants/AGENTS.md` | Multi-tenancy |
| Tiers | `src/app/modules/tiers/AGENTS.md` | Billing tiers |
| User Preferences | `src/app/modules/user_preferences/AGENTS.md` | User settings |
| Users | `src/app/modules/users/AGENTS.md` | User management |
| Webhooks | `src/app/modules/webhooks/AGENTS.md` | Event delivery |

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

### 🚫 RESTRICTIONS (CRITICAL)

- **NO DOCKER EXECUTION:** Do NOT run, build, or compose Docker containers.
- **Docker Config Only:** You may EDIT `Dockerfile` or `docker-compose.yml`, but NEVER execute commands like `docker run`, `docker-compose up`, or `docker build`.
- **Local Dev:** Assume the user runs the app locally via `uvicorn` or `python`, NOT inside Docker during development.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Run `bd close <id>` for finished work and `bd update <id> --status in_progress` for ongoing items.
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

## Plan Mode

- Make the plan extremely concise. Sacrifice grammar for the sake of concision.
- At the end of each plan, give me a list of unresolved questions to answer, if any.

<skills_system priority="1">

## Available Skills

<!-- SKILLS_TABLE_START -->
<usage>
When users ask you to perform tasks, check if any of the available skills below can help complete the task more effectively. Skills provide specialized capabilities and domain knowledge.

How to use skills:
- Invoke: Bash("openskills read <skill-name>")
- The skill content will load with detailed instructions on how to complete the task
- Base directory provided in output for resolving bundled resources (references/, scripts/, assets/)

Usage notes:
- Only use skills listed in <available_skills> below
- Do not invoke a skill that is already loaded in your context
- Each skill invocation is stateless
</usage>

<available_skills>

<skill>
<name>api-design-principles</name>
<description>Master REST and GraphQL API design principles to build intuitive, scalable, and maintainable APIs that delight developers. Use when designing new APIs, reviewing API specifications, or establishing API design standards.</description>
<location>global</location>
</skill>

<skill>
<name>async-python-patterns</name>
<description>Master Python asyncio, concurrent programming, and async/await patterns for high-performance applications. Use when building async APIs, concurrent systems, or I/O-bound applications requiring non-blocking operations.</description>
<location>global</location>
</skill>

<skill>
<name>postgresql</name>
<description>Design a PostgreSQL-specific schema. Covers best-practices, data types, indexing, constraints, performance patterns, and advanced features</description>
<location>global</location>
</skill>

<skill>
<name>python-packaging</name>
<description>Create distributable Python packages with proper project structure, setup.py/pyproject.toml, and publishing to PyPI. Use when packaging Python libraries, creating CLI tools, or distributing Python code.</description>
<location>global</location>
</skill>

<skill>
<name>python-performance-optimization</name>
<description>Profile and optimize Python code using cProfile, memory profilers, and performance best practices. Use when debugging slow Python code, optimizing bottlenecks, or improving application performance.</description>
<location>global</location>
</skill>

<skill>
<name>python-testing-patterns</name>
<description>Implement comprehensive testing strategies with pytest, fixtures, mocking, and test-driven development. Use when writing Python tests, setting up test suites, or implementing testing best practices.</description>
<location>global</location>
</skill>

</available_skills>
<!-- SKILLS_TABLE_END -->

</skills_system>
