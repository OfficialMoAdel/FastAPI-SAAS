# Phase 0: Research & Key Decisions

## 1. Role-Permission Association Strategy

**Decision**: Normalized `role_permissions` Table.

**Rationale**:
- **Referential Integrity**: Database enforces valid foreign keys.
- **Query Efficiency**: Efficiently finding "all roles with permission X" or "all permissions for role Y" via JOINs.
- **Consistency**: Matches existing `user_roles` pattern.
- **Scalability**: Better than JSON columns for complex queries or updates.

**Alternatives Considered**:
- **JSON Column**: Rejected due to inability to easily enforce FK constraints and complexity in "reverse" lookups (finding roles by permission).

## 2. Cache Invalidation Strategy

**Decision**: Immediate Event-Driven Invalidation.

**Rationale**:
- **Security**: Security changes (e.g., revoking a role) must take effect immediately.
- **Mechanism**: When a `Role` or `UserRole` record is modified/deleted, the corresponding cache keys (e.g., `user:{id}:permissions`) are explicitly deleted.
- **Performance**: Valid permissions are cached for fast lookups (< 100ms), but writes incur a small penalty to clear cache.

**Alternatives Considered**:
- **TTL Only**: Rejected because a 5-minute window of vulnerability after role revocation is unacceptable for this system's security standards.

## 3. Data Migration & Seeding

**Decision**: Raw SQL / `op.bulk_insert` in Alembic.

**Rationale**:
- **Safety**: Avoids importing application models during migration execution, preventing circular dependency crashes and version mismatches.
- **Scope**: Must seed `Permissions` (Enum) AND default System Roles (`Admin`, `Member`) to ensure a working baseline state.

## 4. Multi-Tenancy Implementation

**Decision**: Row-Level Security via `tenant_id` column + Application-Level filtering.

**Rationale**:
- **Strict Isolation**: Every RBAC entity (`Role`, `UserRole`) MUST have a `tenant_id`.
- **Global Exceptions**: `Permission` entity is global (system-wide definitions), so it does not have `tenant_id`.
- **Enforcement**: Middleware/Dependency extracts `X-Tenant-ID` and Service layer filters `WHERE tenant_id = :current_tenant_id`.
