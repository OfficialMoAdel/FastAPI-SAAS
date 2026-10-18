# User Preferences Module AGENTS.md

## Package Identity
Manages user preferences including theme, language, notifications, and profile image. Supports default preferences and image upload integration.

## Setup & Run
```bash
# Run tests
uv run pytest tests/unit/modules/user_preferences/test_service.py -v

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
- Notifications: email: true, push: true, disabled_types: [], quiet_hours: false
- Timezone: UTC
- Profile image: null (can upload)

### Notification Preferences
- `email_notifications_enabled`: Toggle email notifications (default: true)
- `push_notifications_enabled`: Toggle push notifications (default: true)
- `disabled_notification_types`: List of notification types to skip (default: [])
- `quiet_hours_enabled`: Enable quiet hours (default: false)
- `quiet_hours_start`: Start time in HH:MM format (e.g., "22:00")
- `quiet_hours_end`: End time in HH:MM format (e.g., "07:00")
- Quiet hours support same-day (22:00-23:59) and cross-day (22:00-07:00) ranges
- Integration: Notifications module calls `should_send_notification()` before sending

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
- Migration: `src/migrations/versions/2d75ef82a66a_add_notification_preferences_to_user_.py` - Notification preferences columns
- Tests: `tests/integration/modules/notifications/test_notification_preferences.py` - Notification preferences tests

## JIT Index Hints

- Find preference defaults: `rg -n "DEFAULT" src/app/modules/user_preferences/`
- Find service methods: `rg -n "async def (get|update|delete)" src/app/modules/user_preferences/service.py`
- Find notification preferences: `rg -n "(email_notifications|quiet_hours|disabled_notification)" src/app/modules/user_preferences/`
- Find image upload: `rg -n "profile.*image" src/app/modules/user_preferences/`
- Find tests: `find tests -name "*user_preferences*"`
- Find integration with notifications: `rg -n "should_send_notification" src/app/modules/notifications/`

## Common Gotchas

- PreferencesService does NOT inherit from BaseService (pending migration)
- New users get default preferences automatically
- Profile image upload requires files module integration
- Image upload is optional - can keep null
- Preferences are user-scoped (not tenant-scoped)
- Test isolation issue: Default preferences may leak between tests
- Notification preferences have init=False - set via database updates, not constructor
- Quiet hours validation prevents start == end (must be different times)
- Notification module calls `should_send_notification()` before sending emails
- **`quiet_hours_start` / `quiet_hours_end` are `String(5)` columns and that is load-bearing** (`models.py:90,95`). Three call sites depend on it: `crud.py:113` writes them with a `setattr` of `"22:00"`-shaped strings, the consumer at `notifications/service.py:611-612` calls `time.fromisoformat(prefs.quiet_hours_start)`, and the schemas type them `str | None`. On a database built by `alembic` those columns are `sa.Time(timezone=True)` (`migrations/versions/05_content_storage.py:103-104`), and MEASURED (`perf-results/RESULT-20261018-9cmn/`, bead `FastAPI_saas-9cmn`): the INSERT is refused with a `DatatypeMismatchError` on the bind type even when both columns are bound NULL, so `get_or_create` cannot create the row at all; a row that does exist reads back as `datetime.time`, and `time.fromisoformat` then raises a **`TypeError`** — which the `except ValueError` at `service.py:631` does NOT catch, so the exception escapes `should_send_notification`. `UserPreferencesRead` does not catch it either, because Pydantic coerces `time` into the `str` it declares, so the API happily serialises a value the notification path cannot parse. The same probe against a `create_all` database accepted every write and read, which is why development never sees it. **Fixed in `FastAPI_saas-nqk6`**, on the decision that the MODEL stays and the MIGRATION moves (a quiet-hours window is "22:00 to 07:00" — not an instant, no date, cross-day is the ordinary case, so the string is the right domain shape). The fix is in the layer that creates the table: `migrations/versions/05_content_storage.py` now declares `sa.String(length=5)`. Because the schema is not shipped yet, the database is dropped and rebuilt (`DROP SCHEMA public CASCADE` + `alembic upgrade head`) rather than repaired in place — `src/migrations/AGENTS.md` says a schema change extends the relevant layer rather than stacking a new revision "unless the layer is already applied in production". If layer 05 is ever applied in production, the rule reverses and the fix becomes a correction plus a revision that alters the existing columns. The consumer's `except ValueError` at `service.py:631` is deliberately left alone: with a text column `fromisoformat` can only raise `ValueError`, so widening it to catch `TypeError` would be handling an impossible scenario and would hide the exact regression the oracle exists to catch. **Do not "widen" these columns, or convert the ORM to `Mapped[datetime.time]`, without changing all three call sites.**
- A `time with time zone` column is unreadable to `time.fromisoformat`, and **`except ValueError` cannot catch that** — if a string-shaped column is ever migrated to a temporal one, the except clause must widen too. That is the general shape of this bug.

## Pre-PR Checks

```bash
uv run mypy src/app/modules/user_preferences/ && uv run ruff check src/app/modules/user_preferences/ && uv run pytest tests/unit/modules/user_preferences/ -v
```
