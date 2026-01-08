# Backend Migration Plan

**Old Backend:** `D:\project\full_stack_fastapi_react_template\backend` (MongoDB + Beanie)
**New Backend:** `D:\project\FastAPI_saas` (PostgreSQL + SQLAlchemy 2.0)
**Frontend:** `D:\project\full_stack_fastapi_react_template\frontend`

**Goal:** Migrate all features from old backend to new backend to maintain frontend compatibility.

---

## Table of Contents

1. [Architecture Comparison](#architecture-comparison)
2. [Already Implemented Features](#already-implemented-features)
3. [Phase 1: Authentication Enhancements](#phase-1-authentication-enhancements)
4. [Phase 2: User Management](#phase-2-user-management)
5. [Phase 3: Billing \& Payments](#phase-3-billing--payments)
6. [Phase 4: File Storage](#phase-4-file-storage)
7. [Phase 5: AI Features](#phase-5-ai-features)
8. [Phase 6: RBAC Enhancements](#phase-6-rbac-enhancements)
9. [Phase 7: Audit Logs Enhancements](#phase-7-audit-logs-enhancements)
10. [Database Migration Summary](#database-migration-summary)
11. [File Summary](#file-summary)

---

## Architecture Comparison

| Aspect | Old Backend (MongoDB) | New Backend (PostgreSQL) |
|--------|----------------------|--------------------------|
| **Database** | MongoDB + Beanie ODM | PostgreSQL + SQLAlchemy 2.0 |
| **Python Version** | >= 3.13 | 3.11 - 3.12 |
| **DI Container** | punq | Manual (Depends) |
| **API Docs** | Not specified | `/docs`, `/redoc` |
| **ORM** | Beanie Document | SQLAlchemy Model |
| **Validation** | Pydantic v2 | Pydantic v2 |
| **Auth** | JWT (HS256/HS512) + pbkdf2 | JWT + bcrypt |
| **Task Queue** | Celery + Redis | ARQ (Redis) |
| **Admin UI** | None | CRUDAdmin |
| **File Storage** | S3, Azure, Local | Not implemented |
| **Payments** | Stripe | Not implemented |
| **Email** | SMTP | aiosmtplib |

---

## Already Implemented Features

| Feature | Status | Location |
|---------|--------|----------|
| Multi-tenancy | ✅ Complete | `src/app/modules/tenants/` |
| JWT Authentication | ✅ Complete | `src/app/modules/auth/` |
| User Management | ✅ Complete | `src/app/modules/users/` |
| RBAC (Roles/Permissions) | ✅ Complete | `src/app/modules/rbac/` |
| Rate Limiting | ✅ Complete | `src/app/modules/rate_limits/` |
| Feature Flags | ✅ Complete | `src/app/modules/feature_flags/` |
| Audit Logs | ✅ Complete | `src/app/modules/audit_logs/` |
| Notifications | ✅ Complete | `src/app/modules/notifications/` |
| Webhooks | ✅ Complete | `src/app/modules/webhooks/` |
| Health Check | ✅ Complete | `src/app/modules/health/` |
| API Keys | ✅ Complete | `src/app/modules/api_keys/` |

---

## Phase 1: Authentication Enhancements

**Priority:** HIGH
**Total Files:** 24 (22 new, 2 modified)

### 1.1 Passkey/WebAuthn Module

**Files to Create:**

```
src/app/modules/passkey/
├── __init__.py
├── models.py
├── router.py
├── schemas.py
├── service.py
└── crud.py
```

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/passkey/register` | Register new passkey |
| POST | `/auth/passkey/verify` | Verify passkey challenge |
| POST | `/auth/passkey/login` | Login with passkey |
| GET | `/auth/passkey/credentials` | List user's passkeys |
| DELETE | `/auth/passkey/credentials/{id}` | Delete passkey |

**Database Table:**

```sql
CREATE TABLE passkey_credentials (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    credential_id BYTEA NOT NULL UNIQUE,
    public_key BYTEA NOT NULL,
    counter BIGINT NOT NULL DEFAULT 0,
    device_type VARCHAR(50) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_passkey_credentials_user_id ON passkey_credentials(user_id);
```

---

### 1.2 Magic Link Login Module

**Files to Create:**

```
src/app/modules/magic_link/
├── __init__.py
├── models.py
├── router.py
├── schemas.py
├── service.py
└── crud.py
```

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/magic-link/send` | Send magic link email |
| POST | `/auth/magic-link/verify` | Verify magic link token |

**Database Table:**

```sql
CREATE TABLE magic_links (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    token VARCHAR(255) NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_magic_links_token ON magic_links(token);
CREATE INDEX idx_magic_links_user_id ON magic_links(user_id);
```

---

### 1.3 SSO Integration Module

**Files to Create:**

```
src/app/modules/sso/
├── __init__.py
├── models.py
├── router.py
├── schemas.py
├── service.py
└── crud.py

src/app/modules/sso_settings/
├── __init__.py
├── models.py
├── router.py
├── schemas.py
└── service.py
```

**Supported Providers:**
- Google
- GitHub
- Microsoft (Azure AD)
- Apple

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/auth/sso/providers` | List available SSO providers |
| GET | `/auth/sso/{provider}/callback` | SSO callback handler |
| POST | `/auth/sso/{provider}/connect` | Connect SSO to account |
| DELETE | `/auth/sso/{provider}` | Disconnect SSO from account |

**Database Tables:**

```sql
-- SSO Provider Settings (host-level)
CREATE TABLE sso_settings (
    id SERIAL PRIMARY KEY,
    provider VARCHAR(50) NOT NULL UNIQUE,
    client_id VARCHAR(255) NOT NULL,
    client_secret VARCHAR(255) NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

-- User SSO Connections (tenant/user-level)
CREATE TABLE user_sso_connections (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    provider VARCHAR(50) NOT NULL,
    provider_user_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    UNIQUE(user_id, provider)
);

CREATE INDEX idx_user_sso_connections_user_id ON user_sso_connections(user_id);
CREATE INDEX idx_user_sso_connections_provider ON user_sso_connections(provider);
```

---

### 1.4 Auth Module Enhancements

**Files to Modify:**

- `src/app/modules/auth/schemas.py`
- `src/app/modules/auth/service.py`

**Add to Schemas:**

```python
class EmailVerificationConfirm(BaseModel):
    token: str

class EmailVerificationRequest(BaseModel):
    email: EmailStr

class MagicLinkRequest(BaseModel):
    email: EmailStr

class MagicLinkConfirm(BaseModel):
    token: str

class PasskeyRegisterRequest(BaseModel):
    passkey_data: dict

class PasskeyVerifyRequest(BaseModel):
    passkey_data: dict

class SSOConnectRequest(BaseModel):
    provider: str
    code: str
```

---

## Phase 2: User Management

**Priority:** HIGH
**Total Files:** 9 (6 new, 3 modified)

### 2.1 User Model Enhancements

**Files to Modify:**

- `src/app/modules/users/models.py`
- `src/app/modules/users/schemas.py`
- `src/app/modules/users/router.py`

**Add to User Model:**

```python
# In src/app/modules/users/models.py - add columns to User class
image_url: Mapped[str | None] = mapped_column(String(500), default=None)
activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
sso_provider_id: Mapped[str | None] = mapped_column(String(100), default=None)

# Add Gender enum
class Gender(str, Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"
```

### 2.2 User Preferences Module

**Files to Create:**

```
src/app/modules/user_preferences/
├── __init__.py
├── models.py
├── router.py
├── schemas.py
├── service.py
└── crud.py
```

**Database Table:**

```sql
CREATE TABLE user_preferences (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    preferences JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

CREATE INDEX idx_user_preferences_user_id ON user_preferences(user_id);
```

**Default Preferences Structure:**

```json
{
  "theme": "light",
  "language": "en",
  "email_notifications": true,
  "push_notifications": false,
  "timezone": "UTC",
  "date_format": "YYYY-MM-DD",
  "currency": "USD"
}
```

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/user/me/preferences` | Get user preferences |
| PATCH | `/user/me/preferences` | Update preferences |

### 2.3 User Profile Images

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| PATCH | `/user/me/image` | Update profile image |

---

## Phase 3: Billing & Payments

**Priority:** MEDIUM
**Total Files:** 22 (22 new, 0 modified)

### 3.1 Stripe Integration Module

**Files to Create:**

```
src/app/modules/stripe/
├── __init__.py
├── models.py
├── router.py
├── schemas.py
├── service.py
└── crud.py
```

**Database Table:**

```sql
-- Host-level Stripe settings
CREATE TABLE stripe_settings (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    stripe_customer_id VARCHAR(255) NOT NULL,
    stripe_account_id VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

-- Billing records
CREATE TABLE billing_records (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    amount INTEGER NOT NULL,  -- in cents
    currency VARCHAR(3) NOT NULL DEFAULT 'usd',
    status VARCHAR(50) NOT NULL,
    stripe_charge_id VARCHAR(255),
    stripe_payment_intent_id VARCHAR(255),
    description VARCHAR(500),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX idx_billing_records_user_id ON billing_records(user_id);
CREATE INDEX idx_billing_records_tenant_id ON billing_records(tenant_id);
CREATE INDEX idx_billing_records_stripe_charge_id ON billing_records(stripe_charge_id);
```

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/stripe/settings` | Get Stripe settings |
| POST | `/stripe/settings` | Update Stripe settings |
| POST | `/stripe/connect` | Connect Stripe account |
| POST | `/stripe/webhook` | Stripe webhook handler |

---

### 3.2 Subscription Plans Module

**Files to Create:**

```
src/app/modules/subscription_plans/
├── __init__.py
├── models.py
├── router.py
├── schemas.py
├── service.py
└── crud.py
```

**Database Table:**

```sql
CREATE TABLE subscription_plans (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    price_monthly INTEGER NOT NULL,  -- in cents
    price_yearly INTEGER NOT NULL,   -- in cents
    features JSONB NOT NULL DEFAULT '[]',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_popular BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX idx_subscription_plans_tenant_id ON subscription_plans(tenant_id);
```

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/subscription-plans` | List all plans |
| POST | `/subscription-plans` | Create plan (superuser) |
| GET | `/subscription-plans/{id}` | Get plan details |
| PATCH | `/subscription-plans/{id}` | Update plan |
| DELETE | `/subscription-plans/{id}` | Delete plan |

---

### 3.3 Products Module

**Files to Create:**

```
src/app/modules/products/
├── __init__.py
├── models.py
├── router.py
├── schemas.py
├── service.py
└── crud.py
```

**Database Table:**

```sql
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX idx_products_tenant_id ON products(tenant_id);
```

---

### 3.4 Pricing Module

**Files to Create:**

```
src/app/modules/pricing/
├── __init__.py
├── models.py
├── router.py
├── schemas.py
├── service.py
└── crud.py
```

**Database Table:**

```sql
CREATE TABLE pricing (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    tier_name VARCHAR(100) NOT NULL,
    price INTEGER NOT NULL,  -- in cents
    interval VARCHAR(20) NOT NULL,  -- 'monthly', 'yearly', 'one_time'
    features JSONB NOT NULL DEFAULT '[]',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE INDEX idx_pricing_product_id ON pricing(product_id);
```

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/products` | List products |
| POST | `/products` | Create product |
| GET | `/products/{id}` | Get product |
| DELETE | `/products/{id}` | Delete product |
| GET | `/products/{id}/pricing` | Get product pricing |
| POST | `/products/{id}/pricing` | Create pricing tier |

---

### 3.5 Billing History Module

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/billing/history` | Get billing history |
| GET | `/billing/invoices` | List invoices |
| GET | `/billing/invoices/{id}` | Get invoice details |
| POST | `/billing/checkout` | Create checkout session |
| POST | `/billing/cancel` | Cancel subscription |

---

## Phase 4: File Storage

**Priority:** MEDIUM
**Total Files:** 16 (16 new, 0 modified)

### 4.1 Storage Settings Module

**Files to Create:**

```
src/app/modules/storage_settings/
├── __init__.py
├── models.py
├── router.py
├── schemas.py
├── service.py
└── crud.py
```

**Database Table:**

```sql
CREATE TABLE storage_settings (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL DEFAULT 'local',  -- 's3', 'azure', 'local'
    s3_bucket_name VARCHAR(255),
    s3_region VARCHAR(50),
    s3_access_key_id VARCHAR(255),
    s3_secret_access_key VARCHAR(255),
    azure_container_name VARCHAR(255),
    azure_connection_string TEXT,
    local_storage_path VARCHAR(500),
    max_file_size_mb INTEGER NOT NULL DEFAULT 100,
    allowed_file_types JSONB NOT NULL DEFAULT '["image/*", "application/pdf"]',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
```

---

### 4.2 Files Module

**Files to Create:**

```
src/app/modules/files/
├── __init__.py
├── models.py
├── router.py
├── schemas.py
├── service.py
├── crud.py
└── externals/
    ├── __init__.py
    ├── s3_storage.py
    ├── azure_storage.py
    └── local_storage.py
```

**Database Table:**

```sql
CREATE TABLE files (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    filename VARCHAR(255) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    file_size BIGINT NOT NULL,  -- in bytes
    storage_provider VARCHAR(50) NOT NULL DEFAULT 'local',
    is_public BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX idx_files_user_id ON files(user_id);
CREATE INDEX idx_files_tenant_id ON files(tenant_id);
CREATE INDEX idx_files_mime_type ON files(mime_type);
```

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/storage/settings` | Get storage settings |
| PATCH | `/storage/settings` | Update storage settings |
| POST | `/files/upload` | Upload file |
| GET | `/files/{file_id}` | Download file |
| DELETE | `/files/{file_id}` | Delete file |
| GET | `/files` | List uploaded files |
| GET | `/files/{file_id}/info` | Get file metadata |

---

## Phase 5: AI Features

**Priority:** LOW
**Total Files:** 16 (16 new, 0 modified)

### 5.1 AI Chat Module

**Files to Create:**

```
src/app/modules/ai_chat/
├── __init__.py
├── models.py
├── router.py
├── schemas.py
├── service.py
└── crud.py
```

**Database Tables:**

```sql
CREATE TABLE ai_chat_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    title VARCHAR(255),
    model VARCHAR(100) NOT NULL DEFAULT 'ollama',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE TABLE ai_chat_messages (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,  -- 'user', 'assistant', 'system'
    content TEXT NOT NULL,
    token_count INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    FOREIGN KEY (session_id) REFERENCES ai_chat_sessions(id) ON DELETE CASCADE
);

CREATE INDEX idx_ai_chat_sessions_user_id ON ai_chat_sessions(user_id);
CREATE INDEX idx_ai_chat_messages_session_id ON ai_chat_messages(session_id);
```

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/ai/chat/sessions` | Create chat session |
| GET | `/ai/chat/sessions` | List sessions |
| GET | `/ai/chat/sessions/{id}` | Get session |
| DELETE | `/ai/chat/sessions/{id}` | Delete session |
| POST | `/ai/chat/sessions/{id}/messages` | Send message |
| GET | `/ai/chat/sessions/{id}/messages` | Get chat history |

---

### 5.2 AI Settings Module

**Database Table:**

```sql
CREATE TABLE ai_settings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    model VARCHAR(100) NOT NULL DEFAULT 'llama3',
    temperature FLOAT NOT NULL DEFAULT 0.7,
    max_tokens INTEGER NOT NULL DEFAULT 2048,
    system_prompt TEXT,
    preferences JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);
```

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/ai/settings` | Get AI settings |
| PATCH | `/ai/settings` | Update AI settings |
| GET | `/ai/models` | List available models |

---

## Phase 6: RBAC Enhancements

**Priority:** HIGH
**Total Files:** 3 (0 new, 3 modified)

### 6.1 Permission Enum Extensions

**File to Modify:**

- `src/app/core/rbac/permissions.py`

**Add Missing Permissions:**

```python
class Permission(Enum):
    # Existing permissions...

    # User permissions
    USER_SELF_READ_AND_WRITE_ONLY = "user:self_read_and_write_only"
    USER_DELETE_ONLY = "user:delete_only"
    USER_ROLE_ASSIGN_OR_REMOVE_ONLY = "role:assign_or_remove_only"

    # Management permissions
    MANAGE_BILLING = "manage:billing"
    MANAGE_PRODUCTS_AND_PRICING = "manage:products_and_pricing"
    MANAGE_STORAGE_SETTINGS = "manage:storage_settings"

    # Audit permissions
    AUDIT_LOGS_VIEW_ONLY = "audit_logs:view_only"
    AUDIT_LOGS_DOWNLOAD = "audit_logs:download"
```

---

### 6.2 Self-Access Permission Logic

**File to Modify:**

- `src/app/modules/rbac/service.py`

**Add Method:**

```python
async def check_self_access(self, user_id: int, resource_id: int) -> bool:
    """Check if user is accessing their own resource."""
    return user_id == resource_id
```

---

### 6.3 RBAC Service Enhancement

**File to Modify:**

- `src/app/core/rbac/dependencies.py`

**Add Logic:**

```python
async def check_self_access_permission(
    current_user: dict,
    resource_id: int,
    db: AsyncSession
) -> bool:
    """Check if user has self-access permission for the resource."""
    # Check if user has USER_SELF_READ_AND_WRITE_ONLY permission
    # If yes, allow access only to their own resource
    pass
```

---

## Phase 7: Audit Logs Enhancements

**Priority:** MEDIUM
**Total Files:** 2 (0 new, 2 modified)

### 7.1 Export and Download Endpoints

**File to Modify:**

- `src/app/modules/audit_logs/router.py`

**Add Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/audit-logs/export` | Export logs as JSONL |
| GET | `/audit-logs/download` | Download log file |

**Implementation:**

```python
@router.get("/export")
async def export_audit_logs(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    format: str = "jsonl",  # jsonl, csv
) -> StreamingResponse:
    """Export audit logs as JSONL file."""
    # Query audit logs with filters
    # Stream response as file download
    pass

@router.get("/download/{file_id}")
async def download_audit_log(
    file_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> FileResponse:
    """Download a specific audit log file."""
    # Return file from storage
    pass
```

---

## Database Migration Summary

### SQL Migration Script

```sql
-- ============================================
-- USER ENHANCEMENTS
-- ============================================

-- Add new columns to users table
ALTER TABLE users ADD COLUMN IF NOT EXISTS image_url VARCHAR(500);
ALTER TABLE users ADD COLUMN IF NOT EXISTS activated_at TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS sso_provider_id VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS gender VARCHAR(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS preferences JSONB DEFAULT '{}';

-- Create user_preferences table
CREATE TABLE IF NOT EXISTS user_preferences (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    preferences JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_preferences_user_id ON user_preferences(user_id);

-- ============================================
-- AUTH TABLES
-- ============================================

-- Passkey credentials
CREATE TABLE IF NOT EXISTS passkey_credentials (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credential_id BYTEA NOT NULL UNIQUE,
    public_key BYTEA NOT NULL,
    counter BIGINT NOT NULL DEFAULT 0,
    device_type VARCHAR(50) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_passkey_credentials_user_id ON passkey_credentials(user_id);

-- Magic links
CREATE TABLE IF NOT EXISTS magic_links (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token VARCHAR(255) NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_magic_links_token ON magic_links(token);
CREATE INDEX IF NOT EXISTS idx_magic_links_user_id ON magic_links(user_id);

-- SSO settings (host-level)
CREATE TABLE IF NOT EXISTS sso_settings (
    id SERIAL PRIMARY KEY,
    provider VARCHAR(50) NOT NULL UNIQUE,
    client_id VARCHAR(255) NOT NULL,
    client_secret VARCHAR(255) NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

-- User SSO connections (tenant-level)
CREATE TABLE IF NOT EXISTS user_sso_connections (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL,
    provider_user_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_user_sso_connections_user_id ON user_sso_connections(user_id);

-- ============================================
-- BILLING TABLES
-- ============================================

-- Stripe settings
CREATE TABLE IF NOT EXISTS stripe_settings (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    stripe_customer_id VARCHAR(255) NOT NULL,
    stripe_account_id VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

-- Billing records
CREATE TABLE IF NOT EXISTS billing_records (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    amount INTEGER NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'usd',
    status VARCHAR(50) NOT NULL,
    stripe_charge_id VARCHAR(255),
    stripe_payment_intent_id VARCHAR(255),
    description VARCHAR(500),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_billing_records_user_id ON billing_records(user_id);
CREATE INDEX IF NOT EXISTS idx_billing_records_tenant_id ON billing_records(tenant_id);

-- Subscription plans
CREATE TABLE IF NOT EXISTS subscription_plans (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    price_monthly INTEGER NOT NULL,
    price_yearly INTEGER NOT NULL,
    features JSONB NOT NULL DEFAULT '[]',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_popular BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_subscription_plans_tenant_id ON subscription_plans(tenant_id);

-- Products
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_products_tenant_id ON products(tenant_id);

-- Pricing
CREATE TABLE IF NOT EXISTS pricing (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    tier_name VARCHAR(100) NOT NULL,
    price INTEGER NOT NULL,
    interval VARCHAR(20) NOT NULL,
    features JSONB NOT NULL DEFAULT '[]',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pricing_product_id ON pricing(product_id);

-- ============================================
-- STORAGE TABLES
-- ============================================

-- Storage settings
CREATE TABLE IF NOT EXISTS storage_settings (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL DEFAULT 'local',
    s3_bucket_name VARCHAR(255),
    s3_region VARCHAR(50),
    s3_access_key_id VARCHAR(255),
    s3_secret_access_key VARCHAR(255),
    azure_container_name VARCHAR(255),
    azure_connection_string TEXT,
    local_storage_path VARCHAR(500),
    max_file_size_mb INTEGER NOT NULL DEFAULT 100,
    allowed_file_types JSONB NOT NULL DEFAULT '["image/*", "application/pdf"]',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

-- Files
CREATE TABLE IF NOT EXISTS files (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    filename VARCHAR(255) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    file_size BIGINT NOT NULL,
    storage_provider VARCHAR(50) NOT NULL DEFAULT 'local',
    is_public BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_files_user_id ON files(user_id);
CREATE INDEX IF NOT EXISTS idx_files_tenant_id ON files(tenant_id);

-- ============================================
-- AI TABLES
-- ============================================

-- AI chat sessions
CREATE TABLE IF NOT EXISTS ai_chat_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    title VARCHAR(255),
    model VARCHAR(100) NOT NULL DEFAULT 'ollama',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

-- AI chat messages
CREATE TABLE IF NOT EXISTS ai_chat_messages (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    FOREIGN KEY (session_id) REFERENCES ai_chat_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_user_id ON ai_chat_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_session_id ON ai_chat_messages(session_id);

-- AI settings
CREATE TABLE IF NOT EXISTS ai_settings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    model VARCHAR(100) NOT NULL DEFAULT 'llama3',
    temperature FLOAT NOT NULL DEFAULT 0.7,
    max_tokens INTEGER NOT NULL DEFAULT 2048,
    system_prompt TEXT,
    preferences JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);
```

---

## File Summary

| Phase | New Files | Modified Files | Total |
|-------|-----------|----------------|-------|
| 1. Authentication | 22 | 2 | 24 |
| 2. Users | 6 | 3 | 9 |
| 3. Billing | 22 | 0 | 22 |
| 4. Storage | 16 | 0 | 16 |
| 5. AI | 16 | 0 | 16 |
| 6. RBAC | 0 | 3 | 3 |
| 7. Audit Logs | 0 | 2 | 2 |
| **TOTAL** | **82** | **10** | **92** |

---

## Module Structure Pattern

Each new module should follow this pattern:

```
src/app/modules/{module_name}/
├── __init__.py           # Lazy exports
├── models.py             # SQLAlchemy models
├── schemas.py            # Pydantic schemas (Create, Read, Update, List)
├── router.py             # API endpoints
├── service.py            # Business logic
├── crud.py               # Database operations (optional, if not using fastcrud)
└── externals/            # External service integrations (optional)
```

---

## Recommended Starting Points

1. **Start with Phase 1 (Authentication):** Passkey, Magic Link, SSO
2. **Then Phase 2 (User Management):** Profile images, preferences
3. **Then Phase 3 (Billing):** Stripe integration
4. **Then Phase 4 (Storage):** File upload/download
5. **Then Phase 5 (AI):** Chat features
6. **Then Phase 6 (RBAC):** Permission enhancements
7. **Finally Phase 7 (Audit):** Export features

---

## Notes

- All new modules should follow the existing project patterns
- Use SQLAlchemy 2.0 style with async/await
- Use Pydantic v2 for validation
- Follow the existing RBAC patterns for permission checking
- Use the existing DI pattern with FastAPI Depends
- Integrate with existing audit logging system
- Follow existing error handling patterns

---

**Generated:** 2026-01-02
**Version:** 1.0
