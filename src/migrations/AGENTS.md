# FastAPI SaaS Database Migrations - Agent Guidance

## Package Identity
Database migration management for the FastAPI SaaS application using Alembic with async SQLAlchemy 2.0 and PostgreSQL.

> **Migrations are consolidated into 8 named origin layers** (`01_foundation.py` … `08_enable_rls_policies.py`) — not date-stamped revisions. New schema changes should extend the relevant layer's `upgrade()`/`downgrade()` rather than stacking a new dated revision, unless the layer is already applied in production. `src/app/core/setup.py::resolve_create_tables_on_start` notes that `create_all`-created tables silently lack the migration 08 RLS policies.

## Setup & Run
```bash
# Generate new migration
uv run alembic revision --autogenerate -m "Migration description"

# Apply all migrations
uv run alembic upgrade head

# Apply specific migration
uv run alembic upgrade +1

# Rollback migration
uv run alembic downgrade -1

# Rollback to specific version
uv run alembic downgrade <revision_id>

# View migration history
uv run alembic history

# View current revision
uv run alembic current

# View migration SQL (without executing)
uv run alembic upgrade head --sql
```

## Patterns & Conventions

### Migration File Structure
✅ DO: Use descriptive migration names and proper file structure
```python
# migrations/versions/001_initial_migration.py
"""Initial migration

Revision ID: 001
Revises: 
Create Date: 2024-01-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Create tables
    pass

def downgrade() -> None:
    # Drop tables
    pass
```

### Async Migration Pattern
✅ DO: Use async patterns for database operations
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

### Table Creation
✅ DO: Create tables with proper constraints and indexes
```python
def upgrade() -> None:
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=100), nullable=False),
        sa.Column('username', sa.String(length=50), nullable=False),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('is_superuser', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('username')
    )
    
    # Create indexes
    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    op.create_index('ix_users_username', 'users', ['username'], unique=True)
```

### Column Addition
✅ DO: Add columns with proper defaults and nullability
```python
def upgrade() -> None:
    # Add nullable column with default
    op.add_column('users', sa.Column('profile_image', sa.String(length=255), nullable=True))
    
    # Add non-nullable column (requires two steps)
    op.add_column('users', sa.Column('tier_id', sa.Integer(), nullable=True))
    # Update existing rows with default value
    op.execute("UPDATE users SET tier_id = 1 WHERE tier_id IS NULL")
    # Make column non-nullable
    op.alter_column('users', 'tier_id', nullable=False)
```

### Foreign Key Constraints
✅ DO: Add foreign keys with proper cascade rules
```python
def upgrade() -> None:
    # Add foreign key constraint
    op.create_foreign_key(
        'fk_users_tier_id',
        'users',
        'tiers',
        ['tier_id'],
        ['id'],
        ondelete='SET NULL'  # or 'CASCADE', 'RESTRICT'
    )
```

## Touch Points / Key Files

### Migration Configuration
- Alembic Config: `alembic.ini` - Alembic configuration file
- Environment: `src/migrations/env.py` - Migration environment setup
- Script Template: `src/migrations/script.py.mako` - Migration file template

### Migration Files
- Versions: `src/migrations/versions/` - Individual migration files
- README: `src/migrations/versions/README.MD` - Migration documentation

### Database Models
- Base Models: `src/app/core/db/models.py` - Base SQLAlchemy models
- Module Models: `src/app/modules/*/models.py` - Feature-specific models

## JIT Index Hints

### Migration Searches
```bash
# List all migrations
ls migrations/versions/

# View migration history
uv run alembic history

# Find specific migration
uv run alembic show <revision_id>

# Search migration files
rg -n "def upgrade\|def downgrade" migrations/versions/
```

### Model Searches
```bash
# Find all database models
rg -n "class.*Base" src/app/modules/

# Find table definitions
rg -n "__tablename__" src/app/modules/

# Find column definitions
rg -n "sa\.Column" src/app/modules/

# Find foreign keys
rg -n "sa\.ForeignKey" src/app/modules/
```

## Common Gotchas

### Migration Dependencies
✅ DO: Ensure proper migration ordering
```python
# In migration file
down_revision = '001'  # This migration comes after 001
```

❌ DON'T: Create circular dependencies

### Column Changes
✅ DO: Use two-step process for non-nullable columns
```python
# Step 1: Add nullable column
op.add_column('users', sa.Column('new_field', sa.String(), nullable=True))

# Step 2: Update data and make non-nullable
op.execute("UPDATE users SET new_field = 'default' WHERE new_field IS NULL")
op.alter_column('users', 'new_field', nullable=False)
```

