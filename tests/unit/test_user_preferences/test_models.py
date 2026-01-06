"""Unit tests for UserPreferences model.

Task: T083 [P] [US9] Write unit tests for UserPreferences model
"""


import pytest

from src.app.modules.user_preferences.models import DEFAULT_PREFERENCES, UserPreferences


class TestUserPreferencesModel:
    """Tests for UserPreferences SQLAlchemy model."""

    def test_model_has_required_columns(self):
        """Test that model has all required columns."""
        # Check column names
        columns = [c.name for c in UserPreferences.__table__.columns]

        assert "id" in columns
        assert "user_id" in columns
        assert "preferences" in columns
        assert "created_at" in columns
        assert "updated_at" in columns

    def test_model_tablename(self):
        """Test that model has correct table name."""
        assert UserPreferences.__tablename__ == "user_preferences"

    def test_model_user_id_is_unique(self):
        """Test that user_id column has unique constraint."""
        user_id_col = UserPreferences.__table__.c.user_id
        # Check if unique constraint exists
        assert user_id_col.unique is True or any(
            c.unique for c in UserPreferences.__table__.constraints
            if hasattr(c, "columns") and "user_id" in [col.name for col in c.columns]
        )

    def test_model_user_id_is_foreign_key(self):
        """Test that user_id references users table."""
        user_id_col = UserPreferences.__table__.c.user_id
        assert len(user_id_col.foreign_keys) > 0
        fk = list(user_id_col.foreign_keys)[0]
        assert "user.id" in str(fk.target_fullname)

    def test_default_preferences_has_all_keys(self):
        """Test that DEFAULT_PREFERENCES contains all expected keys."""
        expected_keys = {
            "theme",
            "language",
            "email_notifications",
            "push_notifications",
            "timezone",
            "date_format",
            "currency",
        }

        assert set(DEFAULT_PREFERENCES.keys()) == expected_keys

    def test_default_preferences_values(self):
        """Test default preference values."""
        assert DEFAULT_PREFERENCES["theme"] == "system"
        assert DEFAULT_PREFERENCES["language"] == "en"
        assert DEFAULT_PREFERENCES["email_notifications"] is True
        assert DEFAULT_PREFERENCES["push_notifications"] is True
        assert DEFAULT_PREFERENCES["timezone"] == "UTC"
        assert DEFAULT_PREFERENCES["date_format"] == "YYYY-MM-DD"
        assert DEFAULT_PREFERENCES["currency"] == "USD"

    def test_preferences_column_is_jsonb(self):
        """Test that preferences column uses JSONB type."""
        prefs_col = UserPreferences.__table__.c.preferences
        # Check the type is JSON or JSONB
        type_name = str(prefs_col.type).lower()
        assert "json" in type_name or "jsonb" in type_name


class TestUserPreferencesSchemas:
    """Tests for UserPreferences Pydantic schemas."""

    def test_theme_option_enum_values(self):
        """Test ThemeOption enum has all expected values."""
        from src.app.modules.user_preferences.schemas import ThemeOption

        assert ThemeOption.SYSTEM.value == "system"
        assert ThemeOption.LIGHT.value == "light"
        assert ThemeOption.DARK.value == "dark"

    def test_update_schema_all_fields_optional(self):
        """Test that UserPreferencesUpdate has all optional fields."""
        from src.app.modules.user_preferences.schemas import UserPreferencesUpdate

        # Create with no fields - should work
        update = UserPreferencesUpdate()
        assert update.theme is None
        assert update.language is None
        assert update.email_notifications is None

    def test_update_schema_validates_language_code(self):
        """Test language code validation (2 lowercase chars)."""
        from pydantic import ValidationError

        from src.app.modules.user_preferences.schemas import UserPreferencesUpdate

        # Valid
        update = UserPreferencesUpdate(language="en")
        assert update.language == "en"

        update = UserPreferencesUpdate(language="fr")
        assert update.language == "fr"

        # Invalid - too long
        with pytest.raises(ValidationError):
            UserPreferencesUpdate(language="eng")

        # Invalid - uppercase
        with pytest.raises(ValidationError):
            UserPreferencesUpdate(language="EN")

    def test_update_schema_validates_currency_code(self):
        """Test currency code validation (3 uppercase chars)."""
        from pydantic import ValidationError

        from src.app.modules.user_preferences.schemas import UserPreferencesUpdate

        # Valid
        update = UserPreferencesUpdate(currency="USD")
        assert update.currency == "USD"

        update = UserPreferencesUpdate(currency="EUR")
        assert update.currency == "EUR"

        # Invalid - lowercase
        with pytest.raises(ValidationError):
            UserPreferencesUpdate(currency="usd")

        # Invalid - wrong length
        with pytest.raises(ValidationError):
            UserPreferencesUpdate(currency="US")

    def test_update_schema_validates_theme(self):
        """Test theme validation."""
        from pydantic import ValidationError

        from src.app.modules.user_preferences.schemas import (
            ThemeOption,
            UserPreferencesUpdate,
        )

        # Valid
        update = UserPreferencesUpdate(theme=ThemeOption.DARK)
        assert update.theme == ThemeOption.DARK

        # Invalid
        with pytest.raises(ValidationError):
            UserPreferencesUpdate(theme="invalid_theme")

    def test_response_schema_has_all_fields(self):
        """Test UserPreferencesResponse has all expected fields."""
        from src.app.modules.user_preferences.schemas import UserPreferencesResponse

        fields = UserPreferencesResponse.model_fields
        expected = {
            "theme",
            "language",
            "email_notifications",
            "push_notifications",
            "timezone",
            "date_format",
            "currency",
            "updated_at",
        }
        assert set(fields.keys()) == expected
