# SSO Module AGENTS.md

## Package Identity
OAuth 2.0 / OIDC provider integrations (Google, GitHub, Microsoft, Apple). Factory pattern for multiple providers with redirect URL generation and token exchange.

## Setup & Run
```bash
# Run tests
uv run pytest tests/unit/test_sso/test_providers.py -v

# Type check
uv run mypy src/app/modules/sso/

# Lint
uv run ruff check src/app/modules/sso/
```

## Patterns & Conventions

### Code Organization
- ⚠️ NOTE: SSOService is NOT yet migrated to BaseService[T]
- Factory pattern for provider instances
- Provider-specific implementations in `providers/` subdirectory
- Settings managed via `sso_settings` module (encrypted)

### Provider Factory Pattern
```python
# Pattern from src/app/modules/sso/service.py:

def get_provider(provider_name: str, settings: SSOSettingsRead) -> BaseSSOProvider:
    """Get configured SSO provider instance."""
    providers = {
        "google": GoogleProvider,
        "github": GitHubProvider,
        "microsoft": MicrosoftProvider,
        "apple": AppleProvider,
    }
    provider_class = providers.get(provider_name)
    if not provider_class:
        raise ValueError(f"Unknown provider: {provider_name}")
    return provider_class(settings)
```

### SSO Flow
```python
# Authentication Flow:

1. User selects provider (google, github, etc.)
2. Generate authorization URL with state parameter
3. Redirect user to provider's auth page
4. Provider redirects back with code + state
5. Exchange code for access token
6. Fetch user profile (email, name, avatar)
7. Create or link user account
8. Create session and redirect to app
```

### Provider Implementations
- Base class: `BaseSSOProvider` in `providers/base.py`
- Each provider implements:
  - `get_authorization_url(redirect_uri, state) -> str`
  - `exchange_code_for_token(code) -> dict`
  - `get_user_profile(access_token) -> UserProfile`

### Settings Integration
- Fetches settings from `sso_settings` module
- Settings encrypted at rest (client_id, client_secret)
- Per-tenant configuration

## Touch Points / Key Files

- Service: `src/app/modules/sso/service.py` - Factory, orchestrator
- Providers: `src/app/modules/sso/providers/` - Provider implementations
  - `base.py` - BaseSSOProvider abstract class
  - `google.py` - Google OAuth 2.0
  - `github.py` - GitHub OAuth
  - `microsoft.py` - Microsoft Azure AD
  - `apple.py` - Apple Sign In
- Router: `src/app/modules/sso/router.py` - Auth/redirect endpoints
- Schemas: `src/app/modules/sso/schemas.py` - SSO request/response models

## JIT Index Hints

- Find provider implementations: `rg -n "class.*Provider" src/app/modules/sso/providers/`
- Find factory method: `rg -n "get_provider" src/app/modules/sso/`
- Find auth flows: `rg -n "(authorization_url|callback)" src/app/modules/sso/`
- Find tests: `find tests -name "*sso*"`

## Common Gotchas

- SSOService does NOT inherit from BaseService (pending migration)
- Provider settings are encrypted in sso_settings module
- State parameter prevents CSRF attacks
- Always validate state parameter on callback
- Redirect URI must match provider configuration exactly
- Handle provider errors gracefully (e.g., user denied access)

## Pre-PR Checks

```bash
uv run mypy src/app/modules/sso/ && uv run ruff check src/app/modules/sso/ && uv run pytest tests/unit/test_sso/test_providers.py -v
```