❌ DON'T: Add non-nullable columns without default values

### Data Migration
✅ DO: Include data migration in upgrade function
```python
def upgrade() -> None:
    # Schema changes
    op.add_column('users', sa.Column('full_name', sa.String(), nullable=True))
    
    # Data migration
    connection = op.get_bind()
    connection.execute(
        "UPDATE users SET full_name = username WHERE full_name IS NULL"
    )
```

❌ DON'T: Forget data migration when adding required fields

### Rollback Safety
✅ DO: Ensure downgrade function properly reverses changes
```python
def upgrade() -> None:
    op.create_table('new_table', ...)
    op.drop_table('old_table')

def downgrade() -> None:
    op.create_table('old_table', ...)
    op.drop_table('new_table')
```

❌ DON'T: Create migrations that cannot be safely rolled back

### Column types are a contract, and the WRITE may still succeed (FastAPI_saas-vthy)
✅ DO: Declare the type the ORM maps. The application reads and writes through
the ORM, so the migration is the one that must conform, not the model.

Nothing in the tree catches a type drift today, for two concrete reasons:

- `create_all` derives the schema FROM the ORM, so a dev database built that way
  can never show this class of bug. Only a database built by `alembic upgrade
  head` can.
- `scripts/check_schema_parity.py` compares column NAMES only, and the `alembic
  check` step in CI carries `continue-on-error: true`.

Measured example — `api_keys.scopes` was `postgresql.JSONB` in layer 03 while the
model maps `Text`, and `key_prefix` was `String(10)` against `String(8)`:

- The INSERT still SUCCEEDED on the jsonb column: asyncpg sends untyped
  parameters, so the server coerces the literal. A "type mismatch breaks writes"
  story would have looked harmless.
- The READ is what broke: SQLAlchemy's asyncpg dialect decodes a jsonb column to
  a Python **list**, and the service read that value with `json_loads`, which
  raises on a list. Every `X-API-Key` request would have 500'd on a
  migration-built database — while `POST /api/v1/api-keys` kept working, which is
  why the defect was so hard to place.
- A raw asyncpg read returns json as raw text and HIDES this. Measure a type
  drift by reading the column through the ORM (`select(Model)`), never through
  the raw driver, and re-verify the read path after the fix.
- A wider string than the model asks for (`String(10)` vs `String(8)`) is
  cosmetic here, but it is still the schema claiming something the code does not.
- An already-created database keeps its old column type: fixing the migration does
  NOT rewrite it. That needs an explicit
  `op.alter_column("scopes", type_=sa.Text(), postgresql_using="scopes::text")`
  in a NEW revision, which was deliberately out of scope for vthy.

Oracle: `tests/unit/migrations/test_api_keys_column_type_parity.py`.

### The gate now compares types too, and only where a difference is observable (FastAPI_saas-d201)

`scripts/check_schema_parity.py` used to compare column NAMES only, which is
why `api_keys.scopes` got through. It now also compares declared TYPES, and the
scope was chosen deliberately:

- **different SQL family** -> FAIL. The driver hands the application a
  different Python object than the ORM promised. `api_keys.scopes`
  (`Text` vs `JSONB`) is this class; so are `user_preferences.quiet_hours_*`
  (`String(5)` vs `sa.Time(timezone=True)`), which are allowlisted and owned by
  `FastAPI_saas-nqk6` because they were MEASURED — and measured worse than
  allowlisting suggests: the ORM cannot insert the row at all.
- **a column narrower than the ORM** -> FAIL. `String(1024)` against
  `String(length=500)` is a value the ORM accepts and the database rejects.
  `Text` against `String(500)` is the same thing: `Text` states "no cap"
  outright, whereas a bare `String` states nothing at all.
- **a bare `String` against `String(n)`** -> REPORTED, not failed. The ORM made
  no width claim, so the column contradicts nothing. Eleven columns are in this
  bucket and the gate lists them so the caps stay deliberate.
- **a wider column, or a different spelling of the same shape** -> not drift.
  `BYTEA`/`LargeBinary`, `DateTime`/`DateTime(True)`, `JSON`/`JSONB`,
  `BigInteger`/`Integer`, `SmallInteger`/`Integer`.

Two traps that cost real time, both now pinned by the oracle:

- the two sides spell a width differently (`String(255)` on a model,
  `sa.String(length=255)` in a migration). Reading only the positional form
  makes every migration column look unbounded, which silently downgrades every
  narrowed column to benign - the gate would have passed with the bug it exists
  to catch;
