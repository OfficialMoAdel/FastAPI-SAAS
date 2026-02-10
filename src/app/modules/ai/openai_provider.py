"""OpenAI provider implementation for AIProvider interface.

Implements the AIProvider interface for OpenAI's GPT models.
"""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from httpx import AsyncClient, HTTPStatusError, RequestError

try:
    from jsonschema import validate
except ImportError:
    validate = None

from .providers import (
    AIProvider,
    AIProviderError,
    AITokenUsage,
    ChatCompletionResponse,
    EmbeddingResponse,
)

logger = logging.getLogger(__name__)


class OpenAIProvider(AIProvider):
    """OpenAI provider implementation.

    Supports GPT models for text generation, streaming, and embeddings.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ):
        """Initialize OpenAI provider.

        Args:
            api_key: OpenAI API key
            base_url: API base URL (default: https://api.openai.com/v1)
            timeout: Request timeout in seconds
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        return "openai"

    def _get_client(self) -> AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
        return self._client

    def _parse_chat_response(self, data: dict[str, Any]) -> ChatCompletionResponse:
        """Parse OpenAI chat completion response."""
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        return ChatCompletionResponse(
            content=message.get("content", ""),
            model=data.get("model", ""),
            finish_reason=choice.get("finish_reason"),
            token_usage=(
                AITokenUsage(
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                )
                if usage
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
        """Generate text completion using OpenAI Chat API.

        Args:
            prompt: The user prompt
            model: Model name (e.g., "gpt-4", "gpt-3.5-turbo")
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            system_prompt: Optional system prompt
            **kwargs: Additional OpenAI-specific parameters

        Returns:
            ChatCompletionResponse with generated text

        Raises:
            AIProviderError: If generation fails
        """
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        try:
            client = self._get_client()
            response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            return self._parse_chat_response(data)
        except HTTPStatusError as e:
            error_msg = e.response.json().get("error", {}).get("message", str(e))
            logger.error(f"OpenAI API error: {error_msg}")
            raise AIProviderError(
                f"OpenAI API error: {error_msg}",
                error_code=str(e.response.status_code),
                provider=self.provider_name,
            ) from e
        except RequestError as e:
            logger.error(f"OpenAI request error: {e}")
            raise AIProviderError(
                f"OpenAI request error: {e}",
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
            **kwargs: Additional OpenAI-specific parameters

        Yields:
            Chunks of generated text

        Raises:
            AIProviderError: If generation fails
        """
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            **kwargs,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        try:
            client = self._get_client()
            async with client.stream("POST", "/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
        except HTTPStatusError as e:
            error_msg = e.response.json().get("error", {}).get("message", str(e))
            logger.error(f"OpenAI streaming error: {error_msg}")
            raise AIProviderError(
                f"OpenAI streaming error: {error_msg}",
                error_code=str(e.response.status_code),
                provider=self.provider_name,
            ) from e
        except RequestError as e:
            logger.error(f"OpenAI request error: {e}")
            raise AIProviderError(
                f"OpenAI request error: {e}",
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
            **kwargs: Additional OpenAI-specific parameters

        Returns:
            Parsed JSON as dictionary

        Raises:
            AIProviderError: If generation or JSON parsing fails
        """
        # Add JSON formatting instructions
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
            # Extract JSON from response (handle markdown code blocks)
            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]

            parsed: dict[str, Any] = json.loads(content.strip())

            # Validate against schema if provided
            if schema:
                if validate:
                    validate(instance=parsed, schema=schema)
                else:
                    logger.warning("jsonschema not installed, skipping schema validation")

            return parsed
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from OpenAI response: {response.content}")
            raise AIProviderError(
                f"Failed to parse JSON: {e}",
                error_code="json_parse_error",
                provider=self.provider_name,
            ) from e

    async def embed_text(
        self,
        text: str,
        model: str,
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Generate embedding vector for text.

        Args:
            text: The text to embed
            model: Embedding model (e.g., "text-embedding-ada-002")
            **kwargs: Additional OpenAI-specific parameters

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
            response = await client.post("/embeddings", json=payload)
            response.raise_for_status()
            data = response.json()

            embedding_data = data.get("data", [{}])[0]
            usage = data.get("usage", {})

            return EmbeddingResponse(
                embedding=embedding_data.get("embedding", []),
                model=data.get("model", ""),
                token_count=usage.get("total_tokens"),
            )
        except HTTPStatusError as e:
            error_msg = e.response.json().get("error", {}).get("message", str(e))
            logger.error(f"OpenAI embedding error: {error_msg}")
            raise AIProviderError(
                f"OpenAI embedding error: {error_msg}",
                error_code=str(e.response.status_code),
                provider=self.provider_name,
            ) from e
        except RequestError as e:
            logger.error(f"OpenAI request error: {e}")
            raise AIProviderError(
                f"OpenAI request error: {e}",
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
            response = await client.get("/models")
            response.raise_for_status()
            data = response.json()

            models = [m.get("id") for m in data.get("data", [])]
            return models
        except HTTPStatusError as e:
            logger.error(f"OpenAI list models error: {e}")
            raise AIProviderError(
                f"Failed to list models: {e}",
                error_code=str(e.response.status_code),
                provider=self.provider_name,
            ) from e
        except RequestError as e:
            logger.error(f"OpenAI request error: {e}")
            raise AIProviderError(
                f"OpenAI request error: {e}",
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
            response = await client.get("/models", timeout=5.0)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"OpenAI health check failed: {e}")
            return False

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
