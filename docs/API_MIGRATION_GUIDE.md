# API Migration Guide

## Overview

This guide documents the API changes made for frontend compatibility. All changes maintain backward compatibility where possible, with clear migration paths for breaking changes.

**Version**: 1.0  
**Date**: 2026-01-24  
**Status**: ✅ Production Ready

---

## Summary of Changes

### Route Renaming (11 endpoints)
- Authentication routes moved from `/auth` to `/account`
- SSO settings moved from `/admin/sso-settings` to `/ssos`
- Storage settings simplified from `/storage-settings` to `/storage`
- Feature flags simplified from `/feature-flags` to `/features`
- User endpoints unified under `/users` prefix

### New Compatibility Endpoints (7 routers)
- `/products` - Product management (separate from billing)
- `/prices` - Pricing management (separate from billing)
- `/checkouts` - Subscription checkout and management
- `/invoices` - Billing records access
- `/configurations/stripe` - Stripe configuration and webhooks
- `/account/register` - New user registration
- `/ssos/available-providers` - SSO provider discovery

---

## Route Changes

### 1. Authentication → Account

**Old Routes**:
```
POST   /api/v1/auth/login
POST   /api/v1/auth/refresh
POST   /api/v1/auth/logout
POST   /api/v1/auth/password
POST   /api/v1/auth/forgot-password
POST   /api/v1/auth/reset-password
POST   /api/v1/auth/send-verification
POST   /api/v1/auth/verify-email
```

**New Routes**:
```
POST   /api/v1/account/login
POST   /api/v1/account/register        # NEW!
POST   /api/v1/account/refresh
POST   /api/v1/account/logout
POST   /api/v1/account/password
POST   /api/v1/account/forgot-password
POST   /api/v1/account/reset-password
POST   /api/v1/account/send-verification
POST   /api/v1/account/verify-email
```

**Status**: ✅ Renamed  
**Breaking**: Yes  
**Migration**: Update all `/auth` references to `/account`

---

### 2. SSO Routes

**Old Routes**:
```
GET    /api/v1/auth/sso/{provider}/authorize
GET    /api/v1/auth/sso/{provider}/callback
POST   /api/v1/auth/sso/{provider}/connect
DELETE /api/v1/auth/sso/{provider}
```

**New Routes**:
```
GET    /api/v1/account/sso/{provider}/authorize
GET    /api/v1/account/sso/{provider}            # Backward compat alias!
GET    /api/v1/account/sso/{provider}/callback
POST   /api/v1/account/sso/{provider}/connect
DELETE /api/v1/account/sso/{provider}
```

**Status**: ✅ Moved + Backward Compatibility Alias  
**Breaking**: Yes, but `GET /{provider}` alias maintains compatibility  
**Migration**: Update `/auth/sso` to `/account/sso`

---

### 3. SSO Admin Settings

**Old Routes**:
```
GET    /api/v1/admin/sso-settings
POST   /api/v1/admin/sso-settings
GET    /api/v1/admin/sso-settings/{provider}
PATCH  /api/v1/admin/sso-settings/{provider}
DELETE /api/v1/admin/sso-settings/{provider}
```

**New Routes**:
```
GET    /api/v1/ssos
GET    /api/v1/ssos/available-providers          # NEW!
POST   /api/v1/ssos
GET    /api/v1/ssos/{provider}
PATCH  /api/v1/ssos/{provider}
DELETE /api/v1/ssos/{provider}
```

**Status**: ✅ Renamed  
**Breaking**: Yes  
**Migration**: Update `/admin/sso-settings` to `/ssos`  
**New Feature**: `/available-providers` endpoint lists supported SSO providers

---

### 4. User Management (Unified)

**Old Routes**:
```
POST   /api/v1/user
GET    /api/v1/users
GET    /api/v1/user/me/
GET    /api/v1/user/{username}
PATCH  /api/v1/user/{username}
DELETE /api/v1/user/{username}
DELETE /api/v1/db_user/{username}
GET    /api/v1/user/{username}/rate_limits
GET    /api/v1/user/{username}/tier
PATCH  /api/v1/user/{username}/tier
```

**New Routes** (All under `/users`):
```
POST   /api/v1/users
GET    /api/v1/users
GET    /api/v1/users/me                          # No trailing slash!
GET    /api/v1/users/{user_id}                   # Supports ID or username!
PATCH  /api/v1/users/{user_id}
PUT    /api/v1/users/{user_id}                   # NEW! Alias for PATCH
DELETE /api/v1/users/{user_id}
DELETE /api/v1/users/db/{user_id}                # Changed path
GET    /api/v1/users/{user_id}/rate-limits       # Hyphenated!
GET    /api/v1/users/{user_id}/tier
PATCH  /api/v1/users/{user_id}/tier
```

