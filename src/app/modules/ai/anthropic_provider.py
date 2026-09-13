"""Anthropic provider implementation for AIProvider interface.

Implements the AIProvider interface for Anthropic's Claude models.
"""

import json
from collections.abc import AsyncGenerator
from typing import Any

from httpx import AsyncClient

from src.app.core.logger import get_logger
from src.app.modules.ai.providers import (
    AIProvider,
    AIProviderError,
    AITokenUsage,
    ChatCompletionResponse,
    ChatMessage,
    EmbeddingResponse,
    validate,
)

__all__ = ["AnthropicProvider", "validate"]

logger = get_logger(__name__)


class AnthropicProvider(AIProvider):
    """Anthropic provider implementation.

    Supports Claude models for text generation and streaming.
    Note: Anthropic does not currently provide embeddings API.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 60.0,
    ):
        """Initialize Anthropic provider.

        Args:
            api_key: Anthropic API key
            base_url: API base URL (default: https://api.anthropic.com/v1)
            timeout: Request timeout in seconds
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _get_client(self) -> AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = AsyncClient(
                base_url=self.base_url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                timeout=self.timeout,
            )
        return self._client

    def _parse_chat_response(self, data: dict[str, Any]) -> ChatCompletionResponse:
        """Parse Anthropic chat completion response."""
        usage = data.get("usage", {})

        # Anthropic returns content in a different format
        content_blocks = data.get("content", [])
        text_content = ""
        for block in content_blocks:
            if block.get("type") == "text":
                text_content += block.get("text", "")

        return ChatCompletionResponse(
            content=text_content,
            model=data.get("model", ""),
            finish_reason=data.get("stop_reason"),
            token_usage=(
                AITokenUsage(
                    prompt_tokens=usage.get("input_tokens", 0),
                    completion_tokens=usage.get("output_tokens", 0),
                    total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                )
                if usage
                else None
            ),
        )

    async def generate_text(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ChatCompletionResponse:
        """Generate text completion using Anthropic Messages API.

        Args:
            messages: Full conversation as typed ChatMessage objects (P2-11)
            model: Model name (e.g., "claude-3-opus-20240229", "claude-3-sonnet-20240229")
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate (required by Anthropic)
            system_prompt: Optional system prompt
            **kwargs: Additional Anthropic-specific parameters

        Returns:
            ChatCompletionResponse with generated text

        Raises:
            AIProviderError: If generation fails
        """
        chat_messages = [m.model_dump() for m in messages]
        # Anthropic requires max_tokens
        if max_tokens is None:
            max_tokens = 4096

        payload = {
            "model": model,
            "messages": chat_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }

        if system_prompt:
            payload["system"] = system_prompt

        try:
            client = self._get_client()
            response = await client.post("/messages", json=payload)
            response.raise_for_status()
            data = response.json()
            return self._parse_chat_response(data)
        except Exception as e:
            raise self._map_error(e) from e
    async def generate_text_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Generate text completion with streaming.

        Yields chunks of generated text as they become available.

        Args:
            messages: Full conversation as typed ChatMessage objects (P2-11)
            model: Model name
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate (required by Anthropic)
            system_prompt: Optional system prompt
            **kwargs: Additional Anthropic-specific parameters

        Yields:
            Chunks of generated text

        Raises:
            AIProviderError: If generation fails
        """
        chat_messages = [m.model_dump() for m in messages]
        if max_tokens is None:
            max_tokens = 4096

        payload = {
            "model": model,
            "messages": chat_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            **kwargs,
        }

        if system_prompt:
            payload["system"] = system_prompt

        try:
            client = self._get_client()
            async with client.stream("POST", "/messages", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            event_type = data.get("type")

                            if event_type == "content_block_delta":
                                delta = data.get("delta", {})
                                content = delta.get("text", "")
                                if content:
                                    yield content
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            raise self._map_error(e) from e

    async def embed_text(
        self,
        text: str,
        model: str,
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Generate embedding vector for text.

        Note: Anthropic does not currently provide an embeddings API.
        This method will always raise an error.

        Args:
            text: The text to embed
            model: Embedding model name (ignored)
            **kwargs: Additional parameters (ignored)

        Returns:
            EmbeddingResponse with vector and metadata

        Raises:
            AIProviderError: Always - Anthropic does not support embeddings
        """
        raise AIProviderError(
            "Anthropic does not currently provide an embeddings API",
            error_code="not_supported",
            provider=self.provider_name,
        )

    async def list_models(self) -> list[str]:
        """List available models for this provider.

        Returns:
            List of model names

        Raises:
            AIProviderError: If listing fails
        """
        # Anthropic doesn't have a public models endpoint
        # Return known Claude models
        return [
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
            "claude-2.1",
            "claude-2.0",
            "claude-instant-1.2",
        ]

    async def health_check(self) -> bool:
        """Check if the provider is accessible.

        Returns:
            True if provider is healthy, False otherwise
        """
        try:
            client = self._get_client()
            # Send a minimal message to test connectivity
            response = await client.post(
                "/messages",
                json={
                    "model": "claude-3-haiku-20240307",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 10,
                },
                timeout=5.0,
            )
            return response.status_code == 200
        except Exception as e:
            logger.error("Anthropic health check failed", error=e)
            return False

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