- do NOT split a migration on the text `op.create_table(` to find its columns. A
  comment between two column declarations containing an unbalanced bracket ends
  the scan early and the remaining columns look absent - which is what happened
  to `api_keys.key_prefix` and `api_keys.scopes` while vthy's own explanatory
  comments sat inside layer 03's `create_table` call. Both sides are parsed with
  `ast`.

An allowlisted row is printed on every successful run with its reason, and an
entry that no longer corresponds to real drift FAILS the gate. A `benign:` entry
is a triaged claim; anything else must name the bead that owns the open question,
because an allowlist whose entries cannot be re-checked reads as a clean bill of
health.

Oracle: `tests/unit/scripts/test_check_schema_parity.py`.

### `alembic check` stays non-blocking, and that is a decision

CI already runs a real `alembic check`, the only tool here that can see types at
all. It keeps `continue-on-error: true`. Making it blocking would fail the build
on ~395 unexamined diffs and train people to ignore red; what it still sees that
AST cannot is policies, constraints and server defaults, which is the part worth
keeping visible. Its type coverage is no longer the gap -
`scripts/check_schema_parity.py` covers that now. The comment in
`.github/workflows/tests.yml` says so, and a test fails if that comment stops
naming the gate.

### The type rows are measured now, and NULLABILITY is still uncovered (FastAPI_saas-9cmn)

Every row the type gate suppresses has been MEASURED on a real migrated database (`perf-results/RESULT-20261018-9cmn/`, two arms: an `alembic upgrade 07_observability` database against the `create_all` database, read through `select(Model)` and never the raw driver). Four of the six are real defects and two are benign; each `TYPE_ALLOWLIST` entry now names the bead that owns it.

Two results change how a type drift should be read:

