# Implementation Plan: Storage, RBAC & Audit Log Enhancements

**Branch**: `007-storage-rbac-audit` | **Date**: 2026-01-07 | **Spec**: [spec.md](file:///d:/project/FastAPI_saas/specs/007-storage-rbac-audit/spec.md)
**Input**: Feature specification from `/specs/007-storage-rbac-audit/spec.md`

---

## Summary

Implements three consolidated phases:
- **Phase 4**: File Storage Infrastructure (S3, Azure Blob, Local) with per-tenant configuration
- **Phase 6**: RBAC Enhancements with 8 new granular permissions
- **Phase 7**: Audit Logs Export with streaming and size limits

---

## Technical Context

**Language/Version**: Python 3.11-3.12 (per pyproject.toml)
**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2, fastcrud
**Storage**: PostgreSQL (constitution-mandated)
**Testing**: pytest, pytest-asyncio
**Target Platform**: Docker-compatible Linux server
**Performance Goals**: 
- File upload/download: ≤5s for 100MB
- Metadata queries: ≤100ms
- Audit export: ≤30s for 100k records
**Constraints**:
- Row-level tenant isolation required
- Async/await for all I/O operations
- 80% test coverage per constitution
**Scale/Scope**: 10k files/tenant (default), 10GB storage quota/tenant (default)

---

## Constitution Check

*GATE: ✅ ALL PASS*

| Principle | Status | Implementation |
|-----------|--------|----------------|
| I. Async-First | ✅ Pass | All storage operations use async SDK calls (`aiofiles`, `azure.storage.blob.aio`) |
| II. Strict Type Safety | ✅ Pass | Pydantic v2 schemas for all DTOs, `Mapped[]` for SQLAlchemy models |
| III. Test-Driven Quality | ✅ Pass | Unit tests before implementation, 80% coverage target |
| IV. Modular Service | ✅ Pass | `storage_settings/`, `files/`, `storage/` as separate modules |
| V. Security by Default | ✅ Pass | Fernet encryption for credentials, permission checks on all endpoints |
| VI. PostgreSQL-Only | ✅ Pass | All data in PostgreSQL with proper migrations |
| VII. Row-Level Tenant | ✅ Pass | `tenant_id` FK on all tables, fastcrud filtering |
| VIII. Tenant via Header | ✅ Pass | Existing `get_current_tenant` dependency reused |
| IX. Documentation | ✅ Pass | OpenAPI contracts, quickstart guide |

---

## Key Design Decisions (from Clarifications)

### Session 1 (Initial)
1. **Public file access**: Signed URLs with 1-hour expiration
2. **Storage failure handling**: Fail fast with clear error (no auto-retry)
3. **Per-tenant limits**: File count (10k) + storage quota (10GB)
4. **Observability**: Full metrics + distributed tracing (OpenTelemetry)
5. **Audit export limit**: Max 100k records per export

### Session 2 (Edge Cases)
6. **No storage settings**: Return 400 "Storage not configured. Contact tenant admin."
7. **User deletion**: SET NULL on user_id, transfer ownership to tenant admin
8. **Credential validation**: Validate on save (test connection, fail if invalid)
9. **Partial upload failure**: Cleanup - delete stored object before returning error
10. **Key rotation**: Support gradual rotation - decrypt with old/new, encrypt with new on update

---

## Project Structure

### Source Code

```text
src/app/modules/
├── storage_settings/        # [NEW] Tenant storage configuration
│   ├── __init__.py
│   ├── models.py           # StorageSettings SQLAlchemy model
│   ├── schemas.py          # Pydantic DTOs
│   ├── service.py          # Business logic + credential validation
│   └── router.py           # API endpoints
├── files/                   # [NEW] File metadata management
│   ├── __init__.py
│   ├── models.py           # File SQLAlchemy model
│   ├── schemas.py          # Pydantic DTOs
│   ├── service.py          # Upload/download/delete + cleanup logic
│   └── router.py           # API endpoints
├── storage/                 # [EXISTING + NEW]
│   ├── interfaces.py       # [EXISTS] StorageProvider ABC
│   ├── s3_storage.py       # [EXISTS] AWS S3 implementation
│   ├── azure_storage.py    # [NEW] Azure Blob implementation
│   └── local_storage.py    # [NEW] Local filesystem implementation
├── rbac/
│   ├── permissions.py      # [MODIFY] Add 8 new permissions
│   └── service.py          # [MODIFY] Add self-access logic
└── audit_logs/
    └── router.py           # [MODIFY] Add export endpoints

tests/
├── unit/
│   ├── test_storage_settings_service.py
│   ├── test_files_service.py
│   ├── test_azure_storage.py
│   └── test_local_storage.py
└── integration/
    ├── test_storage_settings_api.py
    ├── test_files_api.py
    └── test_audit_export_api.py
```

---

## Proposed Changes

### Phase 4: File Storage Infrastructure

#### `storage_settings/models.py` [NEW]
- `StorageSettings` model with `tenant_id` FK (unique constraint)
- Enum field for provider type (s3/azure/local)
- Encrypted credential fields (BYTEA with Fernet)
- Config fields: `max_file_size_mb`, `allowed_file_types`, `max_file_count`, `storage_quota_bytes`

#### `storage_settings/service.py` [NEW]
- `create/get/update_settings()` with credential encryption
- `validate_credentials()` - test connection before save
- Fernet key rotation support (try old key, encrypt with new)

#### `files/models.py` [NEW]
- `File` model with `tenant_id`, `user_id` (nullable), `storage_path`, metadata
- ON DELETE SET NULL for user_id, CASCADE for tenant_id

#### `files/service.py` [NEW]
- `upload_file()` with quota check, cleanup on failure
- `download_file()` with streaming response
- `get_signed_url()` for public access
- `transfer_ownership()` for user deletion handling

### Phase 6: RBAC Enhancements

#### `permissions.py` [MODIFY]
```python
# Add 8 new permissions
MANAGE_BILLING = "billing:manage"
MANAGE_PRODUCTS = "products:manage"
MANAGE_STORAGE_SETTINGS = "storage_settings:manage"
AUDIT_LOGS_VIEW_ONLY = "audit_logs:read"
AUDIT_LOGS_DOWNLOAD = "audit_logs:export"
FILES_READ = "files:read"
FILES_WRITE = "files:write"
FILES_DELETE = "files:delete"
```

### Phase 7: Audit Logs Export

#### `audit_logs/router.py` [MODIFY]
- `GET /audit-logs/export` - streaming export with 100k limit
- `GET /audit-logs/export/count` - preview count before export
- Log export events to audit trail

---

## Verification Plan

### Automated Tests

```bash
# Run all unit tests
cd src && pytest tests/unit/test_storage_settings*.py tests/unit/test_files*.py -v

# Run integration tests
cd src && pytest tests/integration/test_storage_settings*.py tests/integration/test_files*.py tests/integration/test_audit*.py -v

# Coverage report
cd src && pytest --cov=app/modules/storage_settings --cov=app/modules/files --cov-report=term-missing
```

### Key Test Scenarios

1. **Storage Settings**
   - Create/update with valid credentials (mock provider)
   - Reject invalid credentials with 400 error
   - Key rotation: decrypt old, encrypt new

2. **File Operations**
   - Upload with quota check
   - Cleanup on partial failure
   - User deletion → ownership transfer

3. **Tenant Isolation**
   - Cross-tenant access returns 404
   - Signed URLs scoped to tenant

4. **Audit Export**
   - Streaming for large datasets
   - 400 error when exceeding 100k limit
   - Export event logged

---

## Dependencies

Add to `pyproject.toml`:
```toml
[project.dependencies]
azure-storage-blob = ">=12.0.0"
aiofiles = ">=23.0.0"
opentelemetry-api = ">=1.20.0"
```

---

## Generated Artifacts

| File | Status |
|------|--------|
| [research.md](file:///d:/project/FastAPI_saas/specs/007-storage-rbac-audit/research.md) | ✅ Complete |
| [data-model.md](file:///d:/project/FastAPI_saas/specs/007-storage-rbac-audit/data-model.md) | ✅ Complete |
| [quickstart.md](file:///d:/project/FastAPI_saas/specs/007-storage-rbac-audit/quickstart.md) | ✅ Complete |
| [contracts/](file:///d:/project/FastAPI_saas/specs/007-storage-rbac-audit/contracts) | ✅ Complete |
