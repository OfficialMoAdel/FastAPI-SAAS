"""AI Provider Factory for managing multiple AI providers.

Provides a unified interface for accessing different AI providers
(OpenAI, Anthropic, Ollama) with caching and configuration management.
"""

import hashlib
from typing import Any

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.modules.ai.anthropic_provider import AnthropicProvider
from src.app.modules.ai.ollama_provider import OllamaProvider
from src.app.modules.ai.openai_provider import OpenAIProvider
from src.app.modules.ai.providers import AIProvider
from src.app.modules.ai.unified_errors import (
    AIServerError,
)

logger = get_logger(__name__)

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
                          There is no AI_PROVIDER setting: the factory falls
                          back to DEFAULT_PROVIDER, so the provider is chosen
                          by passing provider_name.
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
            # No `AI_PROVIDER` setting exists in any config component, so this
            # is unconditionally DEFAULT_PROVIDER ("ollama"). It used to be
            # spelled `getattr(settings, "AI_PROVIDER", DEFAULT_PROVIDER)`,
            # which read as though an operator could set the provider and
            # could not: the key is absent from the ORM, so the getattr always
            # took the default and an `AI_PROVIDER=openai` in the environment
            # was silently ignored. Spelled as a constant rather than a
            # getattr so the absence is visible at the call site.
            provider_name = DEFAULT_PROVIDER

        if provider_name not in SUPPORTED_PROVIDERS:
            raise AIServerError(
                provider=provider_name,
                message=f"Unsupported AI provider: {provider_name}",
            )

        provider_class = SUPPORTED_PROVIDERS[provider_name]

        # Generate cache key that includes credentials
        cache_key = cls._get_cache_key(provider_name, api_key, base_url)

        if cache_key in cls._instances:
            logger.debug("Returning cached provider instance:", provider_name=provider_name)
            return cls._instances[cache_key]

        logger.info("Creating new provider instance:", provider_name=provider_name)

        try:
            provider = cls._create_provider(provider_class, provider_name, api_key, base_url)  # type: ignore[arg-type]
            cls._instances[cache_key] = provider
            return provider
        except Exception as e:
            logger.error("Failed to create provider", provider_name=provider_name, error=e)
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
            # S-31: namespaced read (the flat delegation only maps DECLARED
            # nested fields, and these were previously undeclared, so the
            # lookup silently returned None for every OpenAI/Anthropic call).
            # SecretStr is unwrapped because the provider takes a plain str.
            _cfg = getattr(settings.ai, "OPENAI_API_KEY", None)
            resolved_api_key = api_key or (_cfg.get_secret_value() if _cfg else None)
            if not resolved_api_key:
                raise AIServerError(
                    provider=provider_name,
                    message="Missing API key for OpenAI. Set OPENAI_API_KEY environment variable.",
                )
            provider_config["api_key"] = resolved_api_key
            resolved_base_url = base_url or getattr(settings.ai, "OPENAI_BASE_URL", None)
            if resolved_base_url:
                provider_config["base_url"] = resolved_base_url
        elif provider_name == "anthropic":
            _cfg = getattr(settings.ai, "ANTHROPIC_API_KEY", None)
            resolved_api_key = api_key or (_cfg.get_secret_value() if _cfg else None)
            if not resolved_api_key:
                raise AIServerError(
                    provider=provider_name,
                    message="Missing API key for Anthropic. Set ANTHROPIC_API_KEY environment variable.",
                )
            provider_config["api_key"] = resolved_api_key
            resolved_base_url = base_url or getattr(settings.ai, "ANTHROPIC_BASE_URL", None)
            if resolved_base_url:
                provider_config["base_url"] = resolved_base_url
        elif provider_name == "ollama":
            provider_config["base_url"] = base_url or settings.ai.OLLAMA_BASE_URL

        provider_instance = provider_class(**provider_config)
        assert isinstance(provider_instance, AIProvider)
        return provider_instance

    @classmethod
    async def shutdown_all(cls) -> None:
        """Close all cached provider instances and their connections."""
        import asyncio

        for provider in list(cls._instances.values()):
            if hasattr(provider, "close"):
                if asyncio.iscoroutinefunction(provider.close):
                    await provider.close()
                else:
                    res = provider.close()  # type: ignore[func-returns-value]
                    if asyncio.iscoroutine(res):
                        await res
        cls.clear_cache()
        logger.info("Shutdown all AI provider instances")

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
