# FastAPI SaaS Source Root - Agent Guidance

## Package Identity
Source code root containing FastAPI application, database migrations, and utility scripts for the complete SaaS platform.

## Setup & Run
```bash
# Install dependencies from source root
uv sync

# Run application from source root
uv run uvicorn src.app.main:app --reload

# Run database migrations from source root
uv run alembic upgrade head

# Run setup scripts from source root
uv run python src/scripts/create_first_superuser.py

# Test entire codebase
uv run pytest

# Type check entire codebase
uv run mypy src/

# Lint entire codebase
uv run ruff check src/
```

## Patterns & Conventions

### Project Structure
✅ DO: Follow clear separation of concerns
```
src/
├── app/           # Main FastAPI application
├── benchmarks/    # Operator-run perf measurement harnesses (tracked; NOT imported by app/)
├── migrations/     # Database migration files
├── scripts/        # Setup and utility scripts
└── __init__.py    # Package initialization
```

### Benchmark Harnesses
✅ DO: Keep perf measurement harnesses tracked in `src/benchmarks/`
```python
# src/benchmarks/security_state.py — pure module, no import-time side effects,
# so tests can import SECURITY_KEY_PATTERNS without booting the app.
# src/benchmarks/percentiles.py — the ONE percentile estimator (nearest rank).
# Every harness in the tree delegates to it; a second implementation is a bug
# the tree-wide scan in tests/unit/benchmarks/test_percentiles.py fails on.
# src/benchmarks/driver_count_io.py, src/benchmarks/census.py,
# src/benchmarks/prof_request_path.py — operator-run
# scripts, `__main__`-guarded so they can never be imported into a test session.
# Each holds its side effects (os.chdir to the repo root, load_dotenv with
# override=True, QUERY_PROFILER_ENABLED, the relaxed SECURITY_BUDGETS) in a
# bootstrap() called only from
# `__main__`: a developer `.env` in `src/` is gitignored by design, so a
# module-level load_dotenv(override=True) would clobber the environment
# tests/conftest.py sets for the WHOLE session.
# src/benchmarks/l12_pool_ratios.py, src/benchmarks/l13_body_offload.py —
# operator-run harnesses RECOVERED from one machine's home directory into the
# tree, because the artefacts they produced recorded about themselves that the
# driver lived OFF-repo ("this measurement is not reproducible from git and
# would fail the Phase 0 provenance gate"). A measurement whose instrument
# exists only on one box's disk cannot be re-checked by anyone else, so the
# DRIVER is the fix, not the prose. Their published reports keep
# `instrument.in_repo: false` UNCHANGED: that was true of the run, and
# rewriting a recorded fact to match the present is how a stored result stops
# being a record. The raw driver output now sits beside each report under
# `raw/`, and tests/unit/benchmarks/test_l12_l13_harnesses.py proves the
# PUBLISHED figures are the HARNESS's figures — a curated report that cannot
# be re-derived from the instrument that produced it is a story, not evidence.
# src/benchmarks/s8_logger_call_counter.py — operator-run COUNTER, not a
# microbenchmark: it counts how many times the attack-scan method gate
# evaluates its logging predicate and how many times it calls logger.debug,
# on a bodyless GET, at DEBUG / INFO / NOTSET. It exists because S-8's own
# arithmetic is 0 us (0.00 % of the 4.09 ms uncontended floor, 0.00 % of the
# 740 ms p95), and a claim whose quantity is not a duration has to be
# measured with an instrument that measures THAT quantity — a microbenchmark
# would have produced a statement about the harness. Two things make the
# counts mean something, and both are asserted rather than assumed: the GUARD
# is evaluated once per request in every arm (otherwise a difference in the
# debug count could be explained by the call site being reached a different
# number of times), and the counter is run against setup_logging(), because
# structlog's ambient default has a structurally always-True predicate and
# would make every arm report a debug call. It calls NO clock, so it is
# deliberately absent from the microbenchmark gate's ALLOWED_RAW_TIMERS.
# src/benchmarks/run_manifest.py — the perf run provenance gate, invoked as
# `python -m src.benchmarks.run_manifest` by scripts/run_perf_baseline.sh. It
# REFUSES to write a manifest unless every file in HARNESS_FILES is tracked and
# clean in git, because a baseline whose instrument cannot be recovered from git
# can never be re-checked; and unless every repo module the harness IMPORTS is
# tracked, because a clean checkout would not contain it. It omits every
# password. It has two modes and each is a separate exit code, never a default:
# `--emit-app-host-report`, run ON THE APP HOST, prints what the server would
# resolve to (event loop, HTTP impl, the image digest the deployment declared in
# PERF_IMAGE_DIGEST, effective limits, stores) AND the deployment manifest of
# addendum 6.1 (orchestrator, distro, CPU model, core count, store locality).
# The deployment manifest splits by what can honestly produce it: distro, CPU
# model, core count and (for IP-literal addresses) store locality are read off
# the machine, so they are evidence; the orchestrator is not knowable from
# inside a process and is declared in PERF_ORCHESTRATOR, with store locality
# declared in PERF_STORE_LOCALITY when the addresses are bare hostnames. No
# repository file records any of it (addendum N-3), and the CI runner's topology
# is explicitly NOT the answer. The load host passes that file back with
# `--app-host-report`, and the gate refuses to build a manifest without it (6),
# refuses a server that is not on uvloop + httptools (7), refuses a manifest
# missing any REQUIRED_MANIFEST_FIELDS (8), with `--phase end
# --assert-matches START` refuses a run whose configuration moved while it was in
# flight (9), and refuses a deployment manifest with a hole in it (10).
# The limits in the manifest are the APP HOST's, not the load host's and not the
# script's.
# The image digest is the one identity field the process cannot resolve about
# itself, so it is DECLARED and validated, never inferred from the commit or
# invented; an absent one is recorded and labelled, because a bare-metal app host
# is a real topology that simply cannot be tied to a build. The five deployment
# fields are different in kind and are NOT labelled when absent: an absent core
# count or store locality is missing EVIDENCE for the throughput claims, so it
# aborts the run instead (exit 10).
# The same module also reports the run's PHASES (P-5). locust's cold numbers are
# 5-10x its warm numbers, so a single reported percentile hides the inflation
# entirely; `--emit-phase-report` emits BOTH, per endpoint, plus the ratio
# between them, so the inflation is a number in the artefact rather than a
# surprise. The hold floor is MIN_HOLD_SECONDS (60), a declared constant in the
# module and NOT a number at the call site — the runner reads it back out with
# `--print-min-hold-seconds` and refuses a shorter hold rather than clamping it.
# A phase whose samples are not on disk aborts (11): a missing percentile is
# missing evidence, exactly like a missing core count. The three phases are
# three SEPARATE locust processes writing to separate CSV prefixes, so the
# hold's samples are structurally incapable of reaching a warm percentile —
# there is no reset to get wrong and no filtering that could drop one.
```
❌ DON'T: add a `__main__`-guarded harness to an import path a test imports.
❌ DON'T: record a run's security limits by reading the script that sets them;
   record what the SERVER ran with, and skip it entirely when it is unpinned.
❌ DON'T: compute a percentile inline. `sorted(v)[int(len(v) * q)]` truncates, so
   `int(20 * 0.95) == int(20 * 0.99)` and p95 and p99 both return the maximum —
   the arithmetic that produced the unusable "208.47 ms logout p95".
❌ DON'T: measure a per-call cost THROUGH your own instrument. A meter that
   charges two `perf_counter` reads, two `process_time` reads and three writes
   per call is itself the dominant cost at the scale being measured, so the
   figure comes out as a statement about the harness. L-8's phase 1 used to run
   with its own instruments installed, which is why the 19.486 us span figure
   it carried and P-19's 3.123 us figure (no meter) disagreed by 6x, and the
   ceiling's own text blamed the host for it (FastAPI_saas-7zwp). Take a
   per-call figure with the instrument NOT installed, price the instrument's
   own cost as its own arm, and let the measurement REFUSE to report a figure
   whose site charged the meter during the run.
❌ DON'T: assert a MECHANISM you have not executed. A harness docstring, and
   especially an arm label copied into the measurement artefact, is a stored
   result: three of them claimed every no-SDK `trace_span` call allocates a
   `NonRecordingSpan`, while on the installed `opentelemetry-api` 1.39.1
   `NoOpTracer.start_as_current_span` just yields the module-level
   `INVALID_SPAN` singleton and allocates nothing (FastAPI_saas-a7ce). The dead
   work is real — the context manager protocol is still entered and thrown away
   once per DB session — but it is not an allocation, and the same file already
   said so in one place and not the other. Run the API before describing it, and
   remember a `>=` pin is a FLOOR: the behaviour you measured is the behaviour
   of the version that happened to be installed, so say which one.
❌ DON'T: publish a curated report whose figures cannot be re-derived from the
   instrument that produced it. A report may be a readable summary, but the
   chain raw driver output -> published figure -> verdict has to be
   EXECUTABLE, or the summary is the only copy of the measurement — and a
   summary is precisely what a later reader cannot check. L-12 and L-13 were
   published as curated reports whose drivers were off-repo, so nothing could
   re-derive them; the drivers are now in the tree and the chain is pinned by
   an oracle, and the two reports' `instrument.in_repo: false` are still
    `false` because that is what the run was.
❌ DON'T: substantiate a claim whose quantity is not a duration with a
   duration. S-8's "moves capacity, not p95" arithmetic is 0 us — 0.00 % of
   the 4.09 ms uncontended floor and 0.00 % of the 740 ms p95 — so a
   microbenchmark asked to confirm it produces a number about the HARNESS,
   and a number about the harness is how a zero claim grows a false
   non-zero. Measure the quantity the claim is actually about: S-8's is a
   COUNT of guard evaluations and logger.debug calls, and
   `src/benchmarks/s8_logger_call_counter.py` measures exactly that, against
   the real logging stack, refusing to publish a run whose arms disagree.

❌ DON'T: retune a declared ceiling to fit a fresh measurement. A `Ceiling` is
   a specification chosen in advance; editing it so the new number passes is
   the `new < old` form `src/benchmarks/protocol.py` bans, whichever direction
   the new number moves.

### Script Organization
✅ DO: Use async patterns for all database scripts
```python
# src/scripts/create_first_superuser.py (lines 18-30)
async def create_first_user(session: AsyncSession) -> None:
    try:
        name = settings.ADMIN_NAME
        email = settings.ADMIN_EMAIL
        username = settings.ADMIN_USERNAME
        hashed_password = get_password_hash(settings.ADMIN_PASSWORD)
        
        # Database operations using async patterns
        query = select(User).filter_by(email=email)
        result = await session.execute(query)
        user = result.scalar_one_or_none()
