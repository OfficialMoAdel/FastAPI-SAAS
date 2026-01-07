# Feature Specification: Storage, RBAC & Audit Log Enhancements

**Feature Branch**: `007-storage-rbac-audit`  
**Created**: 2026-01-07  
**Status**: Draft  
**Input**: User description: "Implement Phase 4 (File Storage), Phase 6 (RBAC Enhancements), and Phase 7 (Audit Logs Enhancements) from the migration plan"

---

## Overview

This feature consolidates three related phases from the backend migration plan:

1. **Phase 4 - File Storage**: Create storage_settings and files modules to enable file upload/download with S3, Azure, and local storage providers
2. **Phase 6 - RBAC Enhancements**: Extend the permission system with new granular permissions for billing, storage, and audit management
3. **Phase 7 - Audit Logs Enhancements**: Add export and download capabilities for audit logs

### Existing Foundation

The project already has:
- `storage/` module with `StorageProvider` interface and `S3Storage` implementation
- `rbac/` module with role/permission models and basic permissions (USERS_READ, USERS_MANAGE, ROLES_READ, ROLES_MANAGE, ROLES_ASSIGN, FEATURE_FLAGS_MANAGE, NOTIFICATIONS_MANAGE)
- `audit_logs/` module with basic list/get endpoints

---

## Clarifications

### Session 2026-01-07

- Q: How are public files accessed? → A: Signed URLs with configurable expiration (e.g., 1 hour)
- Q: What happens when storage provider fails during upload? → A: Fail fast with clear error message (no automatic retry)
- Q: Are there per-tenant storage limits? → A: Both file count limit and total storage quota per tenant (configurable)
- Q: What observability signals for storage operations? → A: Full observability with metrics and distributed tracing
- Q: What if audit export exceeds 100,000 records? → A: Enforce maximum 100,000 per export; require date filtering for larger sets

### Session 2026-01-07 (continued)

- Q: What if user uploads without storage settings configured? → A: Return 400 Bad Request with message: "Storage not configured. Contact your tenant administrator."
- Q: What happens to files when a user is deleted? → A: SET NULL on user_id, then transfer ownership to tenant admin
- Q: Should storage credentials be validated before saving? → A: Yes, validate credentials on save (test connection, fail if invalid)
- Q: What if upload partially completes (storage OK, DB fails)? → A: Cleanup on failure - delete stored object before returning error
- Q: Encryption key rotation requirements? → A: Support key rotation - decrypt with old/new key, encrypt with new on next update

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - File Upload and Download (Priority: P1)

As a tenant user, I want to upload files to my tenant's storage and download them later so that I can share documents and assets within my organization.

**Why this priority**: File storage is a core infrastructure feature that many other features depend on (profile images, attachments, document management).

**Independent Test**: Upload a file via the API endpoint and verify it can be downloaded. Delete the file and verify it's removed.

**Acceptance Scenarios**:

1. **Given** an authenticated user with file upload permissions, **When** they upload a file (image/PDF), **Then** the file is stored and metadata is saved to the database with tenant isolation
2. **Given** an uploaded file exists, **When** the owner requests to download it, **Then** they receive the file content with correct MIME type
3. **Given** an uploaded file belongs to tenant A, **When** a user from tenant B attempts to access it, **Then** they receive a 404 error (tenant isolation)
4. **Given** a file type not in allowed list, **When** user attempts upload, **Then** the system rejects with appropriate error message
5. **Given** a file exceeds size limit, **When** user attempts upload, **Then** the system rejects with size limit error

---

### User Story 2 - Storage Provider Configuration (Priority: P1)

As a tenant administrator, I want to configure storage settings (S3, Azure, or local) for my tenant so that files are stored according to our infrastructure requirements.

**Why this priority**: Required before file uploads can work; determines where files are physically stored.

**Independent Test**: Create or update storage settings for a tenant and verify the configuration is persisted. Subsequent file uploads should use the configured provider.

**Acceptance Scenarios**:

1. **Given** a tenant without storage settings, **When** admin creates settings with S3 provider, **Then** the settings are saved with encrypted credentials
2. **Given** existing storage settings, **When** admin updates the provider from S3 to Azure, **Then** the settings are updated (existing files remain accessible via old provider)
3. **Given** storage settings are configured, **When** a file is uploaded, **Then** the file is stored using the tenant's configured provider
4. **Given** a tenant admin, **When** they view storage settings, **Then** sensitive fields (access keys, secrets) are masked in the response

---

### User Story 3 - Extended Permission Management (Priority: P2)

As a system administrator, I want granular permissions for billing, storage, and audit log management so that I can delegate responsibilities while maintaining security.

**Why this priority**: Enables role-based access control for the new storage and audit export features. Essential for enterprise customers.

**Independent Test**: Assign the new permissions to a role, assign role to user, verify user can access protected endpoints.

**Acceptance Scenarios**:

1. **Given** a user without MANAGE_STORAGE_SETTINGS permission, **When** they attempt to update storage settings, **Then** they receive 403 Forbidden
2. **Given** a user with AUDIT_LOGS_DOWNLOAD permission, **When** they export audit logs, **Then** the export is allowed
3. **Given** a user with USER_SELF_READ_AND_WRITE_ONLY permission, **When** they access their own profile, **Then** access is granted; accessing other profiles is denied
4. **Given** a role with new permissions assigned, **When** the role is assigned to a user, **Then** the user inherits all the permissions correctly

---

### User Story 4 - Audit Log Export (Priority: P2)

As a compliance officer, I want to export audit logs in JSONL or CSV format so that I can analyze them externally and meet regulatory requirements.

