# API Reference Documentation

Comprehensive API documentation for the FastAPI SaaS , including endpoints, request/response schemas, authentication requirements, and integration patterns. This documentation enables developers to understand and integrate with the API effectively.

## API Overview

The FastAPI SaaS  exposes a RESTful API organized under the `/api/v1/` prefix for clear versioning. All endpoints return JSON responses and accept JSON request bodies where applicable. The API implements standard HTTP methods (GET, POST, PUT, PATCH, DELETE) with semantic meaning: GET for retrieval, POST for creation, PUT for full updates, PATCH for partial updates, and DELETE for removal.

Authentication is handled through JWT tokens with support for both header-based and cookie-based token delivery. The API implements role-based access control (RBAC) with permission checks at both the route level and operation level. Multi-tenancy is enforced through the TenantMiddleware, which scopes all operations to the authenticated user's tenant context.

All endpoints include OpenAPI documentation accessible at `/docs` (Swagger UI) and `/redoc` (ReDoc). The OpenAPI schema provides interactive documentation, request/response examples, and the ability to test endpoints directly from the browser. The API follows REST best practices including proper HTTP status codes, consistent error response formats, and pagination for collection endpoints.

### Base URL and Versioning

All API endpoints are prefixed with `/api/v1/`, indicating the first major version of the API. API versioning follows semantic versioning principles where breaking changes require a new version number. Non-breaking additions and improvements are made to the existing version without requiring client updates. The versioning strategy enables clients to continue using existing endpoints while new features are added.

```
https://api.example.com/api/v1/{endpoint}
```

For development and testing, the API is accessible at:
```
http://localhost:8000/api/v1/{endpoint}
```

### Response Format

All API responses follow a consistent JSON structure. Successful responses include the requested data, while error responses include an error object with code, message, and details. Pagination responses include metadata about the total count, page number, and page size.

**Success Response (Single Resource):**
```json
{
  "data": {
    "id": "uuid",
    "name": "Example",
    "created_at": "2024-01-15T10:30:00Z"
  },
  "message": "Resource retrieved successfully"
}
```

**Success Response (Paginated Collection):**
```json
{
  "data": [
    {
      "id": "uuid",
      "name": "Item 1"
    },
    {
      "id": "uuid",
      "name": "Item 2"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 50,
    "total_pages": 3
  },
  "message": "Resources retrieved successfully"
}
```

**Error Response:**
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Validation failed for request",
    "details": [
      {
        "field": "email",
        "message": "Invalid email format"
      }
    ]
  },
  "message": "Request validation failed"
}
```

### HTTP Status Codes

The API uses standard HTTP status codes to indicate the result of API requests. Understanding these codes is essential for proper error handling in client applications.

| Status Code | Meaning | Description |
|-------------|---------|-------------|
| 200 | OK | Request succeeded, returning the requested data |
| 201 | Created | Resource created successfully, returning the new resource |
| 204 | No Content | Request succeeded with no response body |
| 400 | Bad Request | Invalid request parameters or body |
| 401 | Unauthorized | Authentication required or invalid token |
| 403 | Forbidden | Authenticated but lacks permission |
| 404 | Not Found | Resource does not exist |
| 422 | Unprocessable Entity | Validation error in request body |
| 429 | Rate Limited | Too many requests, rate limit exceeded |
| 500 | Internal Server Error | Server error, check logs for details |
| 503 | Service Unavailable | Service temporarily unavailable |

---

## Authentication Endpoints (`/api/v1/auth/`)

The authentication module provides comprehensive authentication functionality including login, logout, token refresh, and password management. All authentication endpoints are publicly accessible (no authentication required) except where noted.

### POST /api/v1/auth/login

Authenticates a user and returns JWT access and refresh tokens. The endpoint accepts email and password credentials, validates them against the database, and returns tokens upon successful authentication. Failed authentication attempts are tracked for account lockout protection.

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "secure_password",
  "tenant_id": "uuid"  // Optional: for shared email across tenants
}
```

**Response (200 OK):**
```json
{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "bearer",
    "expires_in": 1800,
    "user": {
      "id": "uuid",
      "email": "user@example.com",
      "tenant_id": "uuid",
      "role": "admin"
    }
  },
  "message": "Login successful"
}
```

**Error Responses:**
- 401 Unauthorized: Invalid credentials
- 423 Locked: Account locked due to too many failed attempts

**Security Considerations:**
- Passwords are hashed using bcrypt before storage
- Failed attempts are tracked with IP and user fingerprinting
- Accounts are automatically locked after 5 failed attempts
- Lockout duration is 15 minutes before automatic unlock

### POST /api/v1/auth/refresh

Refreshes an access token using a valid refresh token. The refresh token is validated against the database token blacklist and user session. Upon successful validation, a new access token is issued while the refresh token is rotated for security.

**Request Headers:**
```
Authorization: Bearer <refresh_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "bearer",
    "expires_in": 1800
  },
  "message": "Token refreshed successfully"
}
```

**Security Considerations:**
- Refresh tokens are stored in HTTP-only secure cookies
- Token rotation on each refresh for enhanced security
- Refresh tokens expire after 7 days
- Old refresh tokens are immediately invalidated upon use

### POST /api/v1/auth/logout

Invalidates the current refresh token, logging the user out. The token is added to the blacklist to prevent further use. Both access and refresh tokens become invalid after logout.

