# Security Module

**Location:** `src/app/modules/security/`  
**Purpose:** Convenience wrapper for passkey management endpoints to maintain backward compatibility.

## Overview

The security module provides a thin wrapper around passkey authentication functionality, exposing endpoints at `/api/v1/security/*` for frontend compatibility. This maintains backward compatibility with legacy frontend applications that expect security-related endpoints at this path.

## Endpoints

### `GET /api/v1/security/passkeys`

List all passkey credentials for the current user.

**Authentication:** Required (JWT token)  
**Tenant Header:** Required  
**Response:** `PasskeyCredentialList` (paginated envelope)

```bash
# Example request
curl -X GET \
  http://localhost:8000/api/v1/security/passkeys \
  -H "Authorization: Bearer <token>" \
  -H "X-Tenant-ID: <tenant-id>"
```

### `DELETE /api/v1/security/passkeys/{passkey_id}`

Delete a specific passkey credential.

**Authentication:** Required (JWT token)  
**Tenant Header:** Required  
**Parameters:**
- `passkey_id` (path) - ID of the passkey to delete

**Response:** `PasskeyDeleteResponse`

```bash
# Example request
curl -X DELETE \
  http://localhost:8000/api/v1/security/passkeys/123 \
  -H "Authorization: Bearer <token>" \
  -H "X-Tenant-ID: <tenant-id>"
```

## Architecture

### Module Structure

```
src/app/modules/security/
├── __init__.py         # Module exports
└── router.py           # API routes (thin wrapper)
```

### Design Pattern

The security module follows the **Facade Pattern**:
- **Wrapper Layer:** Provides convenience endpoints at `/security/*`
- **Delegation:** All business logic delegated to `passkey_service`
- **No Duplication:** No duplicate service layer; reuses existing passkey module

### Dependencies

```python
from src.app.modules.auth.schemas import (
    PasskeyCredentialResponse,
    PasskeyDeleteResponse,
)
from src.app.modules.auth.services.passkey import passkey_service
```

## Implementation Notes

### Why This Module Exists

1. **Backward Compatibility:** Legacy frontend expects `/security/passkeys`
2. **Clean Separation:** Security-related endpoints grouped under `/security` prefix
3. **API Simplification:** Provides simpler path structure for frontend developers

### Relationship to Passkey Module

The security module is a **thin wrapper** around the passkey module:

| Security Endpoint | Delegates To | Purpose |
|-------------------|--------------|---------|
| `GET /security/passkeys` | `passkey_service.list_credentials()` | List user's passkeys |
| `DELETE /security/passkeys/{id}` | `passkey_service.delete_credential()` | Delete a passkey |

Full passkey functionality (registration, authentication) remains at:
- `POST /auth/passkey/register/start` - Start registration
- `POST /auth/passkey/register/finish` - Complete registration
- `POST /auth/passkey/login/start` - Start authentication
- `POST /auth/passkey/login/finish` - Complete authentication
- `GET /auth/passkey/credentials` - List credentials (same as security endpoint)

## Testing

### Unit Tests

```bash
# Run security module tests
uv run pytest tests/unit/test_security_router.py -v
```

**Test Coverage:**
- ✅ Endpoints require tenant header
- ✅ OpenAPI documentation generation
- ✅ Correct tag assignment ("security")
- ✅ Compatibility with passkey endpoints

### Integration Tests

Security endpoints are included in the frontend compatibility test suite:

```bash
uv run pytest tests/integration/test_frontend_compatibility.py::TestFrontendCompatibility::test_security_endpoints_exist -v
```

## Common Workflows

### List User's Passkeys

```python
# Frontend code
async function listPasskeys() {
  const response = await fetch('/api/v1/security/passkeys', {
    headers: {
      'Authorization': `Bearer ${token}`,
      'X-Tenant-ID': tenantId,
    },
  });
  return await response.json();
}
```

### Delete a Passkey

```python
# Frontend code
async function deletePasskey(passkeyId: number) {
  const response = await fetch(`/api/v1/security/passkeys/${passkeyId}`, {
    method: 'DELETE',
    headers: {
      'Authorization': `Bearer ${token}`,
      'X-Tenant-ID': tenantId,
    },
  });
  return await response.json();
}
```

## Maintenance Notes

### Future Enhancements

If more security-related features are added (2FA, security keys, etc.), they should be added to this module under the `/security` prefix for consistency.

### Deprecation

If the frontend is updated to use `/auth/passkey/*` directly, this module can be marked as deprecated but should remain for backward compatibility until all clients are migrated.

## Related Modules

- **Auth Module (Passkeys & Identity):** `src/app/modules/auth/` - Passkey credentials and authentication
- **Users Module:** `src/app/modules/users/` - User management

## API Versioning

- **Current Version:** v1
- **Prefix:** `/api/v1/security`
- **Stability:** Stable (backward compatible wrapper)
