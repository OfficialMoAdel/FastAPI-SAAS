"""Ollama client for AI chat completions.

Provides async interface to Ollama API for chat completions with streaming support.
"""

import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from pydantic import BaseModel

from src.app.core.config import settings

logger = logging.getLogger(__name__)


class OllamaMessage(BaseModel):
    """Message format for Ollama API."""

    role: str
    content: str


class OllamaResponse(BaseModel):
    """Response from Ollama chat API."""

    model: str
    message: OllamaMessage
    done: bool
    total_duration: int | None = None
    eval_count: int | None = None  # Token count


class OllamaStreamChunk(BaseModel):
    """Streaming chunk from Ollama API."""

    model: str
    message: OllamaMessage
    done: bool
    eval_count: int | None = None


class OllamaClient:
    """Async client for Ollama API."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = base_url or settings.OLLAMA_BASE_URL
        self.timeout = timeout or settings.OLLAMA_TIMEOUT
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client with connection limits."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                limits=httpx.Limits(
                    max_connections=settings.OLLAMA_MAX_CONNECTIONS,
                    max_keepalive_connections=settings.OLLAMA_MAX_KEEPALIVE,
                    keepalive_expiry=settings.OLLAMA_KEEPALIVE_EXPIRY,
                ),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        """Send a chat completion request to Ollama.

        Args:
            model: The model name (e.g., "gemma3", "llama3")
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            system_prompt: Optional system prompt to prepend

        Returns:
            Dict with 'message', 'model', 'token_count', 'finish_reason'
        """
        client = await self._get_client()

        # Prepare messages with optional system prompt
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        # Build request options
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        payload = {
            "model": model,
            "messages": all_messages,
            "stream": False,
            "options": options,
        }

        try:
            response = await client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()

            return {
                "message": data.get("message", {}).get("content", ""),
                "model": data.get("model", model),
                "token_count": data.get("eval_count"),
                "finish_reason": "stop" if data.get("done") else None,
            }

        except httpx.TimeoutException as e:
            logger.error(f"Ollama timeout: {e}")
            raise OllamaError("Request timed out", "timeout") from e
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error: {e.response.status_code} - {e.response.text}")
            raise OllamaError(
                f"HTTP {e.response.status_code}: {e.response.text}",
                "http_error",
            ) from e
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            raise OllamaError(str(e), "unknown") from e

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream a chat completion from Ollama.

        Yields dicts with 'content', 'done', and optionally 'token_count'.
        """
        client = await self._get_client()

        # Prepare messages with optional system prompt
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        # Build request options
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        payload = {
            "model": model,
            "messages": all_messages,
            "stream": True,
            "options": options,
        }

        try:
            async with client.stream("POST", "/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        import json

                        chunk = json.loads(line)
                        yield {
                            "content": chunk.get("message", {}).get("content", ""),
                            "done": chunk.get("done", False),
                            "token_count": chunk.get("eval_count"),
                        }

        except httpx.TimeoutException as e:
            logger.error(f"Ollama stream timeout: {e}")
            raise OllamaError("Stream timed out", "timeout") from e
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama stream HTTP error: {e.response.status_code}")
            raise OllamaError(
                f"HTTP {e.response.status_code}",
                "http_error",
            ) from e
        except Exception as e:
            logger.error(f"Ollama stream error: {e}")
            raise OllamaError(str(e), "unknown") from e

    async def list_models(self) -> list[str]:
        """List available models in Ollama."""
        client = await self._get_client()

        try:
            response = await client.get("/api/tags")
            response.raise_for_status()
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            logger.error(f"Failed to list models: {e}")
            return []

    async def health_check(self) -> bool:
        """Check if Ollama is reachable."""
        client = await self._get_client()

        try:
            response = await client.get("/")
            return response.status_code == 200
        except Exception:
            return False


class OllamaError(Exception):
    """Exception raised for Ollama API errors."""

    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


# Singleton instance
_ollama_client: OllamaClient | None = None


def get_ollama_client() -> OllamaClient:
    """Get or create the Ollama client singleton."""
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = OllamaClient()
    return _ollama_client