```

### Migration Management
✅ DO: Keep migrations organized and versioned
```python
# migrations/env.py (lines 1-50)
from asyncio import run
from sqlalchemy.ext.asyncio import AsyncSession

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = async_engine
    
    async def do_run_migrations(connection):
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    
    run(do_run_migrations(connectable))
```

### Package Initialization
✅ DO: Use proper package structure
```python
# src/__init__.py (minimal)
# src/app/__init__.py (minimal)
# All other __init__.py files properly structured
```

### Absolute Import Pattern
✅ DO: Use absolute imports from project root
```python
# From scripts, use absolute imports
from src.app.core.config import settings
from src.app.core.db.database import AsyncSession
from src.app.modules.users.models import User
```

❌ DON'T: Use relative imports from scripts or main files

### Database Connection Management
✅ DO: Use proper session management
```python
# src/scripts/create_first_superuser.py (lines 72-73)
async def main():
    async with local_session() as session:
        await create_first_user(session)
```

### Configuration Hierarchy
✅ DO: Use environment-based configuration
```python
# All config via Pydantic Settings
from src.app.core.config import settings

# Environment files:
# .env.local - Local development secrets
# .env - Versioned environment variables
```

## Touch Points / Key Files

### Application Entry Points
- Main App: `src/app/main.py` - FastAPI application factory
- App Init: `src/app/__init__.py` - Application package initialization

### Database Management
- Migrations: `src/migrations/` - Alembic migration files and configuration
- Migration Env: `src/migrations/env.py` - Migration environment setup
- Migration Script: `src/migrations/script.py.mako` - Migration file template

### Setup Scripts
- Superuser: `src/scripts/create_first_superuser.py` - Create initial admin user
- Tier Setup: `src/scripts/create_first_tier.py` - Create initial subscription tiers

### Configuration
- Project Config: `pyproject.toml` - Python project configuration
- Alembic Config: `alembic.ini` - Database migration configuration
- Environment: `.env.local` - Local development secrets

### Package Structure
- Source Init: `src/__init__.py` - Source package initialization
- App Package: `src/app/` - Main application package with its own AGENTS.md

## JIT Index Hints

### Project Structure Searches
```bash
# Find all Python files in source
find src/ -name "*.py" -type f

