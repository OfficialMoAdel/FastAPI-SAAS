"""Ollama provider implementation for AIProvider interface.

Implements the AIProvider interface for Ollama's local models.
"""

from collections.abc import AsyncGenerator
from typing import Any

from httpx import AsyncClient, HTTPStatusError, RequestError

from src.app.core.logger import get_logger
from src.app.modules.ai.providers import (
    AIProvider,
    AIProviderError,
    AITokenUsage,
    ChatCompletionResponse,
    EmbeddingResponse,
)

logger = get_logger(__name__)


class OllamaProvider(AIProvider):
    """Ollama provider implementation.

    Supports local LLM models via Ollama API.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: float = 60.0,
    ):
        """Initialize Ollama provider.

        Args:
            base_url: Ollama API base URL (default: http://localhost:11434)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        return "ollama"

    def _get_client(self) -> AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
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
        prompt: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ChatCompletionResponse:
        """Generate text completion using Ollama Chat API.

        Args:
            prompt: The user prompt
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
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
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
        except HTTPStatusError as e:
            logger.error("Ollama API error", error=e)
            raise AIProviderError(
                f"Ollama API error: {e}",
                error_code=str(e.response.status_code),
                provider=self.provider_name,
            ) from e
        except RequestError as e:
            logger.error("Ollama request error", error=e)
            raise AIProviderError(
                f"Ollama request error: {e}",
                error_code="request_error",
                provider=self.provider_name,
            ) from e

    async def generate_text_stream(
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
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
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
                        import json

                        data = json.loads(line)
                        message = data.get("message", {})
                        content = message.get("content", "")
                        if content:
                            yield content

                        if data.get("done", False):
                            break
                    except json.JSONDecodeError:
                        continue
        except HTTPStatusError as e:
            logger.error("Ollama streaming error", error=e)
            raise AIProviderError(
                f"Ollama streaming error: {e}",
                error_code=str(e.response.status_code),
                provider=self.provider_name,
            ) from e
        except RequestError as e:
            logger.error("Ollama request error", error=e)
            raise AIProviderError(
                f"Ollama request error: {e}",
                error_code="request_error",
                provider=self.provider_name,
            ) from e

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
            model: Model name
            schema: Optional JSON schema for validation
            temperature: Sampling temperature (lower is better for JSON)
            max_tokens: Maximum tokens to generate
            system_prompt: Optional system prompt
            **kwargs: Additional Ollama-specific parameters

        Returns:
            Parsed JSON as dictionary

        Raises:
            AIProviderError: If generation or JSON parsing fails
        """
        import json

        json_system = (
            system_prompt or ""
        ) + "\n\nYou must respond with valid JSON only. Do not include any other text."
        if schema:
            json_system += f"\n\nJSON Schema:\n{json.dumps(schema, indent=2)}"

        response = await self.generate_text(
            prompt=prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=json_system,
            **kwargs,
        )

        try:
            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]

            parsed: dict[str, Any] = json.loads(content.strip())

            if schema:
                from jsonschema import validate

                validate(instance=parsed, schema=schema)

            return parsed
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON from Ollama response:", content=response.content)
            raise AIProviderError(
                f"Failed to parse JSON: {e}",
                error_code="json_parse_error",
                provider=self.provider_name,
            ) from e
        except ImportError:
            logger.warning("jsonschema not installed, skipping schema validation")
            return parsed

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

            return EmbeddingResponse(
                embedding=data.get("embedding", []),
                model=data.get("model", ""),
                token_count=None,
            )
        except HTTPStatusError as e:
            logger.error("Ollama embedding error", error=e)
            raise AIProviderError(
                f"Ollama embedding error: {e}",
                error_code=str(e.response.status_code),
                provider=self.provider_name,
            ) from e
        except RequestError as e:
            logger.error("Ollama request error", error=e)
            raise AIProviderError(
                f"Ollama request error: {e}",
                error_code="request_error",
                provider=self.provider_name,
            ) from e

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
        except HTTPStatusError as e:
            logger.error("Ollama list models error", error=e)
            raise AIProviderError(
                f"Failed to list models: {e}",
                error_code=str(e.response.status_code),
                provider=self.provider_name,
            ) from e
        except RequestError as e:
            logger.error("Ollama request error", error=e)
            raise AIProviderError(
                f"Ollama request error: {e}",
                error_code="request_error",
                provider=self.provider_name,
            ) from e

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
