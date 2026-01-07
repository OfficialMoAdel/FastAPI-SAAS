# Research: Storage, RBAC & Audit Log Enhancements

**Branch**: `007-storage-rbac-audit` | **Date**: 2026-01-07

---

## Research Topics

### 1. Credential Encryption for Storage Settings

**Decision**: Use Fernet symmetric encryption from `cryptography` library

**Rationale**: 
- Fernet provides authenticated encryption (AES-128-CBC + HMAC-SHA256)
- Already referenced in IMPLEMENTATION_WORKFLOW.md Task 0.1
- Single key management via `settings.ENCRYPTION_KEY`
- Suitable for encrypting S3/Azure credentials at rest

**Alternatives Considered**:
- AWS KMS / Azure Key Vault: More secure but adds external dependency and complexity
- Asymmetric encryption: Overkill for this use case, slower
- Database-level encryption: PostgreSQL TDE doesn't protect against SQL injection

**Implementation Notes**:
- Key must be 32-byte URL-safe base64 encoded
- Store encrypted values as `LargeBinary` in PostgreSQL
- Use hybrid properties on SQLAlchemy models for transparent encrypt/decrypt

---

### 2. Azure Blob Storage Async SDK

**Decision**: Use `azure-storage-blob` with async support via `aio` namespace

**Rationale**:
- Official Microsoft SDK with async support
- Matches existing S3 async pattern
- Supports streaming uploads/downloads

**Best Practices**:
```python
from azure.storage.blob.aio import BlobServiceClient

async def upload_blob(container: str, blob_name: str, data: bytes):
    async with BlobServiceClient.from_connection_string(conn_str) as client:
        container_client = client.get_container_client(container)
        await container_client.upload_blob(blob_name, data, overwrite=True)
```

**Alternatives Considered**:
- `aiobotocore` with S3-compatible endpoint: Azure-specific features lost
- Sync SDK with `run_in_executor`: Adds complexity, potential thread starvation

---

### 3. Local Storage Async File I/O

**Decision**: Use `aiofiles` library for async file operations

**Rationale**:
- Simple, well-maintained async wrapper around file operations
- Non-blocking I/O for file reads/writes
- Compatible with existing async patterns

**Best Practices**:
```python
import aiofiles

async def write_file(path: str, content: bytes):
    async with aiofiles.open(path, 'wb') as f:
        await f.write(content)
```

**Alternatives Considered**:
- `anyio`: More features but heavier dependency
- Sync I/O with executor: Works but less elegant

---

### 4. Streaming File Downloads

**Decision**: Use FastAPI `StreamingResponse` with async generators

**Rationale**:
- Memory efficient for large files
- Proper HTTP chunked transfer encoding
- Works with all storage providers

**Pattern**:
```python
from fastapi.responses import StreamingResponse

@router.get("/files/{file_id}")
async def download_file(file_id: int):
    async def stream():
        async for chunk in storage_provider.download(file_path):
            yield chunk
    return StreamingResponse(stream(), media_type=mime_type)
```

---

### 5. Signed URL Generation for Public Files

**Decision**: Use provider-specific presigned URL generation

**Rationale**:
- S3: `generate_presigned_url()` built into boto3
- Azure: SAS (Shared Access Signature) tokens
- Local: Generate application-signed token with expiry

**Pattern**:
- S3: `s3_client.generate_presigned_url('get_object', Params={...}, ExpiresIn=3600)`
- Azure: `generate_blob_sas()` with expiry
- Local: HMAC-signed URL validated by application

---

### 6. Audit Log Export Streaming

**Decision**: Use async generators with `StreamingResponse`

**Rationale**:
- Handles 100k records without loading all into memory
- JSONL format is naturally line-oriented for streaming
- CSV can use buffered writer approach

**Pattern**:
```python
async def export_jsonl(tenant_id: int, start: datetime, end: datetime):
    async for batch in audit_crud.stream_logs(tenant_id, start, end, batch_size=1000):
        for log in batch:
            yield json.dumps(log.model_dump()) + "\n"
```

---

### 7. Observability: Metrics and Tracing

**Decision**: Use OpenTelemetry for both metrics and tracing

**Rationale**:
- Industry standard for observability
- Integrates with multiple backends (Prometheus, Jaeger, etc.)
- FastAPI has good OTEL integration

**Metrics to Emit**:
- `storage.upload.count` (counter, by tenant/provider)
- `storage.upload.bytes` (histogram)
- `storage.upload.duration_ms` (histogram)
- `storage.upload.errors` (counter, by error_type)
- `storage.download.count`, `storage.download.bytes`, etc.

**Tracing Spans**:
- `storage.upload` with attributes: file_size, mime_type, provider
- `storage.download` with attributes: file_id, file_size

---

### 8. Permission Enforcement Patterns

**Decision**: Extend existing `Depends` pattern with new permission checks

**Rationale**:
- Consistent with existing RBAC implementation
- Follows dependency injection pattern from constitution

**Pattern**:
```python
from src.app.core.rbac.dependencies import require_permission, Permission

@router.patch("/storage/settings")
async def update_settings(
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission(Permission.MANAGE_STORAGE_SETTINGS)),
):
    ...
```

---

## Unresolved Items

None - All technical decisions made based on existing patterns and best practices.

---

## References

- [Azure Blob Storage Async](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-quickstart-blobs-python)
- [FastAPI StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse)
- [OpenTelemetry Python](https://opentelemetry.io/docs/instrumentation/python/)
- IMPLEMENTATION_WORKFLOW.md Task 0.1 (Credential Encryption)
- MIGRATION_PLAN.md Phase 4 (File Storage specification)