# List main directories
ls src/ src/app/ src/app/modules/ src/app/core/

# Find setup scripts
find src/scripts/ -name "*.py" -type f

# Find migration files
find src/migrations/ -name "*.py" -type f
```

### Import Pattern Searches
```bash
# Find absolute imports
rg -n "from src\." src/scripts/ src/app/

# Find configuration usage
rg -n "from.*config import.*settings" src/

# Find database connections
rg -n "local_session\|async_engine" src/
```

### Migration Searches
```bash
# Find migration commands
rg -n "alembic.*upgrade|alembic.*downgrade" src/migrations/

# Find database operations in scripts
rg -n "select\|insert\|update\|delete" src/scripts/

# Find async functions
rg -n "async def.*:" src/scripts/
```

## Common Gotchas

### Database Scripts
✅ DO: Use proper async session management
```python
async with local_session() as session:
    await create_first_user(session)
```

❌ DON'T: Use sync database operations in scripts

### Import Paths
✅ DO: Use absolute imports from project root
```python
from src.app.core.config import settings
```

❌ DON'T: Use relative imports from scripts
```python
# Wrong in scripts
from ..app.core.config import settings
```

### Environment Configuration
✅ DO: Use `.env.local` for local secrets
❌ DON'T: Commit secrets to version control

### Package Structure
✅ DO: Keep all `__init__.py` files minimal
❌ DON'T: Put import logic in `__init__.py` files

### Migration Safety
✅ DO: Test migrations before applying to production
```bash
# Generate migration
uv run alembic revision --autogenerate -m "Description"