**Status**: ✅ Unified under `/users` prefix  
**Breaking**: Yes  
**Migration**: 
- Update `/user` and `/users` to `/users`
- Remove trailing slash from `/me/`
- Update `{username}` to `{user_id}` (accepts both!)
- Update `rate_limits` to `rate-limits`
- Update `/db_user/` to `/users/db/`

**Smart ID Resolution**: All `{user_id}` parameters accept both:
- Integer user IDs: `/users/123`
- Usernames: `/users/johndoe`

---

### 5. Storage Settings

**Old Routes**:
```
GET    /api/v1/storage-settings
POST   /api/v1/storage-settings
PATCH  /api/v1/storage-settings
```

**New Routes**:
```
GET    /api/v1/storage
POST   /api/v1/storage
PATCH  /api/v1/storage
```

**Status**: ✅ Renamed  
**Breaking**: Yes  
**Migration**: Update `/storage-settings` to `/storage`

---

### 6. Feature Flags

**Old Routes**:
```
GET    /api/v1/feature-flags
GET    /api/v1/feature-flags/{flag_name}
PATCH  /api/v1/feature-flags/{flag_name}
DELETE /api/v1/feature-flags/{flag_name}
PUT    /api/v1/feature-flags/bulk
```

**New Routes**:
```
GET    /api/v1/features
GET    /api/v1/features/{flag_name}
PATCH  /api/v1/features/{flag_name}
DELETE /api/v1/features/{flag_name}
PUT    /api/v1/features/bulk
```

**Status**: ✅ Renamed  
**Breaking**: Yes  
**Migration**: Update `/feature-flags` to `/features`

---

## New Endpoints

### 1. Products (Billing)

**New Routes**:
```
GET    /api/v1/products                          # List products
POST   /api/v1/products                          # Create product (admin)
GET    /api/v1/products/{product_id}             # Get product (supports Stripe IDs!)
PATCH  /api/v1/products/{product_id}             # Update product (admin)
DELETE /api/v1/products/{product_id}             # Delete product (admin)
POST   /api/v1/products/{product_id}/pricing     # Add pricing tier (admin)
```

**Purpose**: Separate product management from billing module  
**Permission**: Admin-only for mutations  
**Special Feature**: `{product_id}` supports both integer IDs and Stripe product IDs (e.g., `prod_xxx`)

---

### 2. Prices (Billing)

**New Routes**:
```
GET    /api/v1/prices?product_id={id}&is_active={bool}  # List prices with filters
POST   /api/v1/prices?product_id={id}                   # Create price (admin)
GET    /api/v1/prices/{price_id}                         # Get price
PATCH  /api/v1/prices/{price_id}                         # Update price (admin)
DELETE /api/v1/prices/{price_id}                         # Soft delete (admin)
```

**Purpose**: Separate pricing management from billing module  
**Permission**: Admin-only for mutations  
**Filters**: `product_id`, `is_active`

---

### 3. Checkouts (Subscription Management)

**New Routes**:
```
POST   /api/v1/checkouts                         # Create Stripe checkout session
GET    /api/v1/checkouts/current                 # Get current subscription
POST   /api/v1/checkouts/cancel                  # Cancel subscription
```

**Purpose**: Subscription management workflow  
**Permission**: Authenticated users only  
**Features**:
- Create checkout session with Stripe redirection
- Retrieve current active subscription
- Cancel subscription at period end
- Ownership verification on cancellation

---

### 4. Invoices (Billing Records)

**New Routes**:
```
GET    /api/v1/invoices?status={status}          # List user's invoices
GET    /api/v1/invoices/{invoice_id}             # Get single invoice
```

**Purpose**: Billing record access for users  
**Permission**: Authenticated users (own records only)  
**Filters**: `status` (succeeded, failed, pending, refunded)

---

### 5. Stripe Configuration

**New Routes**:
```
GET    /api/v1/configurations/stripe             # Get Stripe settings (admin)
POST   /api/v1/configurations/stripe             # Create/update settings (admin)
POST   /api/v1/configurations/stripe/webhooks    # Stripe webhook handler (public)
```

**Purpose**: Stripe configuration and webhook handling  
**Permission**: Admin-only except webhooks  
**Features**:
- Secret masking (returns `has_secret_key`, `has_webhook_secret` flags)
- Webhook signature verification
- 24-hour idempotency check
- Background event processing

---

## Backward Compatibility Features

### 1. Smart ID Resolution
All `{user_id}` parameters accept both integer IDs and username strings:
```
GET /api/v1/users/123       # Integer ID
GET /api/v1/users/johndoe   # Username
```

### 2. Stripe ID Support
Product endpoints support both integer and Stripe product IDs:
```
GET /api/v1/products/456        # Integer ID
GET /api/v1/products/prod_xxx   # Stripe product ID
```

### 3. SSO Backward Compatibility Alias
Old frontend can still use short SSO path:
```
GET /api/v1/account/sso/google              # Works! (alias)
GET /api/v1/account/sso/google/authorize    # Canonical
```

