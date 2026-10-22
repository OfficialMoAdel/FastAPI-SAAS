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

### IP rules ownership

IP filtering is implemented by the core-side `core/guard/ip_rules.py` module. The
`IpRules` protocol remains the injected seam for `IPFilterMiddleware` and
`AttackDetectionMiddleware`; `RedisIpRules` is the production adapter. This
module keeps no facade or re-export; the router imports the
core-owned administrative instance directly.

### Module Structure

```
src/app/modules/security/
├── __init__.py         # Module exports
├── router.py           # API routes (thin: request shaping + response models)
├── service.py          # Business logic (passkeys, IP rules, auto-ban)
└── schemas.py          # Request/response schemas
```

### Design Pattern

The security module follows the **Service Layer** pattern:
- **Router:** Parses the request, resolves the request context, and shapes the
  response — no business logic and no raw-dict reshaping.
- **Service:** Owns the behaviour: passkey listing/deletion, IP whitelist and
  blacklist shaping, runtime-config updates (with the audit write), and the
  auto-ban operations. It depends on the core-side collaborators
  (`core.guard.ip_rules.ip_filter_service`, `core.security.auto_ban`,
  `core.security.runtime_config`), never on the API layer.
- **Passkeys:** The passkey collaborator comes from the auth module's own
  factory (`src.app.modules.auth` → `get_passkey_service`). The security
  module does not re-implement passkey logic; it delegates.

### Dependencies

```python
from src.app.modules.auth import get_passkey_service
from src.app.modules.auth.schemas import (
    PasskeyCredentialResponse,
    PasskeyDeleteResponse,
)
```

The service never imports `src.app.api.*` — the API layer sits above it, so
the service takes `db` and the resolved ids as plain arguments.

## Implementation Notes

### Why This Module Exists

1. **Backward Compatibility:** Legacy frontend expects `/security/passkeys`
2. **Clean Separation:** Security-related endpoints grouped under `/security` prefix
3. **API Simplification:** Provides simpler path structure for frontend developers

### Relationship to Passkey Module

The security module is a **thin wrapper** around the passkey module:

| Security Endpoint | Delegates To | Purpose |
|-------------------|--------------|---------|
| `GET /security/passkeys` | `SecurityService.list_passkeys()` → `passkey_service.list_credentials_page()` | List user's passkeys |
| `DELETE /security/passkeys/{id}` | `SecurityService.delete_passkey()` → `passkey_service.delete_credential()` | Delete a passkey |

`SecurityService.list_passkeys()` pages in the DATABASE, not in RAM: it asks
the CRUD for one page (`offset`/`limit`) and the matching total, so a user
with many credentials does not load all of them to return a slice.

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
uv run pytest tests/unit/modules/security/ -v
```

**Test Coverage:**
- ✅ Endpoints require tenant header
- ✅ OpenAPI documentation generation
- ✅ Correct tag assignment ("security")
- ✅ Compatibility with passkey endpoints
- ✅ Database-level pagination of the passkey list
- ✅ Service-level behaviour for IP rules, runtime config and auto-ban

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