**Request Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "message": "Logout successful"
}
```

**Security Considerations:**
- Token is immediately blacklisted in the database
- Blacklist is checked on every authenticated request
- Blacklisted tokens cannot be used for authentication

### POST /api/v1/auth/password/reset/request

Initiates a password reset flow by sending a reset email to the user. The endpoint generates a secure reset token and emails it to the registered email address. This endpoint does not reveal whether the email exists for security purposes.

**Request Body:**
```json
{
  "email": "user@example.com",
  "tenant_id": "uuid"  // Optional: for shared email across tenants
}
```

**Response (200 OK):**
```json
{
  "message": "If the email exists, a password reset link has been sent"
}
```

### POST /api/v1/auth/password/reset/confirm

Confirms a password reset with the token sent via email. The token is validated for expiration and then used to update the user's password. The new password must meet complexity requirements.

**Request Body:**
```json
{
  "token": "reset_token_from_email",
  "new_password": "new_secure_password"
}
```

**Response (200 OK):**
```json
{
  "message": "Password reset successful"
}
```

### POST /api/v1/auth/password/change

Changes the authenticated user's password. Requires the current password for security validation. This endpoint is authenticated and scoped to the current user and tenant context.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "current_password": "current_password",
  "new_password": "new_secure_password"
}
```

**Response (200 OK):**
```json
{
  "message": "Password changed successfully"
}
```

---

## Users Endpoints (`/api/v1/users/`)

The users module provides user management functionality including CRUD operations, profile management, and user-specific endpoints. All endpoints require authentication, and most are scoped to the user's tenant context.

### GET /api/v1/users/me

Retrieves the current authenticated user's profile. This endpoint provides all user information including preferences, tenant membership, and role assignments.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "email": "user@example.com",
    "tenant_id": "uuid",
    "first_name": "John",
    "last_name": "Doe",
    "avatar_url": "https://...",
    "is_active": true,
    "is_superuser": false,
    "preferences": {
      "theme": "dark",
      "language": "en"
    },
    "created_at": "2024-01-01T00:00:00Z",
    "last_login": "2024-01-15T10:30:00Z"
  },
  "message": "User profile retrieved"
}
```

### GET /api/v1/users/

Retrieves a paginated list of users within the current tenant. Requires administrative permissions (users:read). Results are filtered by tenant and support filtering by active status and search queries.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| page | int | Page number (default: 1) |
| per_page | int | Items per page (default: 20, max: 100) |
| is_active | bool | Filter by active status |
| search | string | Search in email and name |

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "email": "user1@example.com",
      "first_name": "User",
      "last_name": "One",
      "is_active": true,
      "role": "member",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 50,
    "total_pages": 3
  },
  "message": "Users retrieved successfully"
}
```

### GET /api/v1/users/{user_id}

Retrieves a specific user by ID within the current tenant. Requires administrative permissions (users:read).

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "email": "user@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "is_active": true,
    "role": "admin",
    "created_at": "2024-01-01T00:00:00Z"
  },
  "message": "User retrieved successfully"
}
```

**Error Responses:**
- 404 Not Found: User not found or not in tenant

### POST /api/v1/users/

Creates a new user within the current tenant. Requires administrative permissions (users:write). The user will receive an invitation email to set their password.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "email": "newuser@example.com",
  "first_name": "New",
  "last_name": "User",
  "role": "member"  // Optional: role assignment
}
```

**Response (201 Created):**
```json
{
  "data": {
    "id": "uuid",
    "email": "newuser@example.com",
    "first_name": "New",
    "last_name": "User",
    "tenant_id": "uuid",
    "is_active": true,
    "created_at": "2024-01-15T12:00:00Z"
  },
  "message": "User created successfully"
}
```

### PUT /api/v1/users/{user_id}

Updates all user fields. Requires administrative permissions (users:write). This is a full update operation; fields not provided will be cleared.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "email": "updated@example.com",
  "first_name": "Updated",
  "last_name": "User",
  "is_active": true
}
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "email": "updated@example.com",
    "first_name": "Updated",
    "last_name": "User",
    "is_active": true
  },
  "message": "User updated successfully"
}
```

### PATCH /api/v1/users/{user_id}

Updates specific user fields. Requires administrative permissions (users:write). This is a partial update operation; only provided fields are modified.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "first_name": "NewFirstName"
}
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "first_name": "NewFirstName",
    "email": "user@example.com"
  },
  "message": "User updated successfully"
}
```

### DELETE /api/v1/users/{user_id}

Soft deletes a user within the current tenant. Requires administrative permissions (users:delete). The user is marked as deleted but data is preserved for audit purposes.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "message": "User deleted successfully"
}
```

---

## Tenants Endpoints (`/api/v1/tenants/`)

The tenants module provides multi-tenant organization management. Tenants represent separate organizations or workspaces within the system. All tenant-scoped operations require a valid tenant context established through authentication.

### GET /api/v1/tenants/me

Retrieves the current user's tenant information. This endpoint is useful for clients to display tenant details and settings.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "name": "Acme Corporation",
    "slug": "acme-corp",
    "domain": "acme.example.com",
    "settings": {
      "features": {
        "api_access": true,
        "webhooks": true
      },
      "limits": {
        "users": 100,
        "storage_gb": 10
      }
    },
    "tier": "professional",
    "created_at": "2024-01-01T00:00:00Z",
    "trial_ends_at": "2024-02-01T00:00:00Z"
  },
  "message": "Tenant retrieved successfully"
}
```