### 4. HTTP Method Aliases
PUT aliases for PATCH endpoints:
```
PATCH /api/v1/users/{user_id}   # Partial update
PUT   /api/v1/users/{user_id}   # Full update (same as PATCH)
```

---

## Breaking Changes Summary

### Required Frontend Updates

1. **Authentication**: `/auth/*` → `/account/*`
2. **SSO Admin**: `/admin/sso-settings` → `/ssos`
3. **User Endpoints**: Consolidate under `/users` prefix
4. **Storage Settings**: `/storage-settings` → `/storage`
5. **Feature Flags**: `/feature-flags` → `/features`

### URL Structure Changes

1. **Trailing Slashes**: `/user/me/` → `/users/me` (removed)
2. **Hyphens**: `/rate_limits` → `/rate-limits`
3. **Path Structure**: `/db_user/{username}` → `/users/db/{user_id}`
4. **Parameters**: `{username}` → `{user_id}` (but accepts both!)

---

## Migration Checklist

### Frontend Developer Checklist

- [ ] Update all `/auth/` URLs to `/account/`
- [ ] Update `/admin/sso-settings` to `/ssos`
- [ ] Update `/storage-settings` to `/storage`
- [ ] Update `/feature-flags` to `/features`
- [ ] Consolidate user endpoints under `/users`
- [ ] Remove trailing slash from `/user/me/`
- [ ] Update `rate_limits` to `rate-limits`
- [ ] Update `/db_user/` to `/users/db/`
- [ ] Test Stripe checkout flow with new `/checkouts` endpoints
- [ ] Test invoice listing with new `/invoices` endpoints

### Testing Checklist

- [ ] Test authentication flow with `/account` endpoints
- [ ] Test SSO login with new `/account/sso` paths
- [ ] Test backward compatibility alias for SSO
- [ ] Test user management with unified `/users` endpoints
- [ ] Test smart ID resolution (int IDs and usernames)
- [ ] Test product/price management
- [ ] Test subscription checkout and cancellation
- [ ] Test invoice listing and filtering
- [ ] Test Stripe configuration and webhooks
- [ ] Test storage settings CRUD
- [ ] Test feature flags listing

---

## OpenAPI/Swagger Changes

All endpoints are automatically documented via FastAPI's OpenAPI integration. Access the interactive documentation at:

- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI JSON**: `http://localhost:8000/openapi.json`

### Updated Tags

- `account` (renamed from `auth`)
- `ssos` (renamed from `admin`, `sso-settings`)
- `users` (consolidated)
- `products` (new)
- `prices` (new)
- `checkouts` (new)
- `invoices` (new)
- `configurations` (new)
- `storage` (renamed from `Storage Settings`)
- `features` (renamed from `Feature Flags`)

---

## Example Code Updates

### Before (Old Backend)
```javascript
// Authentication
await fetch('/api/v1/auth/login', { method: 'POST', ... });

// Get current user
await fetch('/api/v1/user/me/', { method: 'GET', ... });

// Update user
await fetch('/api/v1/user/johndoe', { method: 'PATCH', ... });

// SSO settings
await fetch('/api/v1/admin/sso-settings', { method: 'GET', ... });

// Storage settings
await fetch('/api/v1/storage-settings', { method: 'GET', ... });

// Feature flags
await fetch('/api/v1/feature-flags', { method: 'GET', ... });
```

### After (New Backend)
```javascript
// Authentication
await fetch('/api/v1/account/login', { method: 'POST', ... });

// Get current user
await fetch('/api/v1/users/me', { method: 'GET', ... });

// Update user (accepts ID or username!)
await fetch('/api/v1/users/123', { method: 'PATCH', ... });
await fetch('/api/v1/users/johndoe', { method: 'PATCH', ... });

// SSO settings
await fetch('/api/v1/ssos', { method: 'GET', ... });

// Storage settings
await fetch('/api/v1/storage', { method: 'GET', ... });

// Feature flags
await fetch('/api/v1/features', { method: 'GET', ... });

// NEW: Subscription checkout
await fetch('/api/v1/checkouts', { method: 'POST', ... });

// NEW: List invoices
await fetch('/api/v1/invoices', { method: 'GET', ... });

// NEW: List products
await fetch('/api/v1/products', { method: 'GET', ... });
```

---

## Support & Questions

For questions about API migration:
1. Check this guide first
2. Review the schema compatibility report: `docs/SCHEMA_COMPATIBILITY.md`
3. Review test coverage summary: `TEST_COVERAGE_SUMMARY.md`
4. Check OpenAPI docs at `/docs`
5. Run E2E tests: `python -m pytest tests/e2e/test_api_compat_e2e.py -v -s`

---

**Last Updated**: 2026-01-24  
**Version**: 1.0  
**Status**: ✅ Complete
