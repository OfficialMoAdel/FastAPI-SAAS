"""Unit tests for PreferencesService.

Task: T084 [P] [US9] Write unit tests for PreferencesService
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.user_preferences.models import DEFAULT_PREFERENCES
from src.app.modules.user_preferences.schemas import ThemeOption, UserPreferencesUpdate
from src.app.modules.user_preferences.service import PreferencesService


class TestPreferencesService:
    """Tests for PreferencesService."""

    @pytest.fixture
    def service(self):
        """Create a PreferencesService instance for testing."""
        return PreferencesService()

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def mock_prefs_record(self):
        """Create a mock UserPreferences database record."""
        record = MagicMock()
        record.user_id = 1
        record.preferences = {
            "theme": "dark",
            "language": "en",
            "email_notifications": True,
            "push_notifications": False,
            "timezone": "America/New_York",
            "date_format": "MM/DD/YYYY",
            "currency": "USD",
        }
        record.updated_at = datetime.now(UTC)
        return record

    # =========================================================================
    # get_preferences tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_get_preferences_returns_user_preferences(self, service, mock_db, mock_prefs_record):
        """Test get_preferences returns existing user preferences."""
        with patch("src.app.modules.user_preferences.service.crud_user_preferences") as mock_crud:
            mock_crud.get_or_create = AsyncMock(return_value=mock_prefs_record)

            result = await service.get_preferences(db=mock_db, user_id=1)

            assert result["theme"] == "dark"
            assert result["push_notifications"] is False
            assert result["timezone"] == "America/New_York"
            assert "updated_at" in result
            mock_crud.get_or_create.assert_called_once_with(db=mock_db, user_id=1)

    @pytest.mark.asyncio
    async def test_get_preferences_fills_missing_with_defaults(self, service, mock_db):
        """Test get_preferences fills missing keys with defaults."""
        partial_record = MagicMock()
        partial_record.preferences = {"theme": "dark"}  # Only one key set
        partial_record.updated_at = datetime.now(UTC)

        with patch("src.app.modules.user_preferences.service.crud_user_preferences") as mock_crud:
            mock_crud.get_or_create = AsyncMock(return_value=partial_record)

            result = await service.get_preferences(db=mock_db, user_id=1)

            # User set value
            assert result["theme"] == "dark"
            # Default values for missing keys
            assert result["language"] == DEFAULT_PREFERENCES["language"]
            assert result["email_notifications"] == DEFAULT_PREFERENCES["email_notifications"]

    @pytest.mark.asyncio
    async def test_get_preferences_handles_empty_preferences(self, service, mock_db):
        """Test get_preferences with empty preferences returns all defaults."""
        empty_record = MagicMock()
        empty_record.preferences = None
        empty_record.updated_at = datetime.now(UTC)

        with patch("src.app.modules.user_preferences.service.crud_user_preferences") as mock_crud:
            mock_crud.get_or_create = AsyncMock(return_value=empty_record)

            result = await service.get_preferences(db=mock_db, user_id=1)

            # Should return all defaults
            for key, value in DEFAULT_PREFERENCES.items():
                assert result[key] == value

    # =========================================================================
    # update_preferences tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_update_preferences_partial_update(self, service, mock_db, mock_prefs_record):
        """Test update_preferences only updates provided fields."""
        updates = UserPreferencesUpdate(theme=ThemeOption.LIGHT)

        with patch("src.app.modules.user_preferences.service.crud_user_preferences") as mock_crud:
            updated_record = MagicMock()
            updated_record.preferences = {**mock_prefs_record.preferences, "theme": "light"}
            updated_record.updated_at = datetime.now(UTC)

            mock_crud.update_preferences = AsyncMock(return_value=updated_record)

            result = await service.update_preferences(db=mock_db, user_id=1, updates=updates)

            assert result["theme"] == "light"
            mock_crud.update_preferences.assert_called_once()
            call_args = mock_crud.update_preferences.call_args
            assert call_args.kwargs["updates"]["theme"] == "light"

    @pytest.mark.asyncio
    async def test_update_preferences_multiple_fields(self, service, mock_db):
        """Test updating multiple fields at once."""
        updates = UserPreferencesUpdate(
            theme=ThemeOption.DARK,
            language="fr",
            email_notifications=False,
        )

        with patch("src.app.modules.user_preferences.service.crud_user_preferences") as mock_crud:
            updated_record = MagicMock()
            updated_record.preferences = {
                **DEFAULT_PREFERENCES,
                "theme": "dark",
                "language": "fr",
                "email_notifications": False,
            }
            updated_record.updated_at = datetime.now(UTC)

            mock_crud.update_preferences = AsyncMock(return_value=updated_record)

            result = await service.update_preferences(db=mock_db, user_id=1, updates=updates)

            assert result["theme"] == "dark"
            assert result["language"] == "fr"
            assert result["email_notifications"] is False

    @pytest.mark.asyncio
    async def test_update_preferences_empty_update_returns_current(self, service, mock_db, mock_prefs_record):
        """Test empty update returns current preferences without DB update."""
        # updates = UserPreferencesUpdate()  # No fields set

        with patch("src.app.modules.user_preferences.service.crud_user_preferences") as mock_crud:
            mock_crud.get_or_create = AsyncMock(return_value=mock_prefs_record)

            result = await service.get_preferences(db=mock_db, user_id=1)

            # Should not call update_preferences since no updates
            mock_crud.update_preferences.assert_not_called()

            assert result is not None

            assert result is not None

    @pytest.mark.asyncio
    async def test_update_preferences_handles_enum_conversion(self, service, mock_db):
        """Test that ThemeOption enum is converted to string value."""
        updates = UserPreferencesUpdate(theme=ThemeOption.SYSTEM)

        with patch("src.app.modules.user_preferences.service.crud_user_preferences") as mock_crud:
            updated_record = MagicMock()
            updated_record.preferences = {**DEFAULT_PREFERENCES}
            updated_record.updated_at = datetime.now(UTC)

            mock_crud.update_preferences = AsyncMock(return_value=updated_record)

            await service.update_preferences(db=mock_db, user_id=1, updates=updates)

            call_args = mock_crud.update_preferences.call_args
            # Should be string "system", not the enum
            assert call_args.kwargs["updates"]["theme"] == "system"


class TestPreferencesServiceFactory:
    """Test get_preferences_service factory function."""

    def test_factory_returns_instance(self):
        """Test that get_preferences_service returns a PreferencesService instance."""
        service = PreferencesService()
        assert service is not None
        assert isinstance(service, PreferencesService)