### GET /api/v1/tenants/

Retrieves a list of tenants. Superusers can see all tenants; regular users can only access their own tenant.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| page | int | Page number (default: 1) |
| per_page | int | Items per page (default: 20) |
| tier | string | Filter by subscription tier |
| is_active | bool | Filter by active status |

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "name": "Acme Corporation",
      "slug": "acme-corp",
      "tier": "professional",
      "is_active": true,
      "user_count": 45,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 1,
    "total_pages": 1
  },
  "message": "Tenants retrieved successfully"
}
```

### POST /api/v1/tenants/

Creates a new tenant organization. The authenticated user becomes the initial owner and administrator of the new tenant.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "name": "New Organization",
  "slug": "new-org",  // Optional: auto-generated if not provided
  "domain": "neworg.example.com",  // Optional
  "settings": {
    "features": {
      "api_access": true
    }
  }
}
```

**Response (201 Created):**
```json
{
  "data": {
    "id": "uuid",
    "name": "New Organization",
    "slug": "new-org",
    "domain": "neworg.example.com",
    "owner_id": "uuid",
    "tier": "starter",
    "is_active": true,
    "created_at": "2024-01-15T12:00:00Z"
  },
  "message": "Tenant created successfully"
}
```

### PUT /api/v1/tenants/{tenant_id}

Updates tenant settings. Requires tenant administrative permissions.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "name": "Updated Organization Name",
  "settings": {
    "features": {
      "api_access": true,
      "webhooks": true
    }
  }
}
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "name": "Updated Organization Name",
    "settings": { /* updated settings */ }
  },
  "message": "Tenant updated successfully"
}
```

### GET /api/v1/tenants/{tenant_id}/members

Retrieves tenant membership list. Requires tenant administrative permissions.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": [
    {
      "user_id": "uuid",
      "email": "user@example.com",
      "role": "admin",
      "joined_at": "2024-01-01T00:00:00Z",
      "invited_by": "uuid"
    }
  ],
  "message": "Members retrieved successfully"
}
```

### POST /api/v1/tenants/{tenant_id}/members/invite

Invites a new member to the tenant. The invited user receives an email invitation and can join by setting up their account.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "email": "newmember@example.com",
  "role": "member"
}
```

**Response (201 Created):**
```json
{
  "data": {
    "invitation_id": "uuid",
    "email": "newmember@example.com",
    "role": "member",
    "expires_at": "2024-01-22T12:00:00Z"
  },
  "message": "Invitation sent successfully"
}
```

### DELETE /api/v1/tenants/{tenant_id}/members/{user_id}

Removes a member from the tenant. Cannot remove the owner. Self-removal is also supported.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "message": "Member removed successfully"
}
```

---

## Roles and Permissions Endpoints (`/api/v1/roles/`)

The RBAC module provides role-based access control management. Roles define collections of permissions that can be assigned to users. The module supports role hierarchies where roles can inherit permissions from parent roles.

### GET /api/v1/roles/

Retrieves all available roles. Requires administrative permissions (rbac:read).

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "name": "admin",
      "description": "Full administrative access",
      "is_system": true,
      "permissions": [
        {
          "id": "uuid",
          "name": "users:read",
          "description": "View users"
        },
        {
          "id": "uuid",
          "name": "users:write",
          "description": "Create and modify users"
        }
      ],
      "created_at": "2024-01-01T00:00:00Z"
    },
    {
      "id": "uuid",
      "name": "member",
      "description": "Basic member access",
      "is_system": false,
      "permissions": [
        {
          "id": "uuid",
          "name": "posts:read",
          "description": "View posts"
        }
      ],
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "message": "Roles retrieved successfully"
}
```

### POST /api/v1/roles/

Creates a new role. Requires administrative permissions (rbac:write). Custom roles can be created for specific tenant requirements.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "name": "moderator",
  "description": "Content moderator",
  "permissions": [
    "posts:read",
    "posts:write",
    "posts:delete",
    "users:read"
  ]
}
```

**Response (201 Created):**
```json
{
  "data": {
    "id": "uuid",
    "name": "moderator",
    "description": "Content moderator",
    "is_system": false,
    "permissions": [ /* permission details */ ],
    "created_at": "2024-01-15T12:00:00Z"
  },
  "message": "Role created successfully"
}
```

### PUT /api/v1/roles/{role_id}

Updates role permissions. Requires administrative permissions (rbac:write).

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "permissions": [
    "posts:read",
    "posts:write",
    "users:read",
    "users:write"
  ]
}
```

**Response (200 OK):**
```json
{
  "message": "Role updated successfully"
}
```

### DELETE /api/v1/roles/{role_id}

Deletes a custom role. System roles (admin, member, viewer) cannot be deleted.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "message": "Role deleted successfully"
}
```

### GET /api/v1/roles/permissions

