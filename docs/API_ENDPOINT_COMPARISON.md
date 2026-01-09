# API Endpoint Comparison Report: Old Backend vs New Backend

**Generated:** 2026-01-09
**Purpose:** Verify API endpoint compatibility between old and new backends for seamless frontend integration

## Overview

- **Old Backend:** `D:\project\full_stack_fastapi_react_template\backend` (MongoDB + Beanie ODM)
- **New Backend:** `D:\project\FastAPI_saas` (PostgreSQL + SQLAlchemy 2.0)
- **Frontend:** `D:\project\full_stack_fastapi_react_template\frontend`

**Goal:** Ensure all endpoints are exactly the same so the frontend works seamlessly with the new backend.

---

## Summary of Route Prefix Differences

| Feature | Old Backend Prefix | New Backend Prefix | Status |
|---------|-------------------|-------------------|--------|
| **Authentication** | `/account` | `/auth` | ❌ Different |
| **Tenants** | `/tenants` | `/tenants` | ✅ Same |
| **Users** | `/users` | `/user`, `/users` | ⚠️ Needs unification |
| **Roles** | `/roles` | `/roles` | ✅ Same |
| **Permissions** | `/permissions` | None (in roles) | ❌ Missing |
| **AI Chat** | `/ai` | `/ai` | ✅ Same |
| **Products** | `/products` | `/billing/products` | ❌ Different |
| **Prices** | `/prices` | `/billing/plans` (pricing) | ❌ Different |
| **Checkout** | `/checkouts` | `/billing/checkout` | ❌ Different |
| **Invoices** | `/invoices` | `/billing/records` | ❌ Different |
| **Billing Plans** | `/billing/plans` | `/billing/plans` | ✅ Same |
| **Stripe Config** | `/configurations/stripe` | `/billing/settings` | ❌ Different |
| **Storage** | `/storage` | `/storage-settings` | ❌ Different |
| **Audit Logs** | `/audit-logs` | `/audit-logs` | ✅ Same |
| **SSO Settings** | `/ssos` | `/admin/sso-settings` | ❌ Different |
| **Notifications** | `/notifications` | `/notifications` | ✅ Same |
| **App Config** | `/app_configuration` | None | ❌ Missing |
| **Dashboard** | `/dashboard` | None | ❌ Missing |
| **Features** | `/features` | `/feature-flags` | ❌ Different |
| **Health** | `/health` | `/health` | ✅ Same |
| **Passkey/Security** | `/security` | `/auth/passkey` | ❌ Different |

---

## Detailed Endpoint Comparison

### 1. Authentication (`/account` → `/auth`)

#### OLD: `/api/v1/account`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/login` | Login with email/password |
| POST | `/logout` | Logout user |
| POST | `/refresh` | Refresh access token |
| POST | `/register` | Register new user |
| POST | `/forgot-password` | Request password reset |
| POST | `/reset-password` | Reset password with token |
| POST | `/send-verification` | Send email verification |
| POST | `/verify-email` | Verify email with token |
| GET | `/sso/{provider}` | SSO OAuth initiation |
| GET | `/sso/{provider}/callback` | SSO OAuth callback |

#### NEW: `/api/v1/auth`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/login` | Login with email/password |
| POST | `/logout` | Logout user |
| POST | `/refresh` | Refresh access token |
| PATCH | `/password` | Change password |
| POST | `/forgot-password` | Request password reset |
| POST | `/reset-password` | Reset password with token |
| POST | `/send-verification` | Send email verification |
| POST | `/verify-email` | Verify email with token |

#### NEW: `/api/v1/auth/sso` (SSO broken out)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/providers` | List available SSO providers |
| GET | `/{provider}/authorize` | SSO OAuth initiation |
| GET/POST | `/{provider}/callback` | SSO OAuth callback |
| POST | `/{provider}/connect` | Connect SSO to existing account |
| DELETE | `/{provider}` | Disconnect SSO provider |

#### 🔧 CHANGES NEEDED:
- Rename `/auth` prefix to `/account`
- Move SSO from `/auth/sso/*` to `/account/sso/*`
- Add `POST /register` endpoint (missing in new backend)

---

### 2. Users (`/users` → mixed)

#### OLD: `/api/v1/users`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List users |
| POST | `/` | Create user |
| GET | `/me` | Get current user |
| GET | `/{user_id}` | Get user by ID |
| PUT | `/{user_id}` | Update user |
| DELETE | `/{user_id}` | Delete user |

#### NEW: No consistent prefix (routes at root and `/user/me`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/user` | Create user |
| GET | `/users` | List users |
| GET | `/user/me/` | Get current user |
| GET | `/user/{username}` | Get user by username |
| PATCH | `/user/{username}` | Update user |
| DELETE | `/user/{username}` | Delete user |
| GET | `/user/{username}/rate_limits` | Get rate limits |
| GET/PATCH | `/user/{username}/tier` | Get/update tier |