**Why this priority**: Critical for compliance (SOC2, GDPR) and security auditing. Builds on existing audit log infrastructure.

**Independent Test**: Call the export endpoint with date filters and verify a downloadable file is returned with correct format.

**Acceptance Scenarios**:

1. **Given** audit logs exist for a tenant, **When** user with AUDIT_LOGS_DOWNLOAD permission requests export, **Then** they receive a streaming file download
2. **Given** export request with date range, **When** processed, **Then** only logs within that range are included
3. **Given** export format specified as CSV, **When** processed, **Then** the file is in valid CSV format with headers
4. **Given** export format specified as JSONL, **When** processed, **Then** each line contains valid JSON for one log entry
5. **Given** user without AUDIT_LOGS_DOWNLOAD permission, **When** they attempt export, **Then** they receive 403 Forbidden

---

### Edge Cases

- Storage provider unavailable during upload: Fail fast with clear error message; user can retry manually
- How does the system handle concurrent uploads of files with same name? (UUID-based filenames)
- What happens when export request exceeds memory limits? (Streaming response)
- How are storage credentials rotated? (Update settings without downtime)
- What happens when local storage disk is full? (Error handling with clear message)

---

## Requirements *(mandatory)*

### Functional Requirements

#### Phase 4 - File Storage

- **FR-001**: System MUST allow tenants to configure storage provider settings (S3, Azure Blob, Local filesystem)
- **FR-002**: System MUST encrypt sensitive storage credentials (access keys, secrets) before database storage
- **FR-003**: System MUST enforce file size limits configurable per tenant (default: 100MB)
- **FR-004**: System MUST restrict allowed file types configurable per tenant (default: images, PDFs)
- **FR-005**: System MUST store file metadata (filename, size, MIME type, upload time, user ID) with tenant isolation
- **FR-006**: System MUST support file download with streaming response
- **FR-007**: System MUST support file deletion (both storage and database records)
- **FR-008**: System MUST provide file metadata endpoint without downloading content
- **FR-009**: System MUST support public/private file visibility toggle; public files accessed via signed URLs with configurable expiration (default: 1 hour)
- **FR-010**: System MUST use existing `StorageProvider` interface for all storage operations
- **FR-010a**: System MUST enforce configurable file count limit per tenant (default: 10,000 files)
- **FR-010b**: System MUST enforce configurable total storage quota per tenant (default: 10GB)
- **FR-010c**: System MUST emit metrics for upload/download counts, file sizes, and error rates
- **FR-010d**: System MUST support distributed tracing for all storage operations

#### Phase 6 - RBAC Enhancements

- **FR-011**: System MUST add USER_SELF_READ_AND_WRITE_ONLY permission for self-access only
- **FR-012**: System MUST add USER_DELETE_ONLY permission for user deletion capability
- **FR-013**: System MUST add USER_ROLE_ASSIGN_OR_REMOVE_ONLY permission for role assignment
- **FR-014**: System MUST add MANAGE_BILLING permission for billing operations
- **FR-015**: System MUST add MANAGE_PRODUCTS_AND_PRICING permission for product management
- **FR-016**: System MUST add MANAGE_STORAGE_SETTINGS permission for storage configuration
- **FR-017**: System MUST add AUDIT_LOGS_VIEW_ONLY permission for read-only audit access
- **FR-018**: System MUST add AUDIT_LOGS_DOWNLOAD permission for export capability

#### Phase 7 - Audit Logs Enhancements

- **FR-019**: System MUST provide audit log export endpoint supporting JSONL and CSV formats
- **FR-020**: System MUST support date range filtering for exports
- **FR-021**: System MUST stream export responses to handle large datasets (max 100,000 records per export)
- **FR-021a**: System MUST reject export requests exceeding 100,000 records with guidance to narrow date range
- **FR-022**: System MUST require AUDIT_LOGS_DOWNLOAD permission for export access
- **FR-023**: System MUST log audit export events to the audit log itself

### Key Entities

#### StorageSettings
- Represents per-tenant storage configuration
- Attributes: tenant_id (FK), provider (enum), S3 settings, Azure settings, local path, file size limit, allowed types, max_file_count, storage_quota_bytes, is_active

#### File
- Represents an uploaded file's metadata
- Attributes: id, user_id (FK), tenant_id (FK), filename, original_filename, file_path, mime_type, file_size, storage_provider, is_public, created_at, updated_at

#### Permission (Extended)
- Extended enum with new permission values for billing, storage, and audit operations

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can upload files up to 100MB and download them within 5 seconds (local network)
- **SC-002**: Tenant isolation is enforced - 0% cross-tenant file access in security testing
- **SC-003**: Audit log export completes within 30 seconds for up to 100,000 records
- **SC-004**: New RBAC permissions correctly gate all protected endpoints with 100% coverage
- **SC-005**: Storage settings can be configured without service restart (hot reload)
- **SC-006**: All file operations are logged to audit trail for compliance
- **SC-007**: 100% of unit and integration tests pass for new modules
- **SC-008**: File metadata queries complete in under 100ms for lists up to 1000 files

---

## Assumptions

1. The existing `StorageProvider` interface is sufficient and does not need modification
2. S3 storage implementation already exists and is tested
3. Azure Blob and Local storage implementations will follow the same interface
4. Database connection pooling is already properly configured
5. Rate limiting is already in place and will apply to new endpoints
6. Encryption key for credential storage is available via `settings.ENCRYPTION_KEY`
7. The `_base` module pattern exists and should be followed for new modules
8. Tenant isolation patterns from existing modules will be reused