# Review SQL before applying
uv run alembic upgrade head --sql

# Apply migration
uv run alembic upgrade head
```

## Pre-PR Checks

### Full Stack Testing
```bash
# Install dependencies
uv sync

# Type check entire codebase
uv run mypy src/

# Lint entire codebase
uv run ruff check src/

# Run all tests
uv run pytest

# Test migrations
uv run alembic upgrade head

# Test application startup
uv run python -c "from src.app.main import app; print('App started successfully')"
```

### Script Testing
```bash
# Test setup scripts
uv run python src/scripts/create_first_superuser.py

# Test script dependencies
uv run python -c "
from src.app.core.config import settings
from src.app.core.db.database import local_session
print('Script dependencies loaded successfully')
"
```

### Database Validation
```bash
# Check database connection
uv run python -c "
import asyncio
from src.app.core.db.database import async_engine

async def test_connection():
    async with async_engine.begin() as conn:
        result = await conn.execute('SELECT 1')
        print('Database connection successful')

asyncio.run(test_connection())
"

# Check migration status
uv run alembic current
```

### Configuration Validation
```bash
# Test all configuration
uv run python -c "
from src.app.core.config import settings
from src.app.main import app
print('✅ Configuration loaded successfully')
print(f'✅ Project: {settings.PROJECT_NAME}')
print(f'✅ Environment: {settings.ENVIRONMENT}')
print(f'✅ Database: {settings.POSTGRES_SERVER}')
"

# Test admin configuration
uv run python -c "
from src.app.admin.initialize import create_admin_interface
admin = create_admin_interface()
print(f'✅ Admin interface: {\"enabled\" if admin else \"disabled\"}')
"
```

### Development Workflow
```bash
# Complete development setup sequence
uv sync                                    # Install dependencies
uv run alembic upgrade head                   # Run migrations
uv run python src/scripts/create_first_superuser.py  # Create admin
uv run uvicorn src.app.main:app --reload      # Start development server
```