#### 🔧 CHANGES NEEDED:
- Add `/users` prefix to all user endpoints
- Change `/{username}` to `/{user_id}` (or support both)
- Change `/user/me` to `/users/me`
- Change `PATCH` to `PUT` for full update
- Remove `/rate_limits` and `/tier` endpoints (not in old backend)

---

### 3. Products (`/products` → `/billing/products`)

#### OLD: `/api/v1/products`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List products |
| POST | `/` | Create product |
| GET | `/{product_id:path}` | Get product |
| PATCH | `/{product_id:path}` | Update product |
| DELETE | `/{product_id:path}` | Delete product |

#### NEW: `/api/v1/billing/products`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List products |
| POST | `/` | Create product |
| GET | `/{product_id}` | Get product |
| PATCH | `/{product_id}` | Update product |
| DELETE | `/{product_id}` | Delete product |

#### 🔧 CHANGES NEEDED:
- Create separate router with `/products` prefix (OR)
- Add alias routes at `/products/*`

---

### 4. Prices (`/prices` → `/billing/plans` for pricing)

#### OLD: `/api/v1/prices`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List prices |
| POST | `/` | Create price |
| PATCH | `/{price_id:path}` | Update price |

#### NEW: Pricing is embedded in `/api/v1/billing/plans`
- Pricing is handled within plans, not as a separate entity

#### 🔧 CHANGES NEEDED:
- Create `/prices` router with endpoints for price CRUD
- Prices should be standalone entities linked to products

---

### 5. Checkout (`/checkouts` → `/billing/checkout`)

#### OLD: `/api/v1/checkouts`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/` | Create checkout session |
| POST | `/cancel` | Cancel subscription |
| GET | `/current` | Get current subscription |

#### NEW: `/api/v1/billing`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/checkout` | Create checkout session |
| GET | `/subscriptions/me` | Get current subscription |
| POST | `/subscriptions/{id}/cancel` | Cancel subscription |

#### 🔧 CHANGES NEEDED:
- Create `/checkouts` router OR add alias routes
- Change `/subscriptions/me` to `/checkouts/current`
- Change cancel path to `/checkouts/cancel`

---

### 6. Invoices (`/invoices` → `/billing/records`)

#### OLD: `/api/v1/invoices`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List invoices |

#### NEW: `/api/v1/billing/records`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List billing records |
| GET | `/{id}` | Get billing record |
| GET | `/summary` | Get billing summary |

#### 🔧 CHANGES NEEDED:
- Create `/invoices` router OR add alias routes

---

### 7. Stripe Configuration (`/configurations/stripe` → `/billing/settings`)

#### OLD: `/api/v1/configurations/stripe`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Get Stripe settings |
| POST | `/` | Create Stripe settings |
| POST | `/webhooks` | Stripe webhook handler |

#### NEW: `/api/v1/billing`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/settings` | Get billing settings |
| PATCH | `/settings` | Update billing settings |
| POST | `/webhook` | Stripe webhook handler |

#### 🔧 CHANGES NEEDED:
- Create `/configurations/stripe` router OR add alias routes
- Add `/webhooks` endpoint at `/configurations/stripe/webhooks`

---

### 8. Storage (`/storage` → `/storage-settings`)

#### OLD: `/api/v1/storage`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List storage providers |
| GET | `/{provider_id}` | Get provider |
| POST | `/{provider_id}` | Create/update provider |
| DELETE | `/{provider_id}` | Delete provider |

#### NEW: `/api/v1/storage-settings`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Get storage settings |
| POST | `/` | Create storage settings |
| PATCH | `/` | Update storage settings |

#### 🔧 CHANGES NEEDED:
- Change prefix from `/storage-settings` to `/storage`

---

### 9. SSO Settings (`/ssos` → `/admin/sso-settings`)

#### OLD: `/api/v1/ssos`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List SSO settings |
| POST | `/` | Create SSO setting |
| GET | `/available-providers` | List available providers |
| GET | `/{sso_id}` | Get SSO setting |
| PATCH | `/{sso_id}` | Update SSO setting |
| DELETE | `/{sso_id}` | Delete SSO setting |

#### NEW: `/api/v1/admin/sso-settings`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List SSO settings |
| POST | `/` | Create SSO setting |
| GET | `/{provider}` | Get SSO setting |
| PATCH | `/{provider}` | Update SSO setting |
| DELETE | `/{provider}` | Delete SSO setting |

#### 🔧 CHANGES NEEDED:
- Change prefix from `/admin/sso-settings` to `/ssos`
- Add `/available-providers` endpoint
- Change `/{provider}` to `/{sso_id}`

---

### 10. Security/Passkey (`/security` → `/auth/passkey`)

#### OLD: `/api/v1/security`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/passkeys` | List passkeys |
| DELETE | `/passkeys/{id}` | Delete passkey |

#### NEW: `/api/v1/auth/passkey`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/register` | Start passkey registration |
| POST | `/register/verify` | Verify passkey registration |
| GET | `/credentials` | List passkeys |
| DELETE | `/credentials/{id}` | Delete passkey |
| POST | `/login` | Start passkey login |
| POST | `/login/verify` | Verify passkey login |

