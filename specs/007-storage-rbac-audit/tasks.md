# Implementation Tasks: Storage, RBAC & Audit Log Enhancements

**Branch**: `007-storage-rbac-audit` | **Date**: 2026-01-07
**Plan**: [plan.md](file:///d:/project/FastAPI_saas/specs/007-storage-rbac-audit/plan.md)
**Spec**: [spec.md](file:///d:/project/FastAPI_saas/specs/007-storage-rbac-audit/spec.md)

---

## Task Summary

| Phase | Description | Tasks | Parallel |
|-------|-------------|-------|----------|
| 1 | Setup & Dependencies | 4 | 2 |
| 2 | Foundational (Shared) | 6 | 4 |
| 3 | US1: Storage Settings | 8 | 4 |
| 4 | US2: File Upload | 9 | 4 |
| 5 | US3: File Management | 6 | 3 |
| 6 | US4: RBAC Permissions | 5 | 2 |
| 7 | US5: Audit Export | 6 | 2 |
| 8 | Polish & Integration | 4 | 0 |
| **Total** | | **48** | **21** |

---

## Phase 1: Setup & Dependencies

> **Goal**: Project setup and dependency installation

- [x] T001 Add `azure-storage-blob>=12.0.0` to `pyproject.toml` dependencies
- [x] T002 [P] Add `aiofiles>=23.0.0` to `pyproject.toml` dependencies
- [x] T003 [P] Add `opentelemetry-api>=1.20.0` to `pyproject.toml` dependencies
- [x] T004 Run `pip install -e .` to install new dependencies

---

## Phase 2: Foundational (Shared Infrastructure)

> **Goal**: Create shared components blocking all user stories
> **Dependencies**: Phase 1 complete

### Storage Provider Interfaces

- [x] T005 [P] Create `src/app/modules/storage/azure_storage.py` implementing `StorageProvider` interface
- [x] T006 [P] Create `src/app/modules/storage/local_storage.py` implementing `StorageProvider` interface
- [x] T007 [P] Create `src/app/core/encryption.py` with Fernet encryption helpers (encrypt/decrypt + key rotation)

### RBAC Extensions

- [x] T008 [P] Add 8 new permissions to `src/app/core/rbac/permissions.py`:
  - `MANAGE_BILLING`, `MANAGE_PRODUCTS`, `MANAGE_STORAGE_SETTINGS`
  - `AUDIT_LOGS_VIEW_ONLY`, `AUDIT_LOGS_DOWNLOAD`
  - `FILES_READ`, `FILES_WRITE`, `FILES_DELETE`

### Unit Tests (Foundational)

- [x] T009 Create `tests/unit/test_azure_storage.py` with mocked Azure SDK
- [x] T010 Create `tests/unit/test_local_storage.py` with temp directory

---

## Phase 3: US1 - Storage Settings Configuration

> **User Story**: "As a Tenant Admin, I can configure storage provider settings for my tenant"
> **Priority**: P1 (highest - blocks file operations)
> **Test Criteria**: Admin can create, view, update storage settings via API

### Models & Schemas

- [x] T011 [P] [US1] Create `src/app/modules/storage_settings/__init__.py` module init
- [x] T012 [P] [US1] Create `src/app/modules/storage_settings/models.py` with `StorageSettings` model:
  - Fields: `id`, `tenant_id` (unique FK), `provider` (enum), credentials (encrypted BYTEA)
  - Config: `max_file_size_mb`, `allowed_file_types`, `max_file_count`, `storage_quota_bytes`

- [x] T013 [P] [US1] Create `src/app/modules/storage_settings/schemas.py` with Pydantic DTOs:
  - `StorageSettingsCreate`, `StorageSettingsUpdate`, `StorageSettingsRead`
  - Mask sensitive fields in response

### Service Layer

- [x] T014 [US1] Create `src/app/modules/storage_settings/service.py`:
  - `create_settings()` - validate credentials before save, encrypt credentials
  - `get_settings()` - decrypt for internal use, mask for response
  - `update_settings()` - re-validate credentials, support key rotation

### API Endpoints

- [x] T015 [US1] Create `src/app/modules/storage_settings/router.py`:
  - `POST /storage-settings` - create (MANAGE_STORAGE_SETTINGS required)
  - `GET /storage-settings` - get current tenant settings
  - `PATCH /storage-settings` - update settings

- [x] T016 [US1] Register router in `src/app/api/v1/router.py`

### Tests

- [ ] T017 [P] [US1] Create `tests/unit/test_storage_settings_service.py`
- [ ] T018 [US1] Create `tests/integration/test_storage_settings_api.py`

---

## Phase 4: US2 - File Upload

> **User Story**: "As a User with FILES_WRITE permission, I can upload files to tenant storage"
> **Priority**: P1
> **Dependencies**: US1 complete (storage settings must exist)
> **Test Criteria**: Authenticated user can upload file, file stored in configured provider

### Models & Schemas

- [x] T019 [P] [US2] Create `src/app/modules/files/__init__.py` module init
- [x] T020 [P] [US2] Create `src/app/modules/files/models.py` with `File` model:
  - Fields: `id`, `tenant_id` (FK), `user_id` (nullable FK), `original_name`, `storage_path`
  - Metadata: `mime_type`, `size_bytes`, `is_public`, `created_at`, `updated_at`
  - ON DELETE: CASCADE for tenant_id, SET NULL for user_id

- [x] T021 [P] [US2] Create `src/app/modules/files/schemas.py`:
  - `FileUploadResponse`, `FileRead`, `FileUpdate`, `FileListResponse`

### Service Layer

- [x] T022 [US2] Create `src/app/modules/files/service.py` - Part 1:
  - `upload_file()` - quota check, store to provider, save metadata
  - Cleanup on failure (delete stored object if DB write fails)
  - Generate UUID-based storage paths

### API Endpoints

- [x] T023 [US2] Create `src/app/modules/files/router.py` - Part 1:
  - `POST /files/upload` - multipart upload (FILES_WRITE required)
  - Validate file type, check quotas, return `FileUploadResponse`

- [x] T024 [US2] Register router in `src/app/api/v1/router.py`

### Tests

- [ ] T025 [P] [US2] Create `tests/unit/test_files_service.py` - upload logic
- [ ] T026 [P] [US2] Create `tests/integration/test_files_upload_api.py`
- [ ] T027 [US2] Add quota exceeded test case to integration tests

---

## Phase 5: US3 - File Management

> **User Story**: "As a User, I can list, download, update, and delete my files"
> **Priority**: P2
> **Dependencies**: US2 complete
> **Test Criteria**: User can CRUD files, public files accessible via signed URLs

### Service Layer Extensions

- [x] T028 [US3] Extend `src/app/modules/files/service.py` - Part 2:
  - `list_files()` - paginated, filterable by user/type
  - `download_file()` - streaming response via async generator
  - `update_file()` - update metadata (name, visibility)
  - `delete_file()` - remove from storage + database
  - `get_signed_url()` - signed URL with expiration

### API Endpoints

- [x] T029 [US3] Extend `src/app/modules/files/router.py` - Part 2:
  - `GET /files` - list files (FILES_READ required)
  - `GET /files/{id}` - download file (FILES_READ required)
  - `GET /files/{id}/info` - get metadata only
  - `GET /files/{id}/url` - get signed URL for public access
  - `PATCH /files/{id}` - update metadata (FILES_WRITE required)
  - `DELETE /files/{id}` - delete file (FILES_DELETE required)

### Tests

- [ ] T030 [P] [US3] Extend `tests/unit/test_files_service.py` - CRUD logic
- [ ] T031 [P] [US3] Create `tests/integration/test_files_crud_api.py`
- [ ] T032 [P] [US3] Create `tests/integration/test_files_signed_url.py`
- [ ] T033 [US3] Add tenant isolation tests (cross-tenant access returns 404)

---

## Phase 6: US4 - RBAC Permission Enforcement

> **User Story**: "As a System, all file and storage endpoints are protected by granular permissions"
> **Priority**: P2
> **Dependencies**: US1, US2, US3 complete
> **Test Criteria**: Unauthorized requests return 403, self-access logic works

### Service Layer

- [x] T034 [US4] Update `src/app/modules/rbac/service.py`:
  - Add self-access logic for `USER_SELF_READ_AND_WRITE_ONLY` permission
  - Implement file ownership check for self-only access

### Integration

- [x] T035 [US4] Update storage_settings router with `MANAGE_STORAGE_SETTINGS` dependency
- [x] T036 [US4] Update files router with `FILES_READ`, `FILES_WRITE`, `FILES_DELETE` dependencies

### Tests

- [ ] T037 [P] [US4] Create `tests/integration/test_rbac_storage_permissions.py`
- [ ] T038 [P] [US4] Create `tests/integration/test_rbac_files_permissions.py`

---

## Phase 7: US5 - Audit Log Export

> **User Story**: "As a Tenant Admin, I can export audit logs for compliance"
> **Priority**: P3
> **Dependencies**: None (uses existing audit_logs module)
> **Test Criteria**: Admin can export logs as CSV/JSON with 100k limit enforced

### API Endpoints

- [x] T039 [US5] Extend `src/app/modules/audit_logs/router.py`:
  - `GET /audit-logs/export/count` - preview record count before export
  - `GET /audit-logs/export` - streaming export (CSV/JSON format)
  - Enforce 100k record limit with 400 error
  - Require `AUDIT_LOGS_DOWNLOAD` permission
  - Log export event to audit trail

### Service Layer

- [x] T040 [US5] Create export helpers in `src/app/modules/audit_logs/service.py`:
  - `count_exportable_records()` - count with filters
  - `stream_export()` - async generator for streaming response
  - Format conversion (JSON, CSV, NDJSON)

### Tests

- [ ] T041 [P] [US5] Create `tests/unit/test_audit_export_service.py`
- [ ] T042 [P] [US5] Create `tests/integration/test_audit_export_api.py`
- [ ] T043 [US5] Add test for 100k limit enforcement
- [ ] T044 [US5] Add test for export event self-logging

---

## Phase 8: Polish & Cross-Cutting Concerns

> **Goal**: Final integration, observability, and documentation

- [ ] T045 Add OpenTelemetry metrics instrumentation to file service
- [ ] T046 Add distributed tracing spans to storage provider operations
- [x] T047 Create Alembic migration for `storage_settings` and `files` tables
- [ ] T048 Update API documentation in OpenAPI schema

---

## Dependency Graph

```text
Phase 1 (Setup)
    ↓
Phase 2 (Foundational)
    ↓
Phase 3 (US1: Storage Settings) ← Required for file operations
    ↓
Phase 4 (US2: File Upload)
    ↓
Phase 5 (US3: File Management)
    ↓
Phase 6 (US4: RBAC)
    
Phase 7 (US5: Audit Export) ← Independent, can run in parallel with US4
    ↓
Phase 8 (Polish)
```

---

## Parallel Execution Opportunities

| Phase | Parallelizable Tasks |
|-------|---------------------|
| Phase 2 | T005, T006, T007, T008 (all independent) |
| Phase 3 | T011-T013 (models/schemas), T017 (unit tests) |
| Phase 4 | T019-T021 (models/schemas), T025-T026 (tests) |
| Phase 5 | T030-T032 (tests) |
| Phase 6 | T037-T038 (tests) |
| Phase 7 | T041-T042 (tests) |

---

## MVP Scope

**Minimum Viable Product**: Phases 1-4 (US1 + US2)
- Storage settings CRUD
- File upload with quota enforcement
- Basic permission checks

**Full Feature**: All 8 phases

---

## Verification Commands

```bash
# Run all new unit tests
cd src && pytest tests/unit/test_storage*.py tests/unit/test_files*.py tests/unit/test_azure*.py tests/unit/test_local*.py tests/unit/test_audit_export*.py -v

# Run all new integration tests
cd src && pytest tests/integration/test_storage*.py tests/integration/test_files*.py tests/integration/test_audit_export*.py tests/integration/test_rbac*.py -v

# Coverage report
cd src && pytest --cov=app/modules/storage_settings --cov=app/modules/files --cov=app/modules/audit_logs --cov-report=term-missing
```
