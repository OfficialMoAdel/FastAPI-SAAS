# Data Model: Multi-tenancy Foundation

**Feature**: 001-multitenancy-foundation  
**Date**: 2025-12-28

## Entity Relationship Diagram

```
┌─────────────────────────────────────┐
│              Tenant                 │
├─────────────────────────────────────┤
│ id: int (PK, auto)                  │
│ name: str (required)                │
│ slug: str (unique, indexed)         │
│ is_active: bool (default: true)     │
│ created_at: datetime (auto)         │
│ updated_at: datetime (nullable)     │
└───────────────┬─────────────────────┘
                │
                │ 1:N
                │
┌───────────────┴─────────────────────┐
│               User                  │
├─────────────────────────────────────┤
│ id: int (PK, auto)                  │
│ tenant_id: int (FK -> Tenant.id)    │  ← NEW
│ name: str                           │
│ username: str (unique)              │
│ email: str                          │
│ hashed_password: str                │
│ ...existing fields...               │
├─────────────────────────────────────┤
│ UNIQUE(email, tenant_id)            │  ← MODIFIED
└─────────────────────────────────────┘
```

## Entities

### Tenant (NEW)

Represents an organization/customer in the multi-tenant system.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | `int` | PK, auto-increment | Internal identifier |
| `name` | `str(100)` | NOT NULL | Display name for the tenant |
| `slug` | `str(50)` | NOT NULL, UNIQUE, INDEXED | URL-safe identifier used in X-Tenant-ID header |
| `is_active` | `bool` | NOT NULL, DEFAULT TRUE | Soft-delete flag; inactive tenants rejected by middleware |
| `created_at` | `datetime(tz)` | NOT NULL, auto | Creation timestamp |
| `updated_at` | `datetime(tz)` | NULLABLE | Last modification timestamp |

**Validation Rules**:
- `name`: 1-100 characters, non-empty after trim
- `slug`: 3-50 characters, lowercase alphanumeric with hyphens, must start/end with alphanumeric
- `slug` reserved values: `host`, `admin`, `api`

**Indexes**:
- Primary key on `id`
- Unique index on `slug`

### User (MODIFIED)

Existing user entity with added tenant relationship.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `tenant_id` | `int` | NOT NULL, FK → Tenant.id, INDEXED | **NEW**: Owning tenant |

**Modified Constraints**:
- Remove: `UNIQUE(email)` (global uniqueness)
- Add: `UNIQUE(email, tenant_id)` (per-tenant uniqueness)

**Indexes**:
- Add index on `tenant_id` for tenant-scoped queries
- Add composite unique index on `(email, tenant_id)`

## State Transitions

### Tenant Lifecycle

```
[Created] ──is_active=true──► [Active] ◄──reactivate──┐
                                 │                     │
                                 │                     │
                            deactivate                 │
                                 │                     │
                                 ▼                     │
                            [Inactive] ───────────────┘
```

| State | `is_active` | API Access | Can Modify |
|-------|-------------|------------|------------|
| Active | `true` | Allowed | Yes |
| Inactive | `false` | Rejected (403) | Yes (reactivate) |

## Migration Strategy

### Pre-Migration State
- `user` table has `email` with global unique constraint
- No `tenants` table exists

### Migration Steps (Single Transaction)

1. **Create tenants table**
   ```sql
   CREATE TABLE tenants (
       id SERIAL PRIMARY KEY,
       name VARCHAR(100) NOT NULL,
       slug VARCHAR(50) NOT NULL,
       is_active BOOLEAN NOT NULL DEFAULT TRUE,
       created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       updated_at TIMESTAMPTZ
   );
   CREATE UNIQUE INDEX ix_tenants_slug ON tenants (slug);
   ```

2. **Insert default tenant**
   ```sql
   INSERT INTO tenants (name, slug, is_active, created_at)
   VALUES ('System', 'host', TRUE, NOW());
   ```

3. **Add nullable tenant_id to user**
   ```sql
   ALTER TABLE "user" ADD COLUMN tenant_id INTEGER;
   ```

4. **Backfill existing users**
   ```sql
   UPDATE "user" SET tenant_id = (SELECT id FROM tenants WHERE slug = 'host');
   ```

5. **Make tenant_id NOT NULL**
   ```sql
   ALTER TABLE "user" ALTER COLUMN tenant_id SET NOT NULL;
   ```

6. **Add foreign key constraint**
   ```sql
   ALTER TABLE "user" 
   ADD CONSTRAINT fk_user_tenant 
   FOREIGN KEY (tenant_id) REFERENCES tenants(id);
   ```

7. **Drop old email unique constraint**
   ```sql
   DROP INDEX IF EXISTS ix_user_email;
   ALTER TABLE "user" DROP CONSTRAINT IF EXISTS user_email_key;
   ```

8. **Create composite unique index**
   ```sql
   CREATE UNIQUE INDEX ix_user_email_tenant ON "user" (email, tenant_id);
   ```

### Post-Migration State
- All existing users have `tenant_id` pointing to "System" tenant (slug="host")
- New users require explicit tenant assignment
- Email uniqueness is per-tenant

### Rollback Strategy

**Automatic Rollback**: Alembic wraps migration in a transaction. If any step fails, the database automatically rolls back to pre-migration state.

**Manual Rollback** (if needed post-completion):
```sql
-- Step 1: Drop composite unique index
DROP INDEX IF EXISTS ix_user_email_tenant;

-- Step 2: Recreate simple unique index on email
-- WARNING: May fail if duplicate emails exist across tenants
CREATE UNIQUE INDEX ix_user_email ON "user" (email);

-- Step 3: Drop FK constraint
ALTER TABLE "user" DROP CONSTRAINT IF EXISTS fk_user_tenant;

-- Step 4: Drop tenant_id column
ALTER TABLE "user" DROP COLUMN IF EXISTS tenant_id;

-- Step 5: Drop tenants table
DROP TABLE IF EXISTS tenants;
```

**Rollback Risk**: Step 2 will fail if the same email was registered in multiple tenants after migration. Manual data cleanup required before rollback in that case.

## Data Volume Assumptions

| Entity | Expected Initial | Expected 1 Year | Query Patterns |
|--------|------------------|-----------------|----------------|
| Tenant | 1 (System) | 100-1000 | Lookup by slug (every request) |
| User | Existing count | 10x existing | Filter by tenant_id |

## Caching Considerations

- **Tenant by slug**: Consider Redis cache for frequently accessed tenants
- **Cache invalidation**: On tenant update/deactivate, invalidate cache entry
- **TTL**: 5-minute TTL recommended to balance freshness vs performance

*Note: Caching implementation is optional for Phase 1, can be added as optimization.*