#### 🔧 CHANGES NEEDED:
- Add `/security` router with:
  - GET `/passkeys` → calls passkey credentials
  - DELETE `/passkeys/{id}` → calls passkey delete

---

### 11. Features (`/features` → `/feature-flags`)

#### OLD: `/api/v1/features`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List available features |

#### NEW: `/api/v1/feature-flags`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List feature flags |
| GET | `/{flag_name}` | Get feature flag |
| POST | `/` | Create feature flag |
| PATCH | `/{flag_name}` | Update feature flag |
| DELETE | `/{flag_name}` | Delete feature flag |

#### 🔧 CHANGES NEEDED:
- Change prefix from `/feature-flags` to `/features`
- OR add alias route at `/features`

---

## Missing Endpoints in New Backend

| Old Endpoint | Description | Action Needed |
|-------------|-------------|---------------|
| `GET /api/v1/app_configuration` | App configuration for frontend | Create new router |
| `GET /api/v1/dashboard` | Dashboard metrics with time filters | Create new router |
| `GET /api/v1/permissions` | List all permissions | Add to RBAC router |

---

## Priority Action Items

### 🔴 HIGH PRIORITY (Critical for frontend)

1. **Rename `/auth` prefix to `/account`**
   - File: `src/app/modules/auth/router.py`
   - Change: `prefix="/auth"` → `prefix="/account"`

2. **Create `/products` router (separate from billing)**
   - Create alias routes or new router at `/products`

3. **Create `/prices` router (separate from billing)**
   - Create new router at `/prices` with price CRUD

4. **Create `/checkouts` router (separate from billing)**
   - Create alias routes or new router at `/checkouts`

5. **Change `/admin/sso-settings` to `/ssos`**
   - File: `src/app/modules/sso_settings/router.py`
   - Change: `prefix="/admin/sso-settings"` → `prefix="/ssos"`

### 🟡 MEDIUM PRIORITY (Important for compatibility)

6. **Unify user endpoints under `/users` prefix**
   - File: `src/app/modules/users/router.py`
   - Add consistent `/users` prefix

7. **Create `/invoices` router**
   - Create alias routes or new router at `/invoices`

8. **Create `/configurations/stripe` router**
   - Create alias routes for Stripe configuration

9. **Change `/storage-settings` to `/storage`**
   - File: `src/app/modules/storage_settings/router.py`
   - Change: `prefix="/storage-settings"` → `prefix="/storage"`

10. **Create `/security` router for passkeys**
    - Create wrapper routes at `/security/passkeys`

### 🟢 LOW PRIORITY (Nice to have)

11. **Create `/app_configuration` endpoint**
    - New module for frontend configuration

12. **Create `/dashboard` endpoint**
    - New module for dashboard metrics

13. **Add `/permissions` endpoint to RBAC**
    - Add `GET /permissions` to list all permissions

---

## Implementation Strategy

### Option A: Rename Existing Routers (Breaking change for new API consumers)
- Directly rename router prefixes to match old backend
- Simpler but may break any new frontend work

### Option B: Add Alias Routes (Non-breaking)
- Keep existing routes intact
- Add new routers that alias to existing endpoints
- More code but preserves both API structures

### Option C: Hybrid Approach (Recommended)
- Rename routers where no new frontend work depends on them
- Add aliases where needed for backward compatibility
- Document deprecated routes for future cleanup

---

## Files to Modify

| File | Current Prefix | Target Prefix |
|------|---------------|---------------|
| `src/app/modules/auth/router.py` | `/auth` | `/account` |
| `src/app/modules/sso/router.py` | `/auth/sso` | `/account/sso` |
| `src/app/modules/passkey/router.py` | `/auth/passkey` | `/account/passkey` + `/security` |
| `src/app/modules/magic_link/router.py` | `/auth/magic-link` | `/account/magic-link` |
| `src/app/modules/users/router.py` | (none) | `/users` |
| `src/app/modules/sso_settings/router.py` | `/admin/sso-settings` | `/ssos` |
| `src/app/modules/storage_settings/router.py` | `/storage-settings` | `/storage` |
| `src/app/modules/feature_flags/router.py` | `/feature-flags` | `/features` |

---

## New Routers to Create

1. **`src/app/modules/products/router.py`** - Standalone products endpoint
2. **`src/app/modules/prices/router.py`** - Standalone prices endpoint
3. **`src/app/modules/checkouts/router.py`** - Standalone checkout endpoint
4. **`src/app/modules/invoices/router.py`** - Standalone invoices endpoint
5. **`src/app/modules/configurations/router.py`** - Stripe configuration endpoint
6. **`src/app/modules/security/router.py`** - Security/passkey wrapper
7. **`src/app/modules/app_configuration/router.py`** - App configuration endpoint
8. **`src/app/modules/dashboard/router.py`** - Dashboard metrics endpoint
