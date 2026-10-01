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