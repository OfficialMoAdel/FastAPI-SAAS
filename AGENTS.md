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


# Enforce the per-module file contract (router.py + service.py + models/schemas).
# NOTE: currently RED — the `security` module has no service.py. A non-zero exit
# is a pre-existing condition, not a regression you introduced.
uv run python scripts/check_module_structure.py

# Reject production comments that exist only to keep a test's grep green.
# See "Comments vs. test greps" below for the convention it enforces.
uv run python scripts/check_comment_grease.py

# Fail on a mutating endpoint (POST/PUT/PATCH/DELETE) with no response_model.
# Endpoints that genuinely cannot carry one — 204 No Content, a returned
# Response/StreamingResponse, or a genuinely dynamic body — are allow-listed
# with the reason in the script, and a stale entry is itself a failure.
uv run python scripts/check_mutation_response_models.py
```

## Module Generator CLI

Generate complete modules with all 6 files pre-configured:

```bash
# Generate a module with fields
uv run python -m src.scripts.create_module products --fields 'name:str,price:Decimal,quantity:int'

# Preview without writing files
uv run python -m src.scripts.create_module products --fields 'name:str,price:Decimal' --dry-run

# Generate without tenant awareness (rare)
uv run python -m src.scripts.create_module tags --fields 'name:str,color:str' --no-tenant

