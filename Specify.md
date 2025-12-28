# Mission: Phase 1 - Multi-tenancy Foundation

## Goal
Establish a robust, mandatory, header-based multi-tenancy foundation in PostgreSQL without breaking existing system health checks, API documentation, or existing data.

## Scope (STRICT)
- Tenant Core Infrastructure only.
- NO RBAC implementation yet.
- NO Feature Flags yet.
- NO Frontend work.

## External Documentation References (Logic Only)
The following links describe business logic and architectural ideas (Mongo-based). 
Adapt them strictly to **PostgreSQL + SQLAlchemy + Header-based** architecture.

- Multi-Tenancy Logic: https://r.jina.ai/https://zread.ai/sajanv88/full_stack_fastapi_react_template/12-tenant-data-isolation-strategies
- Middleware Logic: https://r.jina.ai/https://zread.ai/sajanv88/full_stack_fastapi_react_template/14-tenant-middleware-implementation

## Tasks

1. **Tenant Domain Module**
   - Create `src/app/modules/tenants/` (models, schemas, crud, service, router).
   - Define SQLAlchemy model `Tenant`:
     - `id` (Integer/UUID, match project style).
     - `name` (String, Unique).
     - `slug` (String, Unique, Indexed) -> The identifier used in X-Tenant-ID.
     - `is_active` (Boolean, default True).
     - `created_at`, `updated_at` (DateTime).

2. **Tenant Context**
   - Create `src/app/core/tenancy/context.py`.
   - Implement a `contextvar` to hold the resolved Tenant ORM object.
   - Ensure context is reset/cleared after request lifecycle to prevent leakage in async workers.

3. **Mandatory Tenant Middleware**
   - Create `src/app/middleware/tenant.py`.
   - **Whitelist Configuration**: Define strictly:
     `["/docs", "/redoc", "/openapi.json", "/api/v1/health", "/api/v1/ready"]`
   - **Logic**:
     1. Check Whitelist: If path matches, skip validation.
     2. Extract Header: `X-Tenant-ID`.
     3. Missing Header: Raise HTTP 400.
     4. DB Lookup: Fetch active tenant by `slug`.
     5. Invalid/Inactive: Raise HTTP 403.
     6. Context Set: Store tenant in `contextvar`.

4. **User–Tenant Relationship (Schema)**
   - Modify `src/app/models/user.py`.
   - Add `tenant_id` column (ForeignKey -> `tenants.id`).
   - Define uniqueness as Composite: `UniqueConstraint('email', 'tenant_id')`.
   - *Note on constraints:* See Migration task below for implementation strategy.

5. **Dependency Utilities**
   - Add `get_current_tenant()` in `src/app/api/dependencies.py`.
   - It must return the Tenant object from the `contextvar`.
   - Raise `HTTP 500` if accessed outside of a valid context.

6. **Safe Database Migration (CRITICAL)**
   - Generate a single Alembic migration file that performs these steps IN ORDER:
     1. Create `tenants` table.
     2. **Data Migration**: Insert a default "System" tenant (slug=`host`, name=`System`).
     3. Add `tenant_id` column to `user` table as **Nullable** initially.
     4. **Backfill**: Update all existing users to have `tenant_id` = System Tenant ID.
     5. Apply `NOT NULL` constraint to `tenant_id`.
     6. Create the Foreign Key constraint.
     7. Create the Composite Unique Index on (email, tenant_id).

7. **Registration**
   - Register the new Tenant Router in `src/app/main.py` (e.g., `/api/v1/tenants`).
   - Register the Tenant Middleware in `src/app/main.py`.

## Output Required
- Full Tenant module code.
- Middleware code.
- Updated User model.
- `main.py` modifications.
- One robust Alembic migration file handling the data backfill.