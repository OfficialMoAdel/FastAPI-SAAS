# Quickstart: Storage, RBAC & Audit Log Enhancements

**Branch**: `007-storage-rbac-audit` | **Date**: 2026-01-07

---

## Prerequisites

- Python 3.11+
- PostgreSQL 15+ running
- Redis running (for rate limiting)
- Environment variables configured (see below)

---

## Environment Setup

Add to your `.env.local`:

```bash
# Storage encryption key (32-byte base64)
ENCRYPTION_KEY=your-32-byte-fernet-key-here

# Optional: S3 default config (can also be set per-tenant)
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_DEFAULT_REGION=us-east-1

# Optional: Azure config (can also be set per-tenant)
AZURE_STORAGE_CONNECTION_STRING=your-connection-string

# Local storage path (for local provider)
LOCAL_STORAGE_PATH=/var/app/uploads
```

Generate a Fernet key:
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

---

## Quick Test

### 1. Configure Storage Settings

```bash
# Create storage settings for tenant (S3 example)
curl -X POST "http://localhost:8000/api/v1/storage/settings" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "s3",
    "s3_bucket_name": "my-tenant-bucket",
    "s3_region": "us-east-1",
    "s3_access_key_id": "AKIAEXAMPLE",
    "s3_secret_access_key": "secret",
    "max_file_size_mb": 50,
    "allowed_file_types": ["image/*", "application/pdf"]
  }'
```

### 2. Upload a File

```bash
curl -X POST "http://localhost:8000/api/v1/files/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1" \
  -F "file=@/path/to/document.pdf"
```

Response:
```json
{
  "id": 1,
  "filename": "a1b2c3d4-5678-90ab-cdef-1234567890ab.pdf",
  "original_filename": "document.pdf",
  "mime_type": "application/pdf",
  "file_size": 102400,
  "is_public": false,
  "created_at": "2026-01-07T14:30:00Z"
}
```

### 3. Download a File

```bash
curl -X GET "http://localhost:8000/api/v1/files/1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1" \
  -o downloaded_file.pdf
```

### 4. Get Signed URL (Public Files)

```bash
# First, make file public
curl -X PATCH "http://localhost:8000/api/v1/files/1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1" \
  -H "Content-Type: application/json" \
  -d '{"is_public": true}'

# Get signed URL
curl -X GET "http://localhost:8000/api/v1/files/1/url" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1"
```

Response:
```json
{
  "url": "https://bucket.s3.amazonaws.com/file.pdf?X-Amz-Signature=...",
  "expires_at": "2026-01-07T15:30:00Z"
}
```

### 5. Export Audit Logs

```bash
# Export as JSONL
curl -X GET "http://localhost:8000/api/v1/audit-logs/export?format=jsonl&start_date=2026-01-01&end_date=2026-01-07" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1" \
  -o audit_logs.jsonl

# Export as CSV
curl -X GET "http://localhost:8000/api/v1/audit-logs/export?format=csv" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 1" \
  -o audit_logs.csv
```

---

## Running Tests

```bash
# All tests
uv run pytest

# Just this feature's tests
uv run pytest tests/unit/test_storage_settings*.py tests/unit/test_files*.py -v
uv run pytest tests/integration/test_storage_settings_api.py tests/integration/test_files_api.py -v

# With coverage
uv run pytest --cov=src --cov-fail-under=80
```

---

## API Documentation

After running the server, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## Common Issues

### "Encryption key not configured"
- Ensure `ENCRYPTION_KEY` is set in environment
- Key must be valid Fernet key (32-byte base64)

### "Storage settings not found"
- Each tenant needs storage settings before file uploads
- Create via `POST /storage/settings`

### "File type not allowed"
- Check tenant's `allowed_file_types` in storage settings
- Default: `["image/*", "application/pdf"]`

### "Quota exceeded"
- Tenant has reached `max_file_count` or `storage_quota_bytes`
- Delete old files or request quota increase
