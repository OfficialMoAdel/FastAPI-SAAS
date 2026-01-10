# User Preferences Module AGENTS.md

## Package Identity
Manages user preferences including theme, language, notifications, and profile image. Supports default preferences and image upload integration.

## Setup & Run
```bash
# Run tests
uv run pytest tests/unit/test_user_preferences/test_service.py -v

# Type check
uv run mypy src/app/modules/user_preferences/

# Lint
uv run ruff check src/app/modules/user_preferences/
```

## Patterns & Conventions

### Code Organization
- ⚠️ NOTE: PreferencesService is NOT yet migrated to BaseService[T]
- Uses singleton pattern for service instance
- Default preferences for new users
- Profile image integration with `files` module

### Preferences Pattern
```python
# Pattern from src/app/modules/user_preferences/service.py:

class PreferencesService:
    """Manage user preferences."""

    async def get_preferences(
        self, db: AsyncSession, user_id: str
    ) -> UserPreferencesRead:
        """Get user preferences with defaults."""
        user_prefs = await crud.get_by_user(db, user_id)
        if not user_prefs:
            return DEFAULT_PREFERENCES
        return UserPreferencesRead.model_validate(user_prefs)

    async def update_preferences(
        self,
        db: AsyncSession,
        user_id: str,
        preferences_in: UserPreferencesUpdate,
    ) -> UserPreferencesRead:
        """Update user preferences."""
        return await crud.update_by_user(db, user_id, preferences_in)
```

### Default Preferences
- Theme: system (uses OS preference)
- Language: en (English)
- Notifications: email: true, push: false
- Timezone: UTC
- Profile image: null (can upload)

### Profile Image Upload
- Images stored via `files` module
- Stored at: `{tenant_id}/{user_id}/profile/{filename}`
- Supports: PNG, JPG, WebP (max 5MB)
- URL stored in preferences

## Touch Points / Key Files

- Service: `src/app/modules/user_preferences/service.py` - Preferences management
- CRUD: `src/app/modules/user_preferences/crud.py` - Database operations
- Schemas: `src/app/modules/user_preferences/schemas.py` - Request/response models
- Router: `src/app/modules/user_preferences/router.py` - API endpoints
- Model: `src/app/modules/user_preferences/models.py` - UserPreferences database model

## JIT Index Hints

- Find preference defaults: `rg -n "DEFAULT" src/app/modules/user_preferences/`
- Find service methods: `rg -n "async def (get|update|delete)" src/app/modules/user_preferences/service.py`
- Find image upload: `rg -n "profile.*image" src/app/modules/user_preferences/`
- Find tests: `find tests -name "*user_preferences*"`

## Common Gotchas

- PreferencesService does NOT inherit from BaseService (pending migration)
- New users get default preferences automatically
- Profile image upload requires files module integration
- Image upload is optional - can keep null
- Preferences are user-scoped (not tenant-scoped)
- Test isolation issue: Default preferences may leak between tests

## Pre-PR Checks

```bash
uv run mypy src/app/modules/user_preferences/ && uv run ruff check src/app/modules/user_preferences/ && uv run pytest tests/unit/test_user_preferences/test_service.py -v
```