Retrieves all available permissions in the system. Useful for building permission selection interfaces.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "name": "users:read",
      "description": "View users",
      "resource": "users"
    },
    {
      "id": "uuid",
      "name": "users:write",
      "description": "Create and modify users",
      "resource": "users"
    },
    {
      "id": "uuid",
      "name": "users:delete",
      "description": "Delete users",
      "resource": "users"
    }
    // ... more permissions
  ],
  "message": "Permissions retrieved successfully"
}
```

---

## Audit Logs Endpoints (`/api/v1/audit-logs/`)

The audit logs module provides immutable audit trail recording for compliance and debugging. All significant system events are recorded with user, tenant, action, resource, and timestamp information.

### GET /api/v1/audit-logs/

Retrieves audit log entries with filtering and pagination. Requires administrative permissions (audit:read). Logs are tenant-scoped by default.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| page | int | Page number (default: 1) |
| per_page | int | Items per page (default: 50, max: 200) |
| user_id | uuid | Filter by user |
| action | string | Filter by action type |
| resource_type | string | Filter by resource type |
| resource_id | uuid | Filter by specific resource |
| start_date | datetime | Filter from date (ISO 8601) |
| end_date | datetime | Filter to date (ISO 8601) |

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "tenant_id": "uuid",
      "user_id": "uuid",
      "user_email": "admin@example.com",
      "action": "users.create",
      "resource_type": "user",
      "resource_id": "uuid",
      "details": {
        "new_user_email": "newuser@example.com"
      },
      "ip_address": "192.168.1.1",
      "user_agent": "Mozilla/5.0...",
      "created_at": "2024-01-15T10:30:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 50,
    "total": 1000,
    "total_pages": 20
  },
  "message": "Audit logs retrieved successfully"
}
```

**Audit Actions Reference:**
| Action | Description |
|--------|-------------|
| auth.login | User login |
| auth.logout | User logout |
| auth.password_reset | Password reset requested |
| users.create | User created |
| users.update | User updated |
| users.delete | User deleted (soft) |
| tenants.create | Tenant created |
| tenants.update | Tenant updated |
| roles.assign | Role assigned to user |
| roles.revoke | Role revoked from user |
| webhooks.create | Webhook created |
| webhooks.trigger | Webhook delivered |
| api_keys.create | API key created |
| api_keys.revoke | API key revoked |

### GET /api/v1/audit-logs/{log_id}

Retrieves a specific audit log entry by ID.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "tenant_id": "uuid",
    "user_id": "uuid",
    "action": "users.create",
    "resource_type": "user",
    "resource_id": "uuid",
    "details": {
      "new_user_email": "newuser@example.com",
      "role": "member"
    },
    "ip_address": "192.168.1.1",
    "user_agent": "Mozilla/5.0...",
    "created_at": "2024-01-15T10:30:00Z"
  },
  "message": "Audit log retrieved successfully"
}
```

---

## Webhooks Endpoints (`/api/v1/webhooks/`)

The webhooks module enables event-driven integration with external systems. Webhooks deliver event notifications to configured endpoints with HMAC signature verification and retry logic.

### GET /api/v1/webhooks/

Retrieves configured webhooks. Requires administrative permissions (webhooks:read).

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "tenant_id": "uuid",
      "name": "Slack Integration",
      "url": "https://hooks.slack.com/services/...",
      "events": [
        "users.create",
        "posts.publish"
      ],
      "is_active": true,
      "secret": "********",  // Masked
      "failure_count": 0,
      "last_triggered_at": "2024-01-15T10:30:00Z",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "message": "Webhooks retrieved successfully"
}
```

### POST /api/v1/webhooks/

Creates a new webhook subscription. Requires administrative permissions (webhooks:write).

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "name": "Zapier Integration",
  "url": "https://hooks.zapier.com/hooks/catch/...",
  "events": ["users.create", "users.update"],
  "headers": {
    "X-Custom-Header": "value"
  }
}
```

**Response (201 Created):**
```json
{
  "data": {
    "id": "uuid",
    "name": "Zapier Integration",
    "url": "https://hooks.zapier.com/hooks/catch/...",
    "events": ["users.create", "users.update"],
    "secret": "whsec_abc123...",  // Store this securely!
    "is_active": true,
    "failure_count": 0,
    "created_at": "2024-01-15T12:00:00Z"
  },
  "message": "Webhook created successfully"
}
```

**Webhook Events Reference:**
| Event | Description |
|-------|-------------|
| auth.login | User logged in |
| users.create | New user created |
| users.update | User profile updated |
| posts.create | Post created |
| posts.publish | Post published |
| posts.delete | Post deleted |
| tiers.changed | Tenant tier changed |

### GET /api/v1/webhooks/{webhook_id}

Retrieves webhook configuration and recent delivery statistics.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "name": "Slack Integration",
    "url": "https://hooks.slack.com/services/...",
    "events": ["users.create", "posts.publish"],
    "secret": "********",  // Masked
    "is_active": true,
    "stats": {
      "total_deliveries": 150,
      "successful": 145,
      "failed": 5,
      "success_rate": 96.67
    },
    "recent_deliveries": [
      {
        "id": "uuid",
        "event": "users.create",
        "status": "success",
        "status_code": 200,
        "delivered_at": "2024-01-15T10:30:00Z",
        "duration_ms": 245
      }
    ],
    "created_at": "2024-01-01T00:00:00Z"
  },
  "message": "Webhook retrieved successfully"
}
```

### PUT /api/v1/webhooks/{webhook_id}

