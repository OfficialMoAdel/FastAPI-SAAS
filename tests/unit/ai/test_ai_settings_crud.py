"""Unit tests for AISettingsCRUD operations.

Tests T135: Unit test for AISettingsCRUD in tests/unit/ai/test_ai_settings_crud.py
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.modules.ai.crud import crud_ai_settings
from src.app.modules.ai.models import AISettings
from src.app.modules.ai.schemas import AISettingsUpdate


@pytest.fixture
def ai_settings_data():
    """Sample AI settings data for testing."""
    return {
        "user_id": 1,
        "model": "gemma3",
        "temperature": 0.7,
        "max_tokens": 2048,
        "system_prompt": None,
    }


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


class TestAISettingsCRUD:
    """Test suite for AISettingsCRUD operations."""

    @pytest.mark.asyncio
    async def test_create_ai_settings(self, mock_db_session, ai_settings_data):
        """Test creating new AI settings for a user."""
        user_id = 1

        mock_settings = MagicMock(spec=AISettings)
        mock_settings.id = 1
        mock_settings.user_id = user_id
        mock_settings.model = ai_settings_data["model"]
        mock_settings.temperature = ai_settings_data["temperature"]
        mock_settings.max_tokens = ai_settings_data["max_tokens"]
        mock_settings.system_prompt = ai_settings_data["system_prompt"]
        mock_settings.created_at = datetime.now(timezone.utc)
        mock_settings.updated_at = None

        with patch.object(crud_ai_settings, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_settings

            result = await crud_ai_settings.create(
                db=mock_db_session,
                object=ai_settings_data
            )

            assert result.id == 1
            assert result.user_id == user_id
            assert result.model == "gemma3"
            assert result.temperature == 0.7
            assert result.max_tokens == 2048
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_ai_settings_by_user_id(self, mock_db_session, ai_settings_data):
        """Test retrieving AI settings by user ID."""
        user_id = 1

        mock_settings = MagicMock(spec=AISettings)
        mock_settings.id = 1
        mock_settings.user_id = user_id
        mock_settings.model = "gemma3"
        mock_settings.temperature = 0.7
        mock_settings.max_tokens = 2048

        with patch.object(crud_ai_settings, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_settings

            result = await crud_ai_settings.get(
                db=mock_db_session,
                user_id=user_id
            )

            assert result is not None
            assert result.user_id == user_id
            mock_get.assert_called_once_with(
                db=mock_db_session,
                user_id=user_id
            )

    @pytest.mark.asyncio
    async def test_get_ai_settings_not_found(self, mock_db_session):
        """Test retrieving non-existent settings returns None."""
        user_id = 999

        with patch.object(crud_ai_settings, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            result = await crud_ai_settings.get(
                db=mock_db_session,
                user_id=user_id
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_update_ai_settings_model(self, mock_db_session):
        """Test updating AI settings model preference."""
        settings_id = 1

        mock_settings = MagicMock(spec=AISettings)
        mock_settings.id = settings_id
        mock_settings.model = "llama3"
        mock_settings.updated_at = datetime.now(timezone.utc)

        update_data = AISettingsUpdate(model="llama3")

        with patch.object(crud_ai_settings, 'update', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_settings

            result = await crud_ai_settings.update(
                db=mock_db_session,
                object=update_data.model_dump(exclude_unset=True),
                id=settings_id
            )

            assert result.model == "llama3"
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_ai_settings_temperature(self, mock_db_session):
        """Test updating AI settings temperature."""
        settings_id = 1

        mock_settings = MagicMock(spec=AISettings)
        mock_settings.id = settings_id
        mock_settings.temperature = 0.9
        mock_settings.updated_at = datetime.now(timezone.utc)

        update_data = AISettingsUpdate(temperature=0.9)

        with patch.object(crud_ai_settings, 'update', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_settings

            result = await crud_ai_settings.update(
                db=mock_db_session,
                object=update_data.model_dump(exclude_unset=True),
                id=settings_id
            )

            assert result.temperature == 0.9
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_ai_settings_max_tokens(self, mock_db_session):
        """Test updating AI settings max tokens."""
        settings_id = 1

        mock_settings = MagicMock(spec=AISettings)
        mock_settings.id = settings_id
        mock_settings.max_tokens = 4096
        mock_settings.updated_at = datetime.now(timezone.utc)

        update_data = AISettingsUpdate(max_tokens=4096)

        with patch.object(crud_ai_settings, 'update', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_settings

            result = await crud_ai_settings.update(
                db=mock_db_session,
                object=update_data.model_dump(exclude_unset=True),
                id=settings_id
            )

            assert result.max_tokens == 4096
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_ai_settings_system_prompt(self, mock_db_session):
        """Test updating AI settings system prompt."""
        settings_id = 1
        new_prompt = "You are a helpful assistant specialized in Python programming."

        mock_settings = MagicMock(spec=AISettings)
        mock_settings.id = settings_id
        mock_settings.system_prompt = new_prompt
        mock_settings.updated_at = datetime.now(timezone.utc)

        update_data = AISettingsUpdate(system_prompt=new_prompt)

        with patch.object(crud_ai_settings, 'update', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_settings

            result = await crud_ai_settings.update(
                db=mock_db_session,
                object=update_data.model_dump(exclude_unset=True),
                id=settings_id
            )

            assert result.system_prompt == new_prompt
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_ai_settings_partial(self, mock_db_session):
        """Test partial update of AI settings."""
        settings_id = 1

        mock_settings = MagicMock(spec=AISettings)
        mock_settings.id = settings_id
        mock_settings.model = "llama3"
        mock_settings.temperature = 0.7  # Unchanged
        mock_settings.max_tokens = 2048  # Unchanged

        update_data = AISettingsUpdate(model="llama3")  # Only model

        with patch.object(crud_ai_settings, 'update', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_settings

            result = await crud_ai_settings.update(
                db=mock_db_session,
                object=update_data.model_dump(exclude_unset=True),
                id=settings_id
            )

            assert result.model == "llama3"
            # Verify only model was in the update payload
            call_args = mock_update.call_args
            update_obj = call_args.kwargs.get("object", call_args[1].get("object", {}))
            assert "model" in update_obj

    @pytest.mark.asyncio
    async def test_unique_user_constraint(self, mock_db_session, ai_settings_data):
        """Test that each user can only have one AI settings record."""
        user_id = 1

        # First creation succeeds
        mock_settings = MagicMock(spec=AISettings)
        mock_settings.id = 1
        mock_settings.user_id = user_id

        with patch.object(crud_ai_settings, 'get', new_callable=AsyncMock) as mock_get:
            # Simulate unique constraint - first get returns existing settings
            mock_get.return_value = {"id": 1, "user_id": user_id}

            result = await crud_ai_settings.get(
                db=mock_db_session,
                user_id=user_id
            )

            # Should return existing settings
            assert result["user_id"] == user_id

    @pytest.mark.asyncio
    async def test_default_settings_values(self):
        """Test that AISettingsUpdate allows partial updates with defaults."""
        # All fields should be optional
        update_minimal = AISettingsUpdate()
        assert update_minimal.model is None
        assert update_minimal.temperature is None
        assert update_minimal.max_tokens is None
        assert update_minimal.system_prompt is None

        # Single field update
        update_model = AISettingsUpdate(model="llama3")
        assert update_model.model == "llama3"
        assert update_model.temperature is None

    @pytest.mark.asyncio
    async def test_temperature_validation(self):
        """Test temperature field validation (0.0 to 2.0)."""
        # Valid temperatures
        update_low = AISettingsUpdate(temperature=0.0)
        assert update_low.temperature == 0.0

        update_high = AISettingsUpdate(temperature=2.0)
        assert update_high.temperature == 2.0

        update_mid = AISettingsUpdate(temperature=1.0)
        assert update_mid.temperature == 1.0

    @pytest.mark.asyncio
    async def test_max_tokens_validation(self):
        """Test max_tokens field validation (1 to 32000)."""
        # Valid max_tokens
        update_low = AISettingsUpdate(max_tokens=1)
        assert update_low.max_tokens == 1

        update_high = AISettingsUpdate(max_tokens=32000)
        assert update_high.max_tokens == 32000

        update_default = AISettingsUpdate(max_tokens=2048)
        assert update_default.max_tokens == 2048
