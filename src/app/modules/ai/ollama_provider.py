"""Ollama provider implementation for AIProvider interface.

Implements the AIProvider interface for Ollama's local models.
"""

from collections.abc import AsyncGenerator
from typing import Any

from httpx import AsyncClient, Limits

from src.app.core.json_utils import json_loads
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

__all__ = ["OllamaProvider", "validate"]

logger = get_logger(__name__)


class OllamaProvider(AIProvider):
    """Ollama provider implementation.

    Supports local LLM models via Ollama API.
    """
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: float | None = None,
    ):
        """Initialize Ollama provider.

        Args:
            base_url: Ollama API base URL (default: http://localhost:11434)
            timeout: Request timeout in seconds (default: settings.ai.OLLAMA_TIMEOUT)
        """
        from src.app.core.config import settings

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout if timeout is not None else settings.ai.OLLAMA_TIMEOUT
        self._client: AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        return "ollama"

    def _get_client(self) -> AsyncClient:
        """Get or create HTTP client with tuned connection pool.

        Pool limits (OLLAMA_MAX_CONNECTIONS / OLLAMA_MAX_KEEPALIVE /
        OLLAMA_KEEPALIVE_EXPIRY) prevent unbounded connection growth under
        concurrent chat/stream load.
        """
        if self._client is None or self._client.is_closed:
            from src.app.core.config import settings

            self._client = AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                limits=Limits(
                    max_connections=settings.ai.OLLAMA_MAX_CONNECTIONS,
                    max_keepalive_connections=settings.ai.OLLAMA_MAX_KEEPALIVE,
                    keepalive_expiry=settings.ai.OLLAMA_KEEPALIVE_EXPIRY,
                ),
            )
        return self._client

    def _parse_chat_response(self, data: dict[str, Any]) -> ChatCompletionResponse:
        """Parse Ollama chat completion response."""
        message = data.get("message", {})
        usage = data.get("prompt_eval_count", 0), data.get("eval_count", 0)

        return ChatCompletionResponse(
            content=message.get("content", ""),
            model=data.get("model", ""),
            finish_reason=data.get("done_reason"),
            token_usage=(
                AITokenUsage(
                    prompt_tokens=usage[0],
                    completion_tokens=usage[1],
                    total_tokens=usage[0] + usage[1],
                )
                if usage[0] or usage[1]
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
        """Generate text completion using Ollama Chat API.

        Args:
            messages: Full conversation as typed ChatMessage objects (P2-11)
            model: Model name (e.g., "llama2", "mistral")
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate (num_predict in Ollama)
            system_prompt: Optional system prompt
            **kwargs: Additional Ollama-specific parameters

        Returns:
            ChatCompletionResponse with generated text

        Raises:
            AIProviderError: If generation fails
        """
        options = {
            "temperature": temperature,
            **kwargs,
        }
        chat_messages = [m.model_dump() for m in messages]
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        payload = {
            "model": model,
            "messages": chat_messages,
            "stream": False,
            "options": options,
        }

        if system_prompt:
            payload["system"] = system_prompt

        try:
            client = self._get_client()
            response = await client.post("/api/chat", json=payload)
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
    ) -> AsyncGenerator[str]:
        """Generate text completion with streaming.

        Yields chunks of generated text as they become available.

        Args:
            messages: Full conversation as typed ChatMessage objects (P2-11)
            model: Model name
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            system_prompt: Optional system prompt
            **kwargs: Additional Ollama-specific parameters

        Yields:
            Chunks of generated text

        Raises:
            AIProviderError: If generation fails
        """
        options = {
            "temperature": temperature,
            **kwargs,
        }
        chat_messages = [m.model_dump() for m in messages]
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        payload = {
            "model": model,
            "messages": chat_messages,
            "stream": True,
            "options": options,
        }

        if system_prompt:
            payload["system"] = system_prompt

        try:
            client = self._get_client()
            async with client.stream("POST", "/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json_loads(line)
                        message = data.get("message", {})
                        content = message.get("content", "")
                        if content:
                            yield content

                        if data.get("done", False):
                            break
                    except ValueError:
                        # orjson.JSONDecodeError subclasses ValueError
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

        Args:
            text: The text to embed
            model: Embedding model name
            **kwargs: Additional Ollama-specific parameters

        Returns:
            EmbeddingResponse with vector and metadata

        Raises:
            AIProviderError: If embedding generation fails
        """
        payload = {
            "model": model,
            "input": text,
            **kwargs,
        }

        try:
            client = self._get_client()
            response = await client.post("/api/embed", json=payload)
            response.raise_for_status()
            data = response.json()

            # S-30 FIX: the request already uses the /api/embed shape (`input`,
            # singular text in, one vector out), but the RESPONSE was read with
            # the OLD /api/embeddings key `embedding`. /api/embed returns
            # PLURAL `embeddings`, so `data.get("embedding", [])` silently
            # produced an EMPTY vector rather than raising — embeddings
            # degraded to all-zeros with no error anywhere.
            #
            # Accept the plural key, and fall back to the singular only if the
            # plural is absent, so an older/vendored Ollama still works.
            embeddings = data.get("embeddings")
            if embeddings is None:
                embeddings = data.get("embedding")
            if not embeddings:
                raise AIProviderError(
                    f"Ollama /api/embed returned no embeddings for model {model!r} "
                    f"(response keys: {sorted(data)})",
                    "empty_embedding",
                    provider="ollama",
                )
            # Batch shape: we sent ONE input, so take the first vector. Ollama
            # may nest it as [[v]] or [[[v]]] depending on version, so unwrap
            # until the leading element is a scalar rather than assuming one
            # level.
            while isinstance(embeddings, list) and embeddings and isinstance(embeddings[0], list):
                embeddings = embeddings[0]

            return EmbeddingResponse(
                embedding=list(embeddings),
                model=data.get("model", model),
                token_count=None,
            )
        except Exception as e:
            raise self._map_error(e) from e
    async def list_models(self) -> list[str]:
        """List available models for this provider.

        Returns:
            List of model names

        Raises:
            AIProviderError: If listing fails
        """
        try:
            client = self._get_client()
            response = await client.get("/api/tags", timeout=5.0)
            response.raise_for_status()
            data = response.json()

            models = [m.get("name") for m in data.get("models", [])]
            return models
        except Exception as e:
            raise self._map_error(e) from e
    async def health_check(self) -> bool:
        """Check if the provider is accessible.

        Returns:
            True if provider is healthy, False otherwise
        """
        try:
            client = self._get_client()
            response = await client.get("/api/tags", timeout=5.0)
            return response.status_code == 200
        except Exception as e:
            logger.error("Ollama health check failed", error=e)
            return False

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