# Overwrite existing files
uv run python -m src.scripts.create_module products --force
```

**Field Types:** `str`, `int`, `bool`, `float`, `Decimal`, `datetime`, `text`, `json`, `uuid`  
**Optional fields:** Add `?` suffix (e.g., `description:text?`)

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
  - Config: `src/app/core/config/` — a **package** (`app.py`, `auth.py`, `cache.py`, `security.py`, …); there is no `core/config.py`
  - Events: `src/app/core/events/` — a **package** (`bus.py` holds `EventBus`); there is no `core/events.py`
- API layer (versioning, deps): `src/app/api/` → [see src/app/api/AGENTS.md](src/app/api/AGENTS.md)
  - v1 group aggregators: `src/app/api/v1/core.py` (non-billing/admin routers), `src/app/api/v1/billing.py`, `src/app/api/v1/admin_group.py` — **not** a flat include list in `v1/__init__.py`
- Outbound client adapters (billing/notifications/storage): `src/app/clients/`
- Composition root: `src/app/composition.py` — `create_application()` and `lifespan_with_admin`
- Shared domain exceptions: `src/app/shared/exceptions.py` (all domain errors derive from `AppException`; translated to HTTP in `src/app/core/errors.py`)
- Admin panel (CRUDAdmin mount): `src/app/admin/` → [see src/app/admin/AGENTS.md](src/app/admin/AGENTS.md)
- Middleware stack: `src/app/middleware/` → [see src/app/middleware/AGENTS.md](src/app/middleware/AGENTS.md)
- Test suite: `tests/` → [see tests/AGENTS.md](tests/AGENTS.md) (suites live under `unit/`, `integration/`, `contract/`; the `tests/` root holds `conftest.py`, `factories/`, `fixtures/`)
- Database migrations: `src/migrations/` → [see src/migrations/AGENTS.md](src/migrations/AGENTS.md)
- Ops/perf scripts: `scripts/` (deploy, health-check, benchmarks, doc/module checks); setup scripts: `src/scripts/` (superuser, tier, module generator); perf **measurement harnesses**: `src/benchmarks/` (tracked, operator-run, never imported by `src/app/`)

### Doc Pointer Integrity

Every rooted `src/…`, `tests/…`, `scripts/…`, `docs/…` and `.github/…`
path in any `AGENTS.md` or `ARCHITECTURE.md` is a claim that the path
exists, and is enforced in CI:

```bash
uv run python scripts/check_doc_paths.py
```

Fix a failing pointer rather than deleting it, or add a reasoned
`ALLOWLIST` entry in the script if the debt is real and out of scope. A
documented path that does not exist is worse than an undocumented one: the
reader trusts it and then spends time proving it wrong.

The gate is **mandatory**: `.github/workflows/tests.yml` has no
`paths`/`paths-ignore` filter, so it runs on every push and every pull
request, and its non-zero exit fails the build. A documentation-only PR
starts the workflow too — which it used not to, when the triggers
excluded `**/*.md`, meaning the gate could not fire on the rot it exists
to catch. `tests/unit/scripts/test_doc_gate_wiring.py` parses that
trigger block and fails if a paths filter is reintroduced, so removing or
weakening the gate is itself a caught change.


### Module Documentation

All 21 feature modules have a dedicated `AGENTS.md` (see table):

| Module | Location | Purpose |
|--------|----------|---------|
| AI | `src/app/modules/ai/AGENTS.md` | AI integration |
| API Keys | `src/app/modules/api_keys/AGENTS.md` | Programmatic access |
| App Configuration | `src/app/modules/app_configuration/AGENTS.md` | Public app config endpoint |
| Audit Logs | `src/app/modules/audit_logs/AGENTS.md` | Immutable logging |
| Auth | `src/app/modules/auth/AGENTS.md` | Authentication & Identity (passwords, SSO, passkeys, magic link, MFA) |
| Billing | `src/app/modules/billing/AGENTS.md` | Stripe billing |
| Dashboard | `src/app/modules/dashboard/AGENTS.md` | Dashboard metrics |
| Feature Flags | `src/app/modules/feature_flags/AGENTS.md` | Toggle management |
| Health | `src/app/modules/health/AGENTS.md` | Health checks |
| Notifications | `src/app/modules/notifications/AGENTS.md` | Email delivery |
| Posts | `src/app/modules/posts/AGENTS.md` | Blog posts |
| RBAC | `src/app/modules/rbac/AGENTS.md` | Permissions |
| Rate Limits | `src/app/modules/rate_limits/AGENTS.md` | Rate limiting |
| Security | `src/app/modules/security/AGENTS.md` | Passkey convenience routes |
| Storage | `src/app/modules/storage/AGENTS.md` | Object storage & tenant configuration |
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

## Architecture Snapshot

- **Modular monolith.** `src/app/main.py` is a thin composition root: it imports `create_application` and `lifespan_with_admin` from **`src/app/composition.py`** (`create_application` is defined at `composition.py:383`), adds the middleware stack (LIFO order documented inline in main.py), and registers exception handlers. `src/app/core/setup.py` is the lifecycle *building blocks* seam (`create_redis_cache_pool`, `resolve_create_tables_on_start`, `set_threadpool_tokens`, …) — it does **not** define the app factory. Do NOT add setup logic to main.py — extend `composition.py` or the `core/setup.py` helpers it schedules.
- **Outbound integrations** (Stripe, SMTP, storage) live behind adapters in `src/app/clients/`; services never import vendor SDKs directly. **The inbound side is the same boundary**: the verified Stripe webhook event type is `StripeEvent`, imported from `src.app.clients.billing` (aliased once in `clients/billing/stripe.py`); only `clients/billing/stripe.py` and `billing/webhook/client.py` may `import stripe`. Enforced by `tests/core/test_stripe_adapter_boundary.py`.
- **The AI providers are deliberately NOT in `src/app/clients/`.** See [ADR-0007](docs/adr/0007-inbound-stripe-boundary-and-ai-provider-placement.md): they are the one outbound integration that talks plain `httpx` instead of a vendor SDK, and their protocol/DTO layer (`ai/providers.py`) is domain vocabulary the service layer constructs and consumes, so moving it would invert the dependency. `openai`/`anthropic` remain declared dependencies but are imported nowhere — the providers speak raw HTTP.
- **Errors:** domain exceptions derive from `AppException` (`src/app/shared/exceptions.py`) and are translated to HTTP centrally in `src/app/core/errors.py`. Services stay HTTP-free.
- Full architecture guide: [ARCHITECTURE.md](ARCHITECTURE.md)

## Definition of Done

- [ ] Code passes `uv run mypy src/` (no type errors)
- [ ] Code passes `uv run ruff check` (no lint errors)
- [ ] All tests pass: `uv run pytest`
- [ ] Database migrations created if needed
- [ ] Documentation updated for new endpoints
- [ ] Every touched module's `AGENTS.md` updated in this same change, or a
      note in the PR saying why it is still accurate — see "Documentation
      Ownership" above. This includes renaming/moving/deleting a file,
      changing a test path, and changing a public class or function name.
- [ ] `uv run python scripts/check_doc_paths.py` exits 0 (it runs in CI and
      blocks the merge)
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


<!-- bv-agent-instructions-v6 -->

---

## Beads Workflow Integration
This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

This project uses a Beads tracker—either the Go `bd` Run `bd prime` to see full workflow context and commands. CLI for persistent issue tracking, plus [beads_viewer](https://github.com/Dicklesworthstone/beads_viewer) (`bv`) for graph-aware triage. Issues are stored in `.beads/`. `bv` auto-discovers supported JSONL exports, including `.beads/issues.jsonl` and legacy `.beads/beads.jsonl`.

**⚠️ CRITICAL RULES:**
- Use `bd remember` for persistent project knowledge — do NOT use ad hoc memory files (like MEMORY.md).
- Understand the architecture: issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export for tools like `bv`.
- Refer to `.agents/skills/beads/SKILL.md` (if installed) for deep Beads workflow guidance.
- Choose the tracker CLI from this repository's instructions and configuration
- **Do NOT run both trackers against the same workspace**
- Do NOT infer the tracker solely from the JSONL filename
- **Do NOT use temporary markdown lists or plans in your context window** - Always use Beads for persistent tracking

---

## 🚀 Quick Start Workflow

### 1️⃣ Start of Session (ALWAYS do this first):

```bash
# Check ready tasks (use your configured tracker)
bd ready --json              # For Go Beads workspace

# Resume unfinished work
bd list --status in_progress --json

# Graph-aware priority analysis
bv --robot-triage            # THE MEGA-COMMAND: start here
```

### 2️⃣ During Work:

```bash
# Claim task before starting (CRITICAL)
bd update <id> --claim --json

# Create new tasks/bugs when discovered
bd create "Bug description" -t bug -d "Details" --json

# Add dependencies
bd dep add <child-id> <parent-id>
```

### 3️⃣ End of Session (CRITICAL - Never skip):

```bash
# Close finished tasks
bd close <id> --json


# Sync for bv (CRITICAL)
bd export -o .beads/issues.jsonl

# Exit with fresh context

1. **File follow-ups**: Create beads for any discovered bugs or remaining work (`bd create ...`).
2. **Run quality gates** (if code changed): Ensure tests, linters, and builds pass.
3. **Update & Close**: Close finished work (`bd close <id> --json`).
4. **Sync Export**: Run `bd export -o .beads/issues.jsonl` so `bv` and the JSONL export are current.
5. **Hand off**: Summarize changes, validation results, and closed issues. Stop and wait for user approval before any git commit/push (respecting the Conservative/Minimal profile).
```

---

## ⚡ Session Management (CRITICAL for AI Agents)

**Rule:** One issue per session → prevents context window panic

```bash
1. bd ready / br ready → pick ONE task
2. bd update <id> --claim / br update <id> --claim
3. Implement only that task
4. bd close <id> / br close <id>
5. Exit (fresh context)
```

**Why?** Prevents context window exhaustion and ensures focused, trackable work.

---

## 🎯 Using bv for Triage (Graph-Aware Analysis)

bv is a graph-aware triage engine with precomputed metrics (PageRank, betweenness, critical path, cycles, HITS, eigenvector, k-core).

**Scope boundary:** bv handles *what to work on* (triage, priority, planning). The selected tracker CLI (`bd` or `br`) handles creating, claiming, modifying, and closing beads.

**CRITICAL: Use ONLY --robot-* flags. Bare bv launches an interactive TUI that blocks your session.**

### Essential bv Commands:

| Command | Returns |
|---------|---------|
| `bv --robot-triage` | **THE MEGA-COMMAND: start here** - quick_ref, recommendations, quick_wins, blockers_to_clear, project_health, commands |
| `bv --robot-next` | Minimal: just the single top pick + claim command |
| `bv --robot-plan` | Parallel execution tracks with unblocks lists |
| `bv --robot-priority` | Priority misalignment detection with confidence |
| `bv --robot-insights` | Full metrics: PageRank, betweenness, HITS, eigenvector, critical path, cycles, k-core |
| `bv --robot-alerts` | Stale issues, blocking cascades, priority mismatches |
| `bv --robot-suggest` | Hygiene: duplicates, missing deps, label suggestions, cycle breaks |
| `bv --robot-diff --diff-since <ref>` | Changes since ref: new/closed/modified issues |
| `bv --robot-graph [--graph-format=json\|dot\|mermaid]` | Dependency graph export |

### Scoping & Filtering:

```bash
bv --robot-plan --label backend              # Scope to label's subgraph
bv --robot-insights --as-of HEAD~30          # Historical point-in-time
bv --recipe actionable --robot-plan          # Pre-filter: ready to work (no blockers)
bv --recipe high-impact --robot-triage       # Pre-filter: top PageRank scores
```

### TOON Output Format:

```bash
# Compact tabular encoding - use --stats to compare sizes
bv --robot-graph --format toon
bv --robot-triage --format toon --stats
```

**Note:** TOON is 7% smaller than JSON for `--robot-graph` but 9-15% LARGER for nested payloads.

**Important:** Recommendations can include blocked or assigned work; `triage.quick_ref.top_picks` reflects snapshot readiness. A suggested action records its original local ID, working directory, and tracker route. Use that route rather than a namespaced display ID. Inspect current tracker state before execution: analysis does not reserve work or guarantee that a later claim succeeds.

Every robot command emits one JSON object; with `--graph-format=dot` or `mermaid` the diagram text is the `graph` field (`bv --robot-graph --graph-format=dot | jq -r .graph`), not the whole output.

---

## 🛠️ Complete Command Reference

### Go Beads (`bd`) Commands:

```bash
# Discovery
bd ready --json                    # Show unblocked issues ready to work
bd list --json                     # See all tasks
bd list --assignee @me --json      # My work
bd list --status in_progress --json
bd show <id> --json                # Full issue details
bd blocked                         # Show blocked tasks
bd dep tree <id>                   # Dependency graph visualization

# Create
bd create "Title" -t <type> -p <priority> -d "Details" --json
bd create "Task" --parent bd-<epic-id>
bd create "Bug" --discovered-from bd-5

# Update
bd update <id> --claim --json      # Atomically claim work
bd update <id> --status <status> --json
bd update <id> --assignee @me --json

# Dependencies
bd dep add <issue> <depends-on>
bd dep add bd-123 --blocks bd-456  # Explicit blocking relationship

# Close
bd close <id> --json
bd close <id1> <id2> --json

# Sync (CRITICAL before git operations)
bd sync                            # Manual sync
bd export -o .beads/issues.jsonl   # Refresh compatibility export for bv
```

---

## 🔗 Advanced Dependencies & Patterns

### Basic Dependencies:

```bash
bd dep add <issue> <depends-on>    # Go Beads
```

### Advanced Patterns:

```bash
# Blocking relationships
bd dep add bd-123 --blocks bd-456

# Epic child tasks
bd create "Task" --parent bd-789

# Bugs discovered during work
bd create "Bug" --discovered-from bd-5
```

### 🧩 ID Hierarchy Pattern (Epic → Task → Sub-task):

- `<EPIC_ID>` (Epic) - Example: `bd-a3f8`
- `<EPIC_ID>.1` (Task) - Example: `bd-a3f8.1`
- `<EPIC_ID>.1.1` (Sub-task) - Example: `bd-a3f8.1.1`

### Epics Pattern:

```bash
bd create "Feature X" -t epic
bd create "Sub-task" --parent bd-<epic-id>
```

---

## 📊 Status, Priority & Types

### Status Flow:

```
open → in_progress → closed
           ↓
        blocked
```

### Priority Levels:

- **P0 / 0** = Critical
- **P1 / 1** = High
- **P2 / 2** = Medium
- **P3 / 3** = Low
- **P4 / 4** = Backlog

```bash
bd create "Task" -p 0              # Critical priority
bd update bd-123 --status blocked --reason "Waiting for API"
```

### Issue Types:

`task`, `bug`, `feature`, `epic`, `chore`, `docs`, `question`

---

## 🤖 Multi-Agent & Automation

### Multi-Agent Commands:

```bash
# Claim work for specific agent
bd update bd-123 --status in_progress --assignee @me

# Check others' work
bd list --status in_progress
```

### Automation:

```bash
# Get first ready task ID
bd ready --json | jq -r '.[0].id'
```

---

## 🔄 Sync & Git Policy (CRITICAL)

### Architecture
Issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md for anti-patterns.

### Agent Context Profiles (Permissions)
- **Conservative/Minimal (Default):** Use `bd` for task tracking. Do NOT run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files and suggest next commands.
- **Team-maintainer (Opt-in only):** Agents may run quality gates, commit, and push ONLY if the repository explicitly opts in.
- **Override Rule:** Explicit user or orchestrator instructions always override these profiles.

### Sync Commands
**For Go Beads (`bd`):**
```bash
bd sync                            # Manual sync
bd export -o .beads/issues.jsonl   # Refresh passive export for bv

**⚠️ CRITICAL: Never leave without syncing!**

**Manual sync required before:**
- `git pull` / `git push`
- Switching machines
- Multi-agent coordination
- Starting a new session

### Git Policy:

Tracker commands do not grant permission to commit or push application code. Follow this repository's own git and tracker instructions before staging, committing, syncing, or pushing. If the repository says "commit only when asked," that rule overrides any generic workflow advice.

---

## 📋 Workflow Pattern Summary

1. **Triage**: Run `bv --robot-triage` to find the highest-impact actionable work
2. **Verify**: Check the selected tracker's `show`/`ready` output before claiming
3. **Claim**: Use `bd update <id> --claim --json`
4. **Work**: Implement the task (ONE issue per session)
5. **Complete**: Use the selected tracker's `close` command
6. **Sync**: Run `br sync --flush-only` or `bd export` so the JSONL export is current
7. **Exit**: Fresh context for next session

---

## 🔑 Key Concepts

- **Dependencies**: Issues can block other issues. `bd ready --json` show unblocked work.
- **Priority**: Use numbers 0-4 (P0-P4), not words
- **Types**: task, bug, feature, epic, chore, docs, question
- **Blocking**: Use `bd dep add <issue> <depends-on>` to add dependencies
- **Session Management**: One issue per session prevents context window panic
- **Sync**: Always sync before git operations or switching contexts

---
<!-- end-bv-agent-instructions -->