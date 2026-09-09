"""Unit tests for AISettingsService operations.

Tests T136: Unit test for AISettingsService in tests/unit/ai/test_ai_settings_service.py
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.ai.router import get_ai_settings_service
from src.app.modules.ai.schemas import AISettingsUpdate
from src.app.modules.ai.service import AISettingsService


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def mock_tenant_context():
    """Create a mock tenant context."""
    tenant = MagicMock()
    tenant.id = 1
    return tenant


@pytest.fixture
def ai_settings_service(mock_db_session):
    """Create an AISettingsService instance with mock database."""
    return AISettingsService(mock_db_session)


@pytest.fixture
def sample_settings():
    """Sample AI settings dict."""
    return {
        "id": 1,
        "user_id": 1,
        "tenant_id": 1,
        "model": "gemma3",
        "temperature": 0.7,
        "max_tokens": 2048,
        "system_prompt": None,
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }


class TestAISettingsService:
    """Test suite for AISettingsService."""

    @pytest.mark.asyncio
    async def test_get_settings_existing(self, ai_settings_service, sample_settings, mock_tenant_context):
        """Test getting existing AI settings for a user."""
        user_id = 1

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(return_value=sample_settings)

                result = await ai_settings_service.get_settings(user_id)

                assert result is not None
                assert result["user_id"] == user_id
                assert result["model"] == "gemma3"
                mock_crud.get.assert_called_once_with(db=ai_settings_service.db, user_id=user_id, tenant_id=1)

    @pytest.mark.asyncio
    async def test_get_settings_not_found(self, ai_settings_service, mock_tenant_context):
        """Test getting settings for user without settings returns None."""
        user_id = 999

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(return_value=None)

                result = await ai_settings_service.get_settings(user_id)

                assert result is None

    @pytest.mark.asyncio
    async def test_get_or_create_settings_existing(self, ai_settings_service, sample_settings, mock_tenant_context):
        """Test get_or_create returns existing settings."""
        user_id = 1

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(return_value=sample_settings)

                result = await ai_settings_service.get_or_create_settings(user_id)

                assert result is not None
                assert result["user_id"] == user_id
                mock_crud.get.assert_called_once_with(db=ai_settings_service.db, user_id=user_id, tenant_id=1)
                mock_crud.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_or_create_settings_creates_defaults(self, ai_settings_service, mock_tenant_context):
        """Test get_or_create creates default settings when none exist."""
        user_id = 1
        new_settings = {
            "id": 1,
            "user_id": user_id,
            "tenant_id": 1,
            "model": "gemma3",
            "temperature": 0.7,
            "max_tokens": 2048,
            "system_prompt": None,
        }

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(return_value=None)
                mock_crud.create = AsyncMock(return_value=new_settings)

                result = await ai_settings_service.get_or_create_settings(user_id)

                assert result is not None
                assert result["model"] == "gemma3"
                assert result["temperature"] == 0.7
                assert result["max_tokens"] == 2048
                mock_crud.create.assert_called_once()
                # Verify default values were used - now the service passes a dict
                call_args = mock_crud.create.call_args
                create_obj = call_args.kwargs.get("object", {})
                assert create_obj["model"] == "gemma3"
                assert create_obj["tenant_id"] == 1

    @pytest.mark.asyncio
    async def test_update_settings_existing(self, ai_settings_service, sample_settings, mock_tenant_context):
        """Test updating existing AI settings."""
        user_id = 1
        updated_settings = {**sample_settings, "model": "llama3"}

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(side_effect=[sample_settings, updated_settings])
                mock_crud.update = AsyncMock(return_value=None)

                update_data = AISettingsUpdate(model="llama3")
                result = await ai_settings_service.update_settings(user_id, update_data)

                assert result is not None
                assert result["model"] == "llama3"
                mock_crud.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_settings_creates_if_not_exists(self, ai_settings_service, mock_tenant_context):
        """Test updating settings creates new record if none exists."""
        user_id = 1
        new_settings = {
            "id": 1,
            "user_id": user_id,
            "tenant_id": 1,
            "model": "llama3",
            "temperature": 0.7,
            "max_tokens": 2048,
            "system_prompt": None,
        }

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(return_value=None)
                mock_crud.create = AsyncMock(return_value=new_settings)

                update_data = AISettingsUpdate(model="llama3")
                result = await ai_settings_service.update_settings(user_id, update_data)

                assert result is not None
                assert result["model"] == "llama3"
                mock_crud.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_settings_partial(self, ai_settings_service, sample_settings, mock_tenant_context):
        """Test partial update only changes specified fields."""
        user_id = 1
        updated_settings = {**sample_settings, "temperature": 0.9}

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(side_effect=[sample_settings, updated_settings])
                mock_crud.update = AsyncMock(return_value=None)

                update_data = AISettingsUpdate(temperature=0.9)
                result = await ai_settings_service.update_settings(user_id, update_data)

                assert result is not None
                assert result["temperature"] == 0.9
                assert result["model"] == "gemma3"  # Unchanged

    @pytest.mark.asyncio
    async def test_update_settings_system_prompt(self, ai_settings_service, sample_settings, mock_tenant_context):
        """Test updating system prompt."""
        user_id = 1
        new_prompt = "You are a helpful coding assistant."
        updated_settings = {**sample_settings, "system_prompt": new_prompt}

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(side_effect=[sample_settings, updated_settings])
                mock_crud.update = AsyncMock(return_value=None)

                update_data = AISettingsUpdate(system_prompt=new_prompt)
                result = await ai_settings_service.update_settings(user_id, update_data)

                assert result is not None
                assert result["system_prompt"] == new_prompt

    @pytest.mark.asyncio
    async def test_update_settings_multiple_fields(self, ai_settings_service, sample_settings, mock_tenant_context):
        """Test updating multiple fields at once."""
        user_id = 1
        updated_settings = {
            **sample_settings,
            "model": "mistral",
            "temperature": 1.0,
            "max_tokens": 4096,
        }

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(side_effect=[sample_settings, updated_settings])
                mock_crud.update = AsyncMock(return_value=None)

                update_data = AISettingsUpdate(model="mistral", temperature=1.0, max_tokens=4096)
                result = await ai_settings_service.update_settings(user_id, update_data)

                assert result is not None
                assert result["model"] == "mistral"
                assert result["temperature"] == 1.0
                assert result["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_update_adds_updated_at(self, ai_settings_service, sample_settings, mock_tenant_context):
        """Test that update adds updated_at timestamp."""
        user_id = 1

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(side_effect=[sample_settings, sample_settings])
                mock_crud.update = AsyncMock(return_value=None)

                update_data = AISettingsUpdate(model="llama3")
                await ai_settings_service.update_settings(user_id, update_data)

                # Verify updated_at was included in the update
                call_args = mock_crud.update.call_args
                update_obj = call_args.kwargs.get("object", {})
                assert "updated_at" in update_obj


class TestAISettingsServiceFactory:
    """Test suite for AISettingsService factory function."""

    def test_get_ai_settings_service(self, mock_db_session):
        """Test factory function creates service correctly."""
        service = get_ai_settings_service(mock_db_session)

        assert isinstance(service, AISettingsService)
        assert service.db == mock_db_session


class TestDefaultAISettings:
    """Test suite for default AI settings values."""

    @pytest.mark.asyncio
    async def test_default_model_is_gemma3(self, ai_settings_service, mock_tenant_context):
        """Test that default model is gemma3."""
        user_id = 1
        new_settings = {
            "id": 1,
            "user_id": user_id,
            "tenant_id": 1,
            "model": "gemma3",
            "temperature": 0.7,
            "max_tokens": 2048,
            "system_prompt": None,
        }

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(return_value=None)
                mock_crud.create = AsyncMock(return_value=new_settings)

                result = await ai_settings_service.get_or_create_settings(user_id)

                assert result["model"] == "gemma3"

    @pytest.mark.asyncio
    async def test_default_temperature_is_0_7(self, ai_settings_service, mock_tenant_context):
        """Test that default temperature is 0.7."""
        user_id = 1
        new_settings = {
            "id": 1,
            "user_id": user_id,
            "tenant_id": 1,
            "model": "gemma3",
            "temperature": 0.7,
            "max_tokens": 2048,
            "system_prompt": None,
        }

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(return_value=None)
                mock_crud.create = AsyncMock(return_value=new_settings)

                result = await ai_settings_service.get_or_create_settings(user_id)

                assert result["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_default_max_tokens_is_2048(self, ai_settings_service, mock_tenant_context):
        """Test that default max_tokens is 2048."""
        user_id = 1
        new_settings = {
            "id": 1,
            "user_id": user_id,
            "tenant_id": 1,
            "model": "gemma3",
            "temperature": 0.7,
            "max_tokens": 2048,
            "system_prompt": None,
        }

        with patch("src.app.modules.ai.service.get_current_tenant", return_value=mock_tenant_context):
            with patch("src.app.modules.ai.service.crud_ai_settings") as mock_crud:
                mock_crud.get = AsyncMock(return_value=None)
                mock_crud.create = AsyncMock(return_value=new_settings)

                result = await ai_settings_service.get_or_create_settings(user_id)

                assert result["max_tokens"] == 2048
