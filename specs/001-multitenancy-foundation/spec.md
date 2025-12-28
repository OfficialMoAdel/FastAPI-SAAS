# Feature Specification: Multi-tenancy Foundation (Phase 1)

**Feature Branch**: `001-multitenancy-foundation`  
**Created**: 2025-12-28  
**Status**: Draft  
**Input**: User description: "Establish a robust, mandatory, header-based multi-tenancy foundation without breaking existing system health checks, API documentation, or existing data."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Tenant Context Resolution (Priority: P1)

As an API consumer, I need my tenant to be automatically identified from the `X-Tenant-ID` header so that all my requests are scoped to my organization's data.

**Why this priority**: This is the core multi-tenancy mechanism. Without tenant resolution, no tenant-scoped operations are possible. This enables all downstream tenant-aware features.

**Independent Test**: Can be fully tested by sending requests with/without the X-Tenant-ID header and verifying the system responds correctly (400 for missing, 403 for invalid, success for valid).

**Acceptance Scenarios**:

1. **Given** a valid X-Tenant-ID header, **When** I make an API request, **Then** my request is processed with the correct tenant context
2. **Given** a missing X-Tenant-ID header on a protected endpoint, **When** I make an API request, **Then** I receive a 400 Bad Request error with a clear message
3. **Given** an invalid or inactive tenant slug in X-Tenant-ID, **When** I make an API request, **Then** I receive a 403 Forbidden error
4. **Given** a request to a whitelisted path (docs, health, openapi), **When** I make an API request without X-Tenant-ID, **Then** the request succeeds without tenant validation

---

### User Story 2 - Tenant Administration (Priority: P2)

As an authenticated user, I need to create and manage tenants so that I can onboard new organizations to the platform.

**Why this priority**: Once tenant resolution works, administrators need the ability to create tenants. Without this, only the default system tenant would exist.

**Independent Test**: Can be fully tested by calling tenant CRUD endpoints as an authenticated user and verifying tenants are created, listed, updated, and deactivated correctly.

**Authentication**: Any authenticated user can access tenant management endpoints in Phase 1. Fine-grained RBAC will be added in Phase 2.

**Acceptance Scenarios**:

1. **Given** valid tenant details (name, slug), **When** I create a tenant, **Then** a new active tenant is created with the provided information
2. **Given** a duplicate slug, **When** I attempt to create a tenant, **Then** I receive an error indicating the slug must be unique
3. **Given** an existing tenant, **When** I deactivate it, **Then** API requests using that tenant's slug return 403 Forbidden
4. **Given** a deactivated tenant, **When** I reactivate it, **Then** API requests using that tenant's slug succeed again
5. **Given** the tenants list endpoint, **When** I request all tenants, **Then** I receive a paginated list of all tenants with their status

---

### User Story 3 - User-Tenant Association (Priority: P3)

As a system administrator, I need existing users to be automatically associated with a default tenant during migration so that the system remains functional for current users.

**Why this priority**: This ensures backward compatibility. Existing users must not lose access when multi-tenancy is enabled.

**Independent Test**: Can be tested by running the migration and verifying all existing users have a valid tenant_id pointing to the default "System" tenant.

**Acceptance Scenarios**:

1. **Given** existing users without tenant association, **When** the migration runs, **Then** all users are assigned to the default "System" tenant
2. **Given** a user with a tenant association, **When** querying the user, **Then** the tenant relationship is accessible
3. **Given** the same email address, **When** creating users in different tenants, **Then** both users can exist (email uniqueness is per-tenant)

---

### Edge Cases

