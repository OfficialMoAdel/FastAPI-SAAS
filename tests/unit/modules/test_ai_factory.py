"""Unit tests for AI Provider Factory."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.modules.ai.anthropic_provider import AnthropicProvider
from src.app.modules.ai.exceptions import AIServerError
from src.app.modules.ai.factory import AIProviderFactory, get_provider
from src.app.modules.ai.ollama_provider import OllamaProvider
from src.app.modules.ai.openai_provider import OpenAIProvider
from src.app.modules.ai.providers import AIProvider


@pytest.fixture(autouse=True)
def clear_factory_cache():
    """Clear factory cache before each test."""
    AIProviderFactory.clear_cache()
    yield
    AIProviderFactory.clear_cache()


@pytest.fixture
def mock_settings():
    """Mock settings for testing."""
    with patch("src.app.modules.ai.factory.settings") as mock:
        mock.AI_PROVIDER = "ollama"
        mock.OPENAI_API_KEY = "test-openai-key"
        mock.ANTHROPIC_API_KEY = "test-anthropic-key"
        mock.OLLAMA_BASE_URL = "http://localhost:11434"
        mock.OPENAI_BASE_URL = None
        mock.ANTHROPIC_BASE_URL = None
        yield mock


@pytest.fixture
def mock_redis():
    """Mock Redis client module-level variable."""
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=None)
    with patch("src.app.core.utils.cache.client", mock_client):
        yield mock_client


class TestAIProviderFactory:
    """Test AI Provider Factory functionality."""

    async def test_factory_get_default_provider(self, mock_settings):
        """Test getting default provider from settings."""
        provider = AIProviderFactory.get_provider()

        assert isinstance(provider, AIProvider)
        assert isinstance(provider, OllamaProvider)
        assert provider.provider_name == "ollama"

    async def test_factory_get_specific_provider_openai(self, mock_settings):
        """Test getting specific OpenAI provider."""
        provider = AIProviderFactory.get_provider(provider_name="openai")

        assert isinstance(provider, AIProvider)
        assert isinstance(provider, OpenAIProvider)
        assert provider.provider_name == "openai"

    async def test_factory_get_specific_provider_anthropic(self, mock_settings):
        """Test getting specific Anthropic provider."""
        provider = AIProviderFactory.get_provider(provider_name="anthropic")

        assert isinstance(provider, AIProvider)
        assert isinstance(provider, AnthropicProvider)
        assert provider.provider_name == "anthropic"

    async def test_factory_get_specific_provider_ollama(self, mock_settings):
        """Test getting specific Ollama provider."""
        provider = AIProviderFactory.get_provider(provider_name="ollama")

        assert isinstance(provider, AIProvider)
        assert isinstance(provider, OllamaProvider)
        assert provider.provider_name == "ollama"

    async def test_factory_caches_instances(self, mock_settings):
        """Test that factory caches provider instances."""
        provider1 = AIProviderFactory.get_provider(provider_name="openai")
        provider2 = AIProviderFactory.get_provider(provider_name="openai")

        assert provider1 is provider2
        assert len(AIProviderFactory._instances) == 1

    async def test_factory_creates_different_instances_for_different_providers(self, mock_settings):
        """Test that different providers get different instances."""
        openai_provider = AIProviderFactory.get_provider(provider_name="openai")
        anthropic_provider = AIProviderFactory.get_provider(provider_name="anthropic")

        assert openai_provider is not anthropic_provider
        assert len(AIProviderFactory._instances) == 2

    async def test_factory_unsupported_provider_raises_error(self, mock_settings):
        """Test that requesting unsupported provider raises error."""
        with pytest.raises(AIServerError) as exc_info:
            AIProviderFactory.get_provider(provider_name="invalid_provider")

        assert "Unsupported AI provider" in str(exc_info.value)
        assert exc_info.value.provider == "invalid_provider"

    async def test_factory_clear_cache(self, mock_settings):
        """Test clearing factory cache."""
        AIProviderFactory.get_provider(provider_name="openai")
        assert len(AIProviderFactory._instances) == 1

        AIProviderFactory.clear_cache()
        assert len(AIProviderFactory._instances) == 0

    async def test_factory_get_supported_providers(self):
        """Test getting list of supported providers."""
        providers = AIProviderFactory.get_supported_providers()

        assert "openai" in providers
        assert "anthropic" in providers
        assert "ollama" in providers
        assert len(providers) == 3

    async def test_factory_with_api_key_override(self, mock_settings):
        """Test factory with API key override."""
        provider = AIProviderFactory.get_provider(
            provider_name="openai",
            api_key="custom-api-key",
        )

        assert isinstance(provider, OpenAIProvider)
        assert provider.provider_name == "openai"

    async def test_factory_with_base_url_override(self, mock_settings):
        """Test factory with base URL override."""
        provider = AIProviderFactory.get_provider(
            provider_name="ollama",
            base_url="http://custom-ollama:11434",
        )

        assert isinstance(provider, OllamaProvider)
        assert provider.provider_name == "ollama"

    async def test_factory_provider_creation_failure(self, mock_settings):
        """Test that provider creation failure raises error."""
        with patch.object(OpenAIProvider, "__init__", side_effect=Exception("Init failed")):
            with pytest.raises(AIServerError) as exc_info:
                AIProviderFactory.get_provider(provider_name="openai")

            assert "Failed to create provider" in str(exc_info.value)


class TestProviderInterfaceCompliance:
    """Test that all providers implement AIProvider interface."""

    @pytest.mark.parametrize("provider_name", ["openai", "anthropic", "ollama"])
    async def test_all_providers_have_provider_name(self, provider_name, mock_settings):
        """Test that all providers have provider_name property."""
        provider = AIProviderFactory.get_provider(provider_name=provider_name)

        assert hasattr(provider, "provider_name")
        assert isinstance(provider.provider_name, str)

    @pytest.mark.parametrize("provider_name", ["openai", "anthropic", "ollama"])
    async def test_all_providers_have_generate_text(self, provider_name, mock_settings):
        """Test that all providers have generate_text method."""
        provider = AIProviderFactory.get_provider(provider_name=provider_name)

        assert hasattr(provider, "generate_text")
        assert callable(provider.generate_text)

    @pytest.mark.parametrize("provider_name", ["openai", "anthropic", "ollama"])
    async def test_all_providers_have_generate_text_stream(self, provider_name, mock_settings):
        """Test that all providers have generate_text_stream method."""
        provider = AIProviderFactory.get_provider(provider_name=provider_name)

        assert hasattr(provider, "generate_text_stream")
        assert callable(provider.generate_text_stream)

    @pytest.mark.parametrize("provider_name", ["openai", "anthropic", "ollama"])
    async def test_all_providers_have_generate_json(self, provider_name, mock_settings):
        """Test that all providers have generate_json method."""
        provider = AIProviderFactory.get_provider(provider_name=provider_name)

        assert hasattr(provider, "generate_json")
        assert callable(provider.generate_json)

    @pytest.mark.parametrize("provider_name", ["openai", "anthropic", "ollama"])
    async def test_all_providers_have_embed_text(self, provider_name, mock_settings):
        """Test that all providers have embed_text method."""
        provider = AIProviderFactory.get_provider(provider_name=provider_name)

        assert hasattr(provider, "embed_text")
        assert callable(provider.embed_text)

    @pytest.mark.parametrize("provider_name", ["openai", "anthropic", "ollama"])
    async def test_all_providers_have_list_models(self, provider_name, mock_settings):
        """Test that all providers have list_models method."""
        provider = AIProviderFactory.get_provider(provider_name=provider_name)

        assert hasattr(provider, "list_models")
        assert callable(provider.list_models)

    @pytest.mark.parametrize("provider_name", ["openai", "anthropic", "ollama"])
    async def test_all_providers_have_health_check(self, provider_name, mock_settings):
        """Test that all providers have health_check method."""
        provider = AIProviderFactory.get_provider(provider_name=provider_name)

        assert hasattr(provider, "health_check")
        assert callable(provider.health_check)

    @pytest.mark.parametrize("provider_name", ["openai", "anthropic", "ollama"])
    async def test_all_providers_have_close(self, provider_name, mock_settings):
        """Test that all providers have close method."""
        provider = AIProviderFactory.get_provider(provider_name=provider_name)

        assert hasattr(provider, "close")
        assert callable(provider.close)


class TestGetProviderConvenienceFunction:
    """Test get_provider convenience function."""

    async def test_get_provider_returns_instance(self, mock_settings):
        """Test that get_provider returns provider instance."""
        provider = get_provider()

        assert isinstance(provider, AIProvider)

    async def test_get_provider_with_args(self, mock_settings):
        """Test get_provider with arguments."""
        provider = get_provider(provider_name="anthropic", api_key="test-key")

        assert isinstance(provider, AIProvider)
        assert isinstance(provider, AnthropicProvider)


class TestOpenAIProviderImplementation:
    """Test OpenAI provider implementation."""

    async def test_openai_provider_initialization(self, mock_settings):
        """Test OpenAI provider initializes correctly."""
        provider = AIProviderFactory.get_provider(provider_name="openai")

        assert provider.provider_name == "openai"
        assert isinstance(provider, OpenAIProvider)

    async def test_openai_provider_health_check(self, mock_settings):
        """Test OpenAI provider health check."""
        provider = AIProviderFactory.get_provider(provider_name="openai")

        is_healthy = await provider.health_check()

        assert isinstance(is_healthy, bool)

    async def test_openai_provider_list_models(self, mock_settings):
        """Test OpenAI provider list models."""
        provider = AIProviderFactory.get_provider(provider_name="openai")

        # Mock the HTTP response for list_models
        with patch.object(provider, "list_models", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = ["gpt-4", "gpt-3.5-turbo"]
            models = await provider.list_models()

            assert isinstance(models, list)
            assert len(models) > 0


class TestAnthropicProviderImplementation:
    """Test Anthropic provider implementation."""

    async def test_anthropic_provider_initialization(self, mock_settings):
        """Test Anthropic provider initializes correctly."""
        provider = AIProviderFactory.get_provider(provider_name="anthropic")

        assert provider.provider_name == "anthropic"
        assert isinstance(provider, AnthropicProvider)

    async def test_anthropic_provider_health_check(self, mock_settings):
        """Test Anthropic provider health check."""
        provider = AIProviderFactory.get_provider(provider_name="anthropic")

        is_healthy = await provider.health_check()

        assert isinstance(is_healthy, bool)

    async def test_anthropic_provider_list_models(self, mock_settings):
        """Test Anthropic provider list models."""
        provider = AIProviderFactory.get_provider(provider_name="anthropic")

        models = await provider.list_models()

        assert isinstance(models, list)


class TestOllamaProviderImplementation:
    """Test Ollama provider implementation."""

    async def test_ollama_provider_initialization(self, mock_settings):
        """Test Ollama provider initializes correctly."""
        provider = AIProviderFactory.get_provider(provider_name="ollama")

        assert provider.provider_name == "ollama"
        assert isinstance(provider, OllamaProvider)

    async def test_ollama_provider_health_check(self, mock_settings):
        """Test Ollama provider health check."""
        provider = AIProviderFactory.get_provider(provider_name="ollama")

        is_healthy = await provider.health_check()

        assert isinstance(is_healthy, bool)

    async def test_ollama_provider_list_models(self, mock_settings):
        """Test Ollama provider list models."""
        provider = AIProviderFactory.get_provider(provider_name="ollama")

        # Mock the HTTP response for list_models (Ollama may not be running)
        with patch.object(provider, "list_models", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = ["llama2", "mistral"]
            models = await provider.list_models()

            assert isinstance(models, list)
            assert len(models) > 0