Updates webhook configuration.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "name": "Updated Webhook Name",
  "url": "https://new-endpoint.example.com",
  "events": ["users.create"],
  "is_active": false
}
```

**Response (200 OK):**
```json
{
  "message": "Webhook updated successfully"
}
```

### DELETE /api/v1/webhooks/{webhook_id}

Deletes a webhook subscription.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "message": "Webhook deleted successfully"
}
```

### GET /api/v1/webhooks/{webhook_id}/deliveries

Retrieves webhook delivery history for a specific webhook.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| page | int | Page number (default: 1) |
| per_page | int | Items per page (default: 20) |
| status | string | Filter by status (success, failed) |
| event | string | Filter by event type |

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "webhook_id": "uuid",
      "event": "users.create",
      "status": "success",
      "status_code": 200,
      "request_body": "{\"event\":\"users.create\"...}",
      "response_body": "{\"ok\":true}",
      "duration_ms": 245,
      "next_retry_at": null,
      "created_at": "2024-01-15T10:30:00Z"
    },
    {
      "id": "uuid",
      "webhook_id": "uuid",
      "event": "posts.publish",
      "status": "failed",
      "status_code": 500,
      "error_message": "Internal Server Error",
      "retry_count": 3,
      "next_retry_at": "2024-01-15T10:45:00Z",
      "created_at": "2024-01-15T10:30:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 150,
    "total_pages": 8
  },
  "message": "Webhook deliveries retrieved successfully"
}
```

### POST /api/v1/webhooks/{webhook_id}/deliveries/{delivery_id}/retry

Manually retries a failed webhook delivery.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "message": "Webhook delivery retry queued"
}
```

---

## Feature Flags Endpoints (`/api/v1/feature-flags/`)

The feature flags module enables progressive feature rollout and A/B testing. Flags support boolean and multivariate configurations with targeting rules.

### GET /api/v1/feature-flags/

Lists all feature flags. Requires administrative permissions (feature_flags:read).

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "key": "dark_mode",
      "name": "Dark Mode",
      "description": "Enable dark mode UI theme",
      "type": "boolean",
      "value": true,
      "rollout_percentage": null,
      "targeting_rules": [],
      "is_active": true,
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-01-15T10:00:00Z"
    },
    {
      "id": "uuid",
      "key": "new_checkout",
      "name": "New Checkout Flow",
      "description": "New checkout experience",
      "type": "boolean",
      "value": false,
      "rollout_percentage": 25,
      "targeting_rules": [
        {
          "attribute": "tenant_tier",
          "operator": "equals",
          "value": "professional"
        }
      ],
      "is_active": true,
      "created_at": "2024-01-10T00:00:00Z",
      "updated_at": "2024-01-15T10:00:00Z"
    }
  ],
  "message": "Feature flags retrieved successfully"
}
```

### POST /api/v1/feature-flags/

Creates a new feature flag. Requires administrative permissions (feature_flags:write).

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body (Boolean Flag):**
```json
{
  "key": "new_dashboard",
  "name": "New Dashboard",
  "description": "Enable new dashboard experience",
  "type": "boolean",
  "value": false,
  "rollout_percentage": 10
}
```

**Request Body (Multivariate Flag):**
```json
{
  "key": "button_color",
  "name": "Button Color",
  "description": "Button color for A/B testing",
  "type": "multivariate",
  "value": "blue",
  "variants": [
    {"key": "blue", "name": "Blue", "value": "blue"},
    {"key": "green", "name": "Green", "value": "green"},
    {"key": "red", "name": "Red", "value": "red"}
  ],
  "rollout_percentage": null,
  "distributions": [
    {"variant": "blue", "percentage": 33},
    {"variant": "green", "percentage": 33},
    {"variant": "red", "percentage": 34}
  ]
}
```

**Response (201 Created):**
```json
{
  "data": {
    "id": "uuid",
    "key": "new_dashboard",
    "name": "New Dashboard",
    "type": "boolean",
    "value": false,
    "rollout_percentage": 10,
    "is_active": true,
    "created_at": "2024-01-15T12:00:00Z"
  },
  "message": "Feature flag created successfully"
}
```

### GET /api/v1/feature-flags/{flag_key}

Retrieves a specific feature flag configuration.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "key": "dark_mode",
    "name": "Dark Mode",
    "description": "Enable dark mode UI theme",
    "type": "boolean",
    "value": true,
    "rollout_percentage": null,
    "targeting_rules": [],
    "is_active": true,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-15T10:00:00Z"
  },
  "message": "Feature flag retrieved successfully"
}
```

### PUT /api/v1/feature-flags/{flag_key}

Updates feature flag configuration.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "value": true,
  "rollout_percentage": 50,
  "description": "Updated description"
}
```

**Response (200 OK):**
```json
{
  "message": "Feature flag updated successfully"
}
```

### DELETE /api/v1/feature-flags/{flag_key}

Deletes a feature flag.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "message": "Feature flag deleted successfully"
}
```

### GET /api/v1/feature-flags/evaluate/{flag_key}