- What happens when the X-Tenant-ID header is present but empty? → Treated as missing, returns 400
- How does the system handle malformed tenant slugs (special characters)? → Returns 403 (tenant not found)
- What happens if the default "System" tenant is deactivated? → System health endpoints remain accessible, but tenant-scoped operations fail
- How are concurrent requests handled for tenant context? → Each request maintains isolated tenant context via async-safe context variables

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST validate the `X-Tenant-ID` header on all requests except whitelisted paths
- **FR-002**: System MUST return HTTP 400 when X-Tenant-ID header is missing on protected endpoints
- **FR-003**: System MUST return HTTP 403 when X-Tenant-ID contains an invalid or inactive tenant slug
- **FR-004**: System MUST whitelist the following paths from tenant validation: `/docs`, `/redoc`, `/openapi.json`, `/api/v1/health`, `/api/v1/ready`
- **FR-005**: System MUST provide a mechanism for services to access the current tenant context
- **FR-006**: System MUST isolate tenant context per request to prevent data leakage in concurrent/async scenarios
- **FR-007**: System MUST support tenant creation with name (required), slug (required, unique), and active status
- **FR-008**: System MUST enforce unique tenant slugs across the platform
- **FR-009**: System MUST associate each user with exactly one tenant via a mandatory relationship
- **FR-010**: System MUST enforce email uniqueness per tenant (same email can exist in different tenants)
- **FR-011**: System MUST migrate existing users to a default "System" tenant without data loss
- **FR-012**: System MUST track tenant creation and modification timestamps
- **FR-013**: System MUST require user authentication for all tenant management endpoints (create, update, deactivate, reactivate)
- **FR-014**: System MUST allow reactivation of deactivated tenants (reversible soft-delete)
- **FR-015**: System MUST clear tenant context in middleware finally block after response completes
- **FR-016**: System MUST return generic "Access denied" (403) for both invalid and inactive tenant slugs to prevent enumeration
- **FR-017**: System MUST return 403 with message "Tenant inactive. Contact support." when authenticated user's tenant is deactivated

> **Note on FR-016 vs FR-017**: These requirements address different scenarios:
> - **FR-016** applies during **header validation** (middleware) when any request uses an unknown or inactive tenant slug in X-Tenant-ID. The generic message prevents tenant enumeration attacks.
> - **FR-017** applies to **authenticated users** whose own tenant becomes deactivated while they have a valid session. They receive a specific message because their identity is already known (no enumeration risk).

- **FR-018**: System MUST log tenant lifecycle events (create, deactivate, reactivate) with timestamp and actor to application logs

### Key Entities

- **Tenant**: Represents an organization/customer in the multi-tenant system. Key attributes: identifier, display name, unique slug (used in X-Tenant-ID header), active status, creation/modification timestamps
- **User-Tenant Relationship**: Links users to their tenant. A user belongs to exactly one tenant. Email uniqueness is scoped to the tenant.

## Assumptions

- The existing User entity has an email field that is currently globally unique
- The system uses an existing health check endpoint at `/api/v1/health`
- The application uses middleware capable of intercepting all requests
- Async context variables are available for request-scoped state management
- A default "System" tenant with slug "host" will serve as the fallback for existing users

## Clarifications

### Session 2025-12-28

- Q: Should tenant management endpoints require authentication in Phase 1? → A: Any authenticated user can manage tenants (minimal auth foundation for Phase 2 RBAC)
- Q: Can a deactivated tenant be reactivated? → A: Yes, deactivated tenants can be reactivated (reversible soft-delete)

### Gap Resolutions 2025-12-28

- **Migration Rollback**: Alembic transaction auto-rolls back on failure. Manual rollback: drop FK, drop tenant_id column, drop tenants table.
- **ContextVar Cleanup**: Middleware clears contextvar in finally block after response. Verified by testing context is None after request.
- **Slug Enumeration Prevention**: Return generic 403 "Access denied" for both invalid and inactive tenants (no details exposed).
- **Deactivated Tenant Users**: Return 403 with "Tenant inactive. Contact support." for users whose tenant is deactivated.
- **Audit Logging (Phase 1)**: Log tenant lifecycle events to application logs. Structured audit table deferred to future phase.

## Out of Scope

- Role-Based Access Control (RBAC) - deferred to Phase 2
- Feature flags per tenant - deferred to future phase
- Frontend/UI components - backend only
- Subdomain-based tenant routing - header-based only per constitution
- Tenant deletion (soft-delete via is_active flag only)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All protected API requests without valid X-Tenant-ID header are rejected within 50ms
- **SC-002**: 100% of existing users are associated with the default tenant after migration with zero data loss
- **SC-003**: System health and documentation endpoints remain accessible without authentication or tenant headers
- **SC-004**: Tenant context resolution adds less than 5ms latency to request processing
- **SC-005**: Email uniqueness constraint allows same email across different tenants while preventing duplicates within a tenant
- **SC-006**: System handles 1000 concurrent requests with correct tenant isolation (no context leakage)
- **SC-007**: Users of deactivated tenants receive clear error message ("Tenant inactive. Contact support.") within 50ms
