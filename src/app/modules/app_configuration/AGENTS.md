# App Configuration Module

**Location:** `src/app/modules/app_configuration/`  
**Purpose:** Provides frontend application configuration data including theme settings, feature flags, and API details.

## Overview

The app_configuration module exposes a public endpoint that returns configuration data needed by the frontend to initialize properly. This includes theme settings, enabled features, API version information, and system status.

## Endpoints

### `GET /api/v1/app_configuration`

Get frontend application configuration.

**Authentication:** Not required (public endpoint)  
**Tenant Header:** Not required (exempt from tenant middleware)  
**Response:** `AppConfigurationResponse`

```bash
# Example request
curl -X GET http://localhost:8000/api/v1/app_configuration
```

**Example Response:**

```json
{
  "app_name": "FastAPI SaaS",
  "app_version": "1.0.0",
  "environment": "production",
  "theme": {
    "primary_color": "#3B82F6",
    "secondary_color": "#10B981",
    "dark_mode_enabled": true,
    "default_theme": "light"
  },
  "features": {
    "sso_enabled": true,
    "passkey_enabled": true,
    "magic_link_enabled": true,
    "billing_enabled": true,
    "ai_enabled": true,
    "webhooks_enabled": true,
    "storage_enabled": true,
    "audit_logs_enabled": true,
    "multi_tenancy_enabled": true
  },
  "api": {
    "version": "1.0.0",
    "base_url": "/api/v1",
    "websocket_url": null
  },
  "maintenance_mode": false
}
```

## Architecture

### Module Structure

```
src/app/modules/app_configuration/
├── __init__.py         # Module exports
├── router.py           # API routes
├── schemas.py          # Pydantic models
└── service.py          # Business logic
```

### Schemas

#### `ThemeConfig`

Frontend theme configuration:
- `primary_color` - Primary brand color (hex)
- `secondary_color` - Secondary brand color (hex)
- `dark_mode_enabled` - Whether dark mode is available
- `default_theme` - Default theme ("light" or "dark")

#### `FeaturesConfig`

Feature flags indicating which modules are enabled:
- `sso_enabled` - SSO authentication
- `passkey_enabled` - WebAuthn passkeys
- `magic_link_enabled` - Passwordless magic links
- `billing_enabled` - Stripe billing integration
- `ai_enabled` - AI features
- `webhooks_enabled` - Webhook functionality
- `storage_enabled` - File storage
- `audit_logs_enabled` - Audit logging
- `multi_tenancy_enabled` - Multi-tenancy support

#### `APIConfig`

API configuration details:
- `version` - API version (e.g., "1.0.0")
- `base_url` - API base URL (e.g., "/api/v1")
- `websocket_url` - WebSocket URL (if enabled)

#### `AppConfigurationResponse`

Complete application configuration response combining all configs above plus:
- `app_name` - Application name
- `app_version` - Application version
- `environment` - Environment (development/staging/production)
- `maintenance_mode` - Whether system is in maintenance mode

## Implementation Notes

### Why This Module Exists

1. **Frontend Initialization:** Provides configuration data needed before user authentication
2. **Feature Discovery:** Frontend can conditionally render UI based on enabled features
3. **Theme Configuration:** Centralizes theme settings for consistent branding
4. **Environment Awareness:** Frontend knows which environment it's running in

### Public Endpoint

This endpoint is **exempt from authentication and tenant requirements** because:
- Frontend needs configuration before user logs in
- No sensitive data is exposed (only public configuration)
- Added to `TENANT_EXEMPT_PATHS` in tenant middleware

### Configuration Sources

Configuration is pulled from multiple sources:

```python
# From settings (environment variables)
app_name = settings.APP_NAME
environment = settings.ENVIRONMENT

# From module detection (hardcoded based on installed modules)
features.sso_enabled = True  # SSO module exists
features.billing_enabled = True  # Billing module exists
```

## Service Layer

### `AppConfigurationService`

**Method:** `get_configuration() -> AppConfigurationResponse`

Builds the complete configuration response by:
1. Reading settings from environment variables
2. Detecting which feature modules are installed
3. Building theme configuration
4. Constructing API metadata
5. Returning combined configuration

```python
from src.app.modules.app_configuration.service import app_configuration_service

# Get configuration
config = await app_configuration_service.get_configuration()
```

## Testing

### Unit Tests

```bash
# Run app configuration tests
uv run pytest tests/unit/modules/app_configuration/test_app_configuration.py -v
```

**Test Coverage:**
- ✅ Public endpoint accessibility (no auth required)
- ✅ Response structure validation
- ✅ Theme configuration format
- ✅ Feature flags presence and types
- ✅ API configuration structure
- ✅ OpenAPI documentation
- ✅ Service layer logic