Evaluates a feature flag for the current user/tenant. This endpoint is typically called from server-side code but can be used for client-side evaluation.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "flag_key": "dark_mode",
    "value": true,
    "reason": {
      "kind": "default",
      "rule_index": -1
    }
  },
  "message": "Flag evaluated successfully"
}
```

---

## API Keys Endpoints (`/api/v1/api-keys/`)

The API keys module provides service-to-service authentication through API key credentials. API keys can have scoped permissions and are useful for server integrations.

### GET /api/v1/api-keys/

Lists all API keys for the current tenant. Requires administrative permissions (api_keys:read).

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "name": "Production Integration",
      "prefix": "sk_live_abc",
      "scopes": ["posts:read", "posts:write"],
      "expires_at": "2025-01-01T00:00:00Z",
      "last_used_at": "2024-01-15T10:30:00Z",
      "created_at": "2024-01-01T00:00:00Z",
      "created_by": "uuid"
    }
  ],
  "message": "API keys retrieved successfully"
}
```

### POST /api/v1/api-keys/

Creates a new API key. Requires administrative permissions (api_keys:write). The full key is only shown once at creation time.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "name": "Development Integration",
  "scopes": ["posts:read", "posts:write"],
  "expires_at": "2025-01-01T00:00:00Z",  // Optional: null for no expiration
  "rate_limit": 1000  // Optional: requests per hour
}
```

**Response (201 Created):**
```json
{
  "data": {
    "id": "uuid",
    "name": "Development Integration",
    "key": "sk_test_xyz123abc456...",  // IMPORTANT: Store this securely!
    "prefix": "sk_test_xyz",
    "scopes": ["posts:read", "posts:write"],
    "expires_at": "2025-01-01T00:00:00Z",
    "created_at": "2024-01-15T12:00:00Z"
  },
  "message": "API key created successfully. Store the key securely - it will not be shown again."
}
```

### GET /api/v1/api-keys/{key_id}

Retrieves API key metadata (without the key itself).

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "name": "Production Integration",
    "prefix": "sk_live_abc",
    "scopes": ["posts:read", "posts:write"],
    "expires_at": "2025-01-01T00:00:00Z",
    "last_used_at": "2024-01-15T10:30:00Z",
    "usage_count": 15000,
    "created_at": "2024-01-01T00:00:00Z",
    "created_by": "uuid"
  },
  "message": "API key retrieved successfully"
}
```

### PUT /api/v1/api-keys/{key_id}

Updates API key metadata (name, scopes, expiration). Cannot update the key value itself.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "name": "Updated Integration Name",
  "scopes": ["posts:read", "posts:write", "posts:delete"],
  "expires_at": "2026-01-01T00:00:00Z"
}
```

**Response (200 OK):**
```json
{
  "message": "API key updated successfully"
}
```

### DELETE /api/v1/api-keys/{key_id}

Revokes an API key. Revoked keys cannot be used for authentication and are immediately rejected.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "message": "API key revoked successfully"
}
```

---

## Rate Limits Endpoints (`/api/v1/rate_limits/`)

The rate limits module provides rate limiting configuration and status monitoring. Rate limits protect the API from abuse and ensure fair resource allocation.

### GET /api/v1/rate_limits/status

Retrieves current rate limit status for the authenticated user/tenant.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "user_id": "uuid",
    "tenant_id": "uuid",
    "limits": [
      {
        "endpoint_pattern": "/api/v1/users/*",
        "limit": 100,
        "remaining": 95,
        "reset_at": "2024-01-15T11:00:00Z",
        "window": "hour"
      },
      {
        "endpoint_pattern": "/api/v1/*",
        "limit": 1000,
        "remaining": 850,
        "reset_at": "2024-01-15T11:00:00Z",
        "window": "hour"
      }
    ]
  },
  "message": "Rate limit status retrieved"
}
```

### GET /api/v1/rate_limits/config

Retrieves rate limit configuration for the current tenant. Requires administrative permissions.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "tenant_id": "uuid",
    "tier": "professional",
    "limits": [
      {
        "name": "api_requests",
        "endpoint_pattern": "/api/v1/*",
        "limit": 10000,
        "window": "hour",
        "strategy": "sliding_window"
      },
      {
        "name": "users_crud",
        "endpoint_pattern": "/api/v1/users/*",
        "limit": 100,
        "window": "hour",
        "strategy": "fixed_window"
      }
    ],
    "burst_allowance": 1.2
  },
  "message": "Rate limit configuration retrieved"
}
```

### PUT /api/v1/rate_limits/config

Updates rate limit configuration for the current tenant. Tier-based limits cannot be exceeded.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "custom_limits": [
    {
      "endpoint_pattern": "/api/v1/reports/*",
      "limit": 10,
      "window": "hour"
    }
  ]
}
```

**Response (200 OK):**
```json
{
  "message": "Rate limit configuration updated"
}
```

---

## Tiers Endpoints (`/api/v1/tiers/`)

The tiers module manages subscription tiers with feature access, rate limits, and resource quotas.

### GET /api/v1/tiers/

Lists available subscription tiers.

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "name": "starter",
      "display_name": "Starter",
      "features": ["api_access", "email_support"],
      "rate_limits": {
        "api_requests": 1000,
        "users": 10
      },
      "storage_gb": 1,
      "price_monthly": 29,
      "is_active": true
    },
    {
      "id": "uuid",
      "name": "professional",
      "display_name": "Professional",
      "features": ["api_access", "email_support", "priority_support", "webhooks", "custom_domain"],
      "rate_limits": {
        "api_requests": 10000,
        "users": 100
      },
      "storage_gb": 10,
      "price_monthly": 99,
      "is_active": true
    },
    {
      "id": "uuid",
      "name": "enterprise",
      "display_name": "Enterprise",
      "features": ["api_access", "dedicated_support", "webhooks", "custom_domain", "sso", "audit_logs"],
      "rate_limits": {
        "api_requests": 100000,
        "users": -1  // Unlimited
      },
      "storage_gb": 100,
      "price_monthly": 299,
      "is_active": true
    }
  ],
  "message": "Tiers retrieved successfully"
}
```

