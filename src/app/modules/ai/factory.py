"""AI Provider Factory for managing multiple AI providers.

Provides a unified interface for accessing different AI providers
(OpenAI, Anthropic, Ollama) with caching and configuration management.
"""

import hashlib
import logging
from typing import Any

from src.app.core.config import settings
from src.app.modules.ai.anthropic_provider import AnthropicProvider
from src.app.modules.ai.exceptions import (
    AIServerError,
)
from src.app.modules.ai.ollama_provider import OllamaProvider
from src.app.modules.ai.openai_provider import OpenAIProvider
from src.app.modules.ai.providers import AIProvider

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "ollama"
SUPPORTED_PROVIDERS = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
}


class AIProviderFactory:
    """Factory for managing AI provider instances.

    Implements singleton pattern with lazy initialization and instance caching.
    """

    _instances: dict[str, AIProvider] = {}

    @classmethod
    def _get_cache_key(cls, provider_name: str, api_key: str | None, base_url: str | None) -> str:
        """Generate a cache key that includes credentials.

        Args:
            provider_name: Name of the provider.
            api_key: Optional API key.
            base_url: Optional base URL.

        Returns:
            Cache key string.
        """
        key_parts = [provider_name]
        if api_key:
            # Hash the API key to avoid storing it in memory as cache key
            key_parts.append(hashlib.sha256(api_key.encode()).hexdigest()[:16])
        if base_url:
            key_parts.append(base_url)
        return ":".join(key_parts)

    @classmethod
    def get_provider(
        cls,
        provider_name: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> AIProvider:
        """Get or create an AI provider instance.

        Args:
            provider_name: Name of the provider ('openai', 'anthropic', 'ollama').
                          Defaults to settings.AI_PROVIDER or DEFAULT_PROVIDER.
            api_key: Optional API key override. If None, uses settings.
            base_url: Optional base URL override. If None, uses settings.

        Returns:
            AIProvider instance.

        Raises:
            AIInvalidRequestError: If provider name is not supported.

        Note:
            Instances are cached by provider_name + api_key_hash + base_url.
            Different credentials will create separate cached instances.
        """
        if provider_name is None:
            provider_name = getattr(settings, "AI_PROVIDER", DEFAULT_PROVIDER)

        if provider_name not in SUPPORTED_PROVIDERS:
            raise AIServerError(
                provider=provider_name,
                message=f"Unsupported AI provider: {provider_name}",
            )

        provider_class = SUPPORTED_PROVIDERS[provider_name]

        # Generate cache key that includes credentials
        cache_key = cls._get_cache_key(provider_name, api_key, base_url)

        if cache_key in cls._instances:
            logger.debug(f"Returning cached provider instance: {provider_name}")
            return cls._instances[cache_key]

        logger.info(f"Creating new provider instance: {provider_name}")

        try:
            provider = cls._create_provider(provider_class, provider_name, api_key, base_url)  # type: ignore[arg-type]
            cls._instances[cache_key] = provider
            return provider
        except Exception as e:
            logger.error(f"Failed to create provider {provider_name}: {e}")
            raise AIServerError(
                provider=provider_name,
                message=f"Failed to create provider: {e}",
            ) from e

    @classmethod
    def _create_provider(
        cls,
        provider_class: type[AIProvider],
        provider_name: str,
        api_key: str | None,
        base_url: str | None,
    ) -> AIProvider:
        """Create a provider instance with appropriate configuration.

        Args:
            provider_class: The provider class to instantiate.
            provider_name: Name of the provider for logging.
            api_key: Optional API key override.
            base_url: Optional base URL override.

        Returns:
            Initialized AIProvider instance.

        Raises:
            AIServerError: If required API key is missing.
        """
        provider_config: dict[str, Any] = {}

        if provider_name == "openai":
            resolved_api_key = api_key or getattr(settings, "OPENAI_API_KEY", None)
            if not resolved_api_key:
                raise AIServerError(
                    provider=provider_name,
                    message="Missing API key for OpenAI. Set OPENAI_API_KEY environment variable.",
                )
            provider_config["api_key"] = resolved_api_key
            resolved_base_url = base_url or getattr(settings, "OPENAI_BASE_URL", None)
            if resolved_base_url:
                provider_config["base_url"] = resolved_base_url
        elif provider_name == "anthropic":
            resolved_api_key = api_key or getattr(settings, "ANTHROPIC_API_KEY", None)
            if not resolved_api_key:
                raise AIServerError(
                    provider=provider_name,
                    message="Missing API key for Anthropic. Set ANTHROPIC_API_KEY environment variable.",
                )
            provider_config["api_key"] = resolved_api_key
            resolved_base_url = base_url or getattr(settings, "ANTHROPIC_BASE_URL", None)
            if resolved_base_url:
                provider_config["base_url"] = resolved_base_url
        elif provider_name == "ollama":
            provider_config["base_url"] = base_url or settings.OLLAMA_BASE_URL

        provider_instance = provider_class(**provider_config)
        assert isinstance(provider_instance, AIProvider)
        return provider_instance

    @classmethod
    def clear_cache(cls) -> None:
        """Clear all cached provider instances.

        Useful for testing or when configuration changes.
        """
        cls._instances.clear()
        logger.info("Cleared all provider instance caches")

    @classmethod
    def get_supported_providers(cls) -> list[str]:
        """Get list of supported provider names.

        Returns:
            List of provider names.
        """
        return list(SUPPORTED_PROVIDERS.keys())


def get_provider(
    provider_name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> AIProvider:
    """Convenience function to get an AI provider.

    Args:
        provider_name: Name of the provider.
        api_key: Optional API key override.
        base_url: Optional base URL override.

    Returns:
        AIProvider instance.
    """
    return AIProviderFactory.get_provider(provider_name, api_key, base_url)


__all__ = [
    "AIProviderFactory",
    "get_provider",
]