### Integration Tests

App configuration is included in the frontend compatibility test suite:

```bash
uv run pytest tests/integration/test_frontend_compatibility.py::TestFrontendCompatibility::test_app_configuration_endpoint_exists -v
```

## Common Workflows

### Frontend Initialization

```typescript
// Frontend code
interface AppConfig {
  app_name: string;
  app_version: string;
  environment: string;
  theme: {
    primary_color: string;
    secondary_color: string;
    dark_mode_enabled: boolean;
    default_theme: string;
  };
  features: {
    sso_enabled: boolean;
    passkey_enabled: boolean;
    // ... other features
  };
  api: {
    version: string;
    base_url: string;
    websocket_url: string | null;
  };
  maintenance_mode: boolean;
}

async function initializeApp() {
  // Fetch configuration (no auth required)
  const config: AppConfig = await fetch('/api/v1/app_configuration')
    .then(res => res.json());
  
  // Apply theme
  document.documentElement.style.setProperty(
    '--color-primary',
    config.theme.primary_color
  );
  
  // Conditionally render features
  if (config.features.sso_enabled) {
    showSSOButtons();
  }
  
  // Check maintenance mode
  if (config.maintenance_mode) {
    showMaintenancePage();
    return;
  }
  
  // Continue app initialization...
}
```

### Feature Detection

```typescript
// Conditionally show features based on backend capabilities
function App() {
  const { data: config } = useAppConfiguration();
  
  return (
    <div>
      {config.features.billing_enabled && <BillingSection />}
      {config.features.ai_enabled && <AIAssistant />}
      {config.features.webhooks_enabled && <WebhookSettings />}
    </div>
  );
}
```

### Environment-Specific Behavior

```typescript
// Enable debug features in development
if (config.environment === 'development') {
  enableDebugMode();
  showDevTools();
}

// Show environment badge
if (config.environment !== 'production') {
  showEnvironmentBadge(config.environment);
}
```

## Environment Variables

Configuration can be customized via environment variables:

```bash
# Application
APP_NAME="My SaaS App"
APP_VERSION="2.0.0"
ENVIRONMENT=production

# Theme
THEME_PRIMARY_COLOR="#3B82F6"
THEME_SECONDARY_COLOR="#10B981"
THEME_DARK_MODE_ENABLED=true
THEME_DEFAULT=light

# Maintenance
MAINTENANCE_MODE=false
```

## Maintenance Notes

### Adding New Configuration Fields

1. Add field to appropriate schema in `schemas.py`:
   ```python
   class FeaturesConfig(BaseModel):
       new_feature_enabled: bool = Field(default=True)
   ```

2. Update service in `service.py`:
   ```python
   features = FeaturesConfig(
       new_feature_enabled=True,
       # ... other features
   )
   ```

3. Add tests in `test_app_configuration.py`

### Feature Flag Management

Current feature flags are **hardcoded** to `True` (all modules are installed). For dynamic feature flags:

1. Create a feature flags table in the database
2. Update `AppConfigurationService` to query the database
3. Cache results in Redis for performance

Example:
```python
# Dynamic feature flags (future enhancement)
features = await feature_flag_service.get_enabled_features()
```

### Caching Considerations

The endpoint is currently **not cached** because:
- Configuration changes are rare
- Response is small (~1KB)
- No database queries required

If performance becomes an issue, add Redis caching:

```python
@router.get("", response_model=AppConfigurationResponse)
@cache(ttl=300)  # Cache for 5 minutes
async def get_app_configuration():
    ...
```

## Related Modules

- **Health Module:** `src/app/modules/health/` - System health checks
- **Feature Flags Module:** `src/app/modules/feature_flags/` - Database-backed feature flags
- **Core Config:** `src/app/core/config.py` - Environment variable settings

## API Versioning

- **Current Version:** v1
- **Prefix:** `/api/v1/app_configuration`
- **Stability:** Stable (public contract)
- **Breaking Changes:** Require major version bump

## Security Considerations

### What's Safe to Expose

✅ **Safe:**
- Application name and version
- Environment type (development/staging/production)
- Theme colors
- Feature availability flags
- API version

❌ **Not Safe:**
- Database connection strings
- API keys or secrets
- Internal service URLs
- User data
- Rate limit values

### Maintenance Mode

The `maintenance_mode` flag can be used to:
- Display maintenance page to users
- Prevent new signups
- Block non-essential API calls
- Show status message

```python
# In service
maintenance_mode = getattr(settings, "MAINTENANCE_MODE", False)
```

Set via environment variable:
```bash
MAINTENANCE_MODE=true
```