### GET /api/v1/tiers/{tier_id}

Retrieves detailed tier information including feature list and rate limits.

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "name": "professional",
    "display_name": "Professional",
    "description": "For growing teams with advanced needs",
    "features": [
      {
        "key": "api_access",
        "name": "API Access",
        "description": "Full API access"
      },
      {
        "key": "webhooks",
        "name": "Webhooks",
        "description": "Up to 10 webhook integrations"
      },
      {
        "key": "custom_domain",
        "name": "Custom Domain",
        "description": "Use your own domain"
      }
    ],
    "rate_limits": {
      "api_requests": 10000,
      "users": 100,
      "storage_gb": 10
    },
    "price_monthly": 99,
    "price_yearly": 990,
    "is_active": true
  },
  "message": "Tier retrieved successfully"
}
```

---

## Notifications Endpoints (`/api/v1/notifications/`)

The notifications module provides notification management and delivery status tracking.

### GET /api/v1/notifications/

Lists notifications for the authenticated user. Requires authentication.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| page | int | Page number (default: 1) |
| per_page | int | Items per page (default: 20) |
| is_read | bool | Filter by read status |
| type | string | Filter by notification type |

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "user_id": "uuid",
      "type": "welcome",
      "title": "Welcome to the Platform",
      "message": "Thank you for joining us!",
      "is_read": false,
      "read_at": null,
      "data": {
        "action_url": "/getting-started"
      },
      "created_at": "2024-01-15T10:30:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 1,
    "total_pages": 1
  },
  "message": "Notifications retrieved successfully"
}
```

### GET /api/v1/notifications/{notification_id}

Retrieves a specific notification.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "type": "welcome",
    "title": "Welcome to the Platform",
    "message": "Thank you for joining us!",
    "is_read": false,
    "data": {
      "action_url": "/getting-started"
    },
    "created_at": "2024-01-15T10:30:00Z"
  },
  "message": "Notification retrieved successfully"
}
```

### PUT /api/v1/notifications/{notification_id}/read

Marks a notification as read.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "message": "Notification marked as read"
}
```

### PUT /api/v1/notifications/read

Marks all notifications as read.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "message": "All notifications marked as read"
}
```

---

## Health Check Endpoints (`/api/v1/health/`)

The health module provides health check endpoints for load balancers and orchestrators.

### GET /api/v1/health/live

Liveness probe - indicates if the application is running. This endpoint should return 200 if the process is alive, regardless of dependencies.

**Response (200 OK):**
```json
{
  "status": "alive",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### GET /api/v1/ready

Canonical readiness probe. It checks the database, Redis, ARQ queue, and EventBus registration. Only the all-healthy state returns HTTP 200; degraded dependency state returns HTTP 503 while preserving the component statuses in the body.

**Response (200 OK):**
```json
{
  "status": "healthy",
  "app": "healthy",
  "database": "healthy",
  "redis": "healthy",
  "queue": "healthy",
  "events": "healthy",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

**Response (503 Service Unavailable):**
```json
{
  "status": "degraded",
  "app": "healthy",
  "database": "healthy",
  "redis": "healthy",
  "queue": "degraded",
  "events": "healthy",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

`/api/health/ready` remains a compatibility alias with the same dependency policy and HTTP status semantics.

### GET /api/v1/health/detailed

Detailed health check with system metrics. Useful for monitoring dashboards.

**Response (200 OK):**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "uptime_seconds": 86400,
  "checks": {
    "database": {
      "status": "healthy",
      "latency_ms": 5
    },
    "redis": {
      "status": "healthy",
      "latency_ms": 2
    }
  },
  "metrics": {
    "active_users": 150,
    "requests_per_minute": 500,
    "error_rate": 0.01
  },
  "timestamp": "2024-01-15T10:30:00Z"
}
```

---

## Tasks Endpoints (`/api/v1/tasks/`)

The tasks module provides background job monitoring and management.

### GET /api/v1/tasks/

Lists background tasks. Requires administrative permissions (tasks:read).

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| page | int | Page number (default: 1) |
| per_page | int | Items per page (default: 20) |
| status | string | Filter by status (queued, running, completed, failed) |
| job_type | string | Filter by job type |

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "job_id": "arq:abc123",
      "job_type": "send_email_batch",
      "status": "completed",
      "result": {
        "sent": 150,
        "failed": 0
      },
      "created_at": "2024-01-15T10:00:00Z",
      "started_at": "2024-01-15T10:00:05Z",
      "completed_at": "2024-01-15T10:01:30Z",
      "duration_ms": 85000
    },
    {
      "id": "uuid",
      "job_id": "arq:def456",
      "job_type": "webhook_delivery",
      "status": "running",
      "progress": 65,
      "created_at": "2024-01-15T10:30:00Z",
      "started_at": "2024-01-15T10:30:05Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 100,
    "total_pages": 5
  },
  "message": "Tasks retrieved successfully"
}
```

### GET /api/v1/tasks/{task_id}

Retrieves detailed task information including progress and result.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "job_id": "arq:abc123",
    "job_type": "send_email_batch",
    "status": "completed",
    "progress": 100,
    "result": {
      "sent": 150,
      "failed": 0
    },
    "error": null,
    "retry_count": 0,
    "created_at": "2024-01-15T10:00:00Z",
    "started_at": "2024-01-15T10:00:05Z",
    "completed_at": "2024-01-15T10:01:30Z",
    "duration_ms": 85000
  },
  "message": "Task retrieved successfully"
}
```

### POST /api/v1/tasks/{task_id}/retry

Retries a failed task.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "message": "Task retry queued"
}
```