1. **A family mismatch can refuse the write, not only the read.** `user_preferences.quiet_hours_start/_end` are `sa.Time(timezone=True)` in layer 05 while the model declares `String(5)`: the ORM's own `INSERT` is refused with `asyncpg.DatatypeMismatchError` **even when both columns are bound NULL**, so `get_or_create` cannot create the row — a stronger failure than `api_keys.scopes`, where the write survived and only the read broke. Fixed in `FastAPI_saas-nqk6` (see the next subsection).
2. **A narrowed column refuses the width the ORM declares.** `files.storage_path` (ORM 1024, column 500), `files.mime_type` (128 / 100), `audit_logs.user_agent` (`Text` / 500) and `passkey_credentials.device_type` (100 / 50) all refuse a value the model considers legal, and the refusal is an untranslated `DBAPIError`. **Fixed in `FastAPI_saas-auz2`**: the two columns that refuse an ordinary value were WIDENED to the ORM declaration — `files.storage_path` to `sa.String(length=1024)` (Azure Blob's maximum blob name) and `audit_logs.user_agent` to `sa.Text()`. `files.mime_type` and `passkey_credentials.device_type` stayed allowlisted: their widest real values are far below the column, so the mismatch cannot refuse a write and renaming the type would buy nothing. See "A narrowed column is fixed by widening the COLUMN, not by shrinking the model" below.

**NULLABILITY is compared by no gate.** `passkey_credentials.credential_id_hash` is nullable in the ORM and `NOT NULL` in migration 02 — found only because a probe insert that omitted it failed on that column. `scripts/check_schema_parity.py` compares names and types; it has no third axis, and that is the next hole rather than a solved one.

The method these two facts came from is worth reusing: a scratch database migrated to the layer that creates the table, foreign keys dropped **in that database only**, one arm per process (the application's engine is a module-level singleton, so re-pointing the environment between two binds silently runs both arms against the same database), and the value read through the application's own ORM. `create_all` derives the schema FROM the ORM, so a development database can never show a migration drift and is not a control — it is the thing that hides them.

### Correcting an applied `create_table` takes TWO files, and only one of them is forced (FastAPI_saas-nqk6)

When the finding is "layer N declares the wrong type", the rule for a schema that has not shipped yet is:

1. **Correct layer N's `create_table`** so a database built from scratch is right and `scripts/check_schema_parity.py` stops reporting the row.
2. **Drop and rebuild the database** (`DROP SCHEMA public CASCADE; CREATE SCHEMA public`, then `uv run alembic -c src/alembic.ini upgrade head`) rather than adding a new revision. Per Package Identity: new schema changes extend the relevant layer's `upgrade()`/`downgrade()` rather than stacking a new dated revision, **unless the layer is already applied in production**. In that case the rule reverses and the fix becomes a correction to layer N **plus** a NEW revision that `ALTER`s the existing column — a corrected `create_table` does **not** rewrite a column that an applied revision already created, so every database that ran layer N keeps the old type forever, silently.

`user_preferences.quiet_hours_start/_end` is the worked example. Layer 05 now declares `sa.String(length=5)` and the database is rebuilt.

Record the decision of **which side moves** in the new revision, not only in the diff. Here the model, the schemas and the consumer were all left alone because a quiet-hours window ("22:00 to 07:00") is not an instant: it has no date, and crossing midnight is the ordinary case, so the string is the right domain shape. Converting the ORM to `Mapped[datetime.time]` would have been the larger change for no gain.

### A narrowed column is fixed by widening the COLUMN, not by shrinking the model (FastAPI_saas-auz2)

The nqk6 rule above answers *which files* to touch. This answers *which side moves*, and the answer is not the symmetric one: when the column is narrower than the ORM declares, **the column moves**. Shrinking the model to `String(500)` would make `scripts/check_schema_parity.py` green and leave the defect intact — a 501-character value is still refused by PostgreSQL with the same untranslated `DBAPIError`, and now nothing in the tree records that the two sides ever disagreed. The gate going quiet is not the same as the bug being fixed, so "make the gate pass" is not an acceptable form of this fix.

The other tell is what the ORM side claims. `audit_logs.user_agent` is `Text` in the model: that is a claim of NO cap, not a forgotten width, and it is corroborated by layer 02, which has always declared the magic-link table's `user_agent` as `sa.Text()`. A `User-Agent` longer than 500 characters is ordinary (proxy chains and enterprise scanners chain four or five of them), and the writer forwards the header verbatim, so a cap that narrow is a live defect, not a theoretical one. `files.storage_path` keeps a bounded width — 1024, the Azure Blob maximum blob name — because a storage key is generated by `storage/service.py` as `tenants/{tenant_id}/files/{uuid}.{ext}` and its length is the module's decision, not a client's.

Two consequences worth stating because they look like contradictions and are not:

- **The oracle asserts the database ACCEPTS a value the ORM accepts.** An oracle that only asserts "the two declarations agree" would be satisfied by the shrink and would have passed against the defect. `tests/unit/migrations/test_width_parity_storage_and_audit.py` therefore drives a 501- and a 4096-character `User-Agent` at the declared column type and requires that neither be refused.
- **Both columns were corrected in the layer that creates them, not by a new revision.** The head is still `08_enable_rls_policies`; a layer-09 `ALTER` would have broken two existing oracles that assert the head (`tests/unit/migrations/test_user_preferences_quiet_hours_parity.py`), and `scripts/check_migration_graph.py` enforces one linear head. The database is rebuilt per the two-file rule above. The oracle pins that shape too, so a later reader who reaches for `alter_column()` fails the test rather than silently creating a second convention.

Write `op.execute` statements **literally, not in a loop**. A migration is read by a human during an incident and grepped by the oracle; a loop hides both the table and the cast.

**Revision ids must fit alembic's `alembic_version.version_num VARCHAR(32)`.** A 41-character id made the entire `alembic upgrade head` **roll back** (not just fail the head) because alembic runs the chain in a single transaction — measured in a discarded first attempt, recorded in `tests/unit/migrations/`. Keep ids at 32 characters or shorter; `09_passkey_and_quiet_hours` (27) is the precedent.

`device_type` on `passkey_credentials` was tightened from nullable to NOT NULL in layer 02's `create_table` (not by a new revision) per the same extend-the-layer rule. The ORM already declared it `nullable=False`; the database was the loose side.

## Pre-PR Checks

### Migration Testing
```bash
# Test migration generation
uv run alembic revision --autogenerate -m "Test migration"

# Review generated SQL
uv run alembic upgrade head --sql

# Test migration on development database
uv run alembic upgrade head

# Test rollback
uv run alembic downgrade -1
```

### Database Validation
```bash
# Check database schema
uv run python -c "
from src.app.core.db.database import async_engine
import asyncio

async def check_schema():
    async with async_engine.begin() as conn:
        result = await conn.execute(sa.text(\"SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'\"))
        print([row[0] for row in result])

asyncio.run(check_schema())
"
```

### Model Validation
```bash
# Check model definitions
uv run mypy src/app/modules/*/models.py

# Validate foreign key relationships
uv run python -c "
from src.app.modules.users.models import User
from src.app.modules.tiers.models import Tier
print('User model loaded successfully')
print('Tier model loaded successfully')
"
```

### Migration Best Practices
```bash
# Review migration before applying
cat migrations/versions/<latest_migration>.py

# Check migration dependencies
uv run alembic history

# Verify current database state
uv run alembic current
```
