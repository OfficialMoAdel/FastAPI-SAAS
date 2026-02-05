"""AI provider interface for multi-provider AI support.

Defines the abstract interface that all AI providers must implement,
allowing easy swapping between OpenAI, Anthropic, Ollama, and other providers.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AIProviderError(Exception):
    """Base exception for AI provider errors."""

    def __init__(self, message: str, error_code: str, provider: str | None = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.provider = provider


class AITokenUsage(BaseModel):
    """Token usage information from AI provider."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatMessage(BaseModel):
    """Message format for chat completions."""

    role: str
    content: str


class ChatCompletionResponse(BaseModel):
    """Response from chat completion."""

    content: str
    model: str
    finish_reason: str | None = None
    token_usage: AITokenUsage | None = None


class EmbeddingResponse(BaseModel):
    """Response from text embedding."""

    embedding: list[float]
    model: str
    token_count: int | None = None


class AIProvider(ABC):
    """Abstract base class for AI providers.

    All AI provider implementations must inherit from this class
    and implement the required methods.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the name of the provider (e.g., 'openai', 'anthropic', 'ollama')."""
        pass

    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ChatCompletionResponse:
        """Generate text completion.

        Args:
            prompt: The user prompt
            model: The model name to use
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            system_prompt: Optional system prompt
            **kwargs: Additional provider-specific parameters

        Returns:
            ChatCompletionResponse with generated text and metadata

        Raises:
            AIProviderError: If generation fails
        """
        pass

    @abstractmethod
    def generate_text_stream(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Generate text completion with streaming.

        Yields chunks of generated text as they become available.

        Args:
            prompt: The user prompt
            model: The model name to use
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            system_prompt: Optional system prompt
            **kwargs: Additional provider-specific parameters

        Yields:
            Chunks of generated text

        Raises:
            AIProviderError: If generation fails
        """
        pass

    @abstractmethod
    async def generate_json(
        self,
        prompt: str,
        model: str,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate structured JSON output.

        Args:
            prompt: The user prompt
            model: The model name to use
            schema: Optional JSON schema for validation
            temperature: Sampling temperature (lower is better for JSON)
            max_tokens: Maximum tokens to generate
            system_prompt: Optional system prompt
            **kwargs: Additional provider-specific parameters

        Returns:
            Parsed JSON as dictionary

        Raises:
            AIProviderError: If generation or JSON parsing fails
        """
        pass

    @abstractmethod
    async def embed_text(
        self,
        text: str,
        model: str,
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Generate embedding vector for text.

        Args:
            text: The text to embed
            model: The embedding model to use
            **kwargs: Additional provider-specific parameters

        Returns:
            EmbeddingResponse with vector and metadata

        Raises:
            AIProviderError: If embedding generation fails
        """
        pass

    @abstractmethod
    async def list_models(self) -> list[str]:
        """List available models for this provider.

        Returns:
            List of model names

        Raises:
            AIProviderError: If listing fails
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is accessible.

        Returns:
            True if provider is healthy, False otherwise
        """
        pass

    async def close(self) -> None:
        """Close any resources (connections, etc.).

        This method should be called when the provider is no longer needed.
        """
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(provider='{self.provider_name}')"