---

## Posts Endpoints (`/api/v1/posts/`)

The posts module provides content management capabilities.

### GET /api/v1/posts/

Lists published posts. Public endpoint.

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| page | int | Page number (default: 1) |
| per_page | int | Items per page (default: 10) |
| status | string | Filter by status (draft, published, archived) |
| category | string | Filter by category |
| search | string | Search in title and content |

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "uuid",
      "title": "Getting Started Guide",
      "slug": "getting-started-guide",
      "excerpt": "Learn how to get started with our platform",
      "content": "...",
      "status": "published",
      "author": {
        "id": "uuid",
        "name": "Admin"
      },
      "category": "guides",
      "tags": ["getting-started", "tutorial"],
      "published_at": "2024-01-10T00:00:00Z",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 10,
    "total": 50,
    "total_pages": 5
  },
  "message": "Posts retrieved successfully"
}
```

### GET /api/v1/posts/{post_id}

Retrieves a specific post by ID.

**Response (200 OK):**
```json
{
  "data": {
    "id": "uuid",
    "title": "Getting Started Guide",
    "slug": "getting-started-guide",
    "content": "Full content here...",
    "status": "published",
    "author": {
      "id": "uuid",
      "name": "Admin",
      "avatar_url": "https://..."
    },
    "category": "guides",
    "tags": ["getting-started", "tutorial"],
    "reading_time_minutes": 5,
    "published_at": "2024-01-10T00:00:00Z",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-05T00:00:00Z"
  },
  "message": "Post retrieved successfully"
}
```

### POST /api/v1/posts/

Creates a new post. Requires authentication and appropriate permissions.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "title": "New Blog Post",
  "content": "Post content in markdown...",
  "status": "draft",
  "category": "updates",
  "tags": ["announcement"],
  "scheduled_publish_at": "2024-01-20T00:00:00Z"
}
```

**Response (201 Created):**
```json
{
  "data": {
    "id": "uuid",
    "title": "New Blog Post",
    "slug": "new-blog-post",
    "status": "draft",
    "created_at": "2024-01-15T12:00:00Z"
  },
  "message": "Post created successfully"
}
```

### PUT /api/v1/posts/{post_id}

Updates a post (full update).

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "title": "Updated Title",
  "content": "Updated content...",
  "status": "published"
}
```

**Response (200 OK):**
```json
{
  "message": "Post updated successfully"
}
```

### PATCH /api/v1/posts/{post_id}

Partially updates a post.

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "status": "published"
}
```

**Response (200 OK):**
```json
{
  "message": "Post updated successfully"
}
```

### DELETE /api/v1/posts/{post_id}

Deletes a post (soft delete).

**Authenticated Request:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "message": "Post deleted successfully"
}
```

---

## Integration Patterns

### Authentication Integration

To authenticate API requests, include the JWT access token in the Authorization header:

```bash
curl -X GET "https://api.example.com/api/v1/users/me" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
```

For API key authentication (service-to-service):

```bash
curl -X GET "https://api.example.com/api/v1/posts/" \
  -H "X-API-Key: sk_live_abc123..."
```

### Error Handling Pattern

Implement comprehensive error handling in your client:

```python
import requests
from typing import Optional

class APIError(Exception):
    def __init__(self, message: str, code: str, details: Optional[dict] = None):
        self.message = message
        self.code = code
        self.details = details

def make_request(method: str, url: str, **kwargs) -> dict:
    response = requests.request(method, url, **kwargs)
    
    if not response.ok:
        error_data = response.json()
        raise APIError(
            message=error_data.get("message", "Request failed"),
            code=error_data.get("error", {}).get("code", "UNKNOWN"),
            details=error_data.get("error", {}).get("details")
        )
    
    return response.json()
```

### Rate Limit Handling

Handle rate limit responses gracefully:

```python
def handle_rate_limit(response):
    if response.status_code == 429:
        reset_at = response.headers.get("X-RateLimit-Reset")
        wait_time = int(reset_at) - int(time.time()) if reset_at else 60
        time.sleep(min(wait_time, 60))
        return True  # Retry
    return False
```

### Webhook Verification

Verify webhook signatures in your endpoint:

```python
import hmac
import hashlib

def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

## Summary

This API reference provides comprehensive documentation for all endpoints in the FastAPI SaaS . The API follows RESTful conventions with consistent response formats, proper authentication mechanisms, and comprehensive error handling. All endpoints are versioned under `/api/v1/` to enable future API evolution without breaking existing integrations.

Key integration points include JWT authentication for user-facing applications, API key authentication for service-to-service integrations, webhook delivery for event notifications, and feature flags for progressive feature rollout. The multi-tenancy architecture ensures data isolation between tenants while providing a seamless developer experience.
