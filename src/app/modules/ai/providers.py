"""AI provider interface for multi-provider AI support.

Defines the abstract interface that all AI providers must implement,
allowing easy swapping between OpenAI, Anthropic, Ollama, and other providers.
"""

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from httpx import HTTPStatusError, RequestError
from pydantic import BaseModel

try:
    from jsonschema import validate
except ImportError:
    validate = None

from src.app.core.logger import get_logger
from src.app.shared.error_codes import ErrorCode
from src.app.shared.exceptions import AppException

logger = get_logger(__name__)


class AIProviderError(AppException):
    """Base exception for AI provider errors.

    Root of the 12-class AI error tree in ``unified_errors``: every
    subclass inherits this one HTTP presentation, so a single declaration
    here replaced the single core-table row that used to cover the whole
    tree via Starlette's MRO walk.
    """

    http_status = 502
    response_code = ErrorCode.BAD_GATEWAY
    log_message = "AI provider error"
    log_error = True

    def __init__(
        self,
        message: str,
        error_code: str,
        provider: str | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.detail = message
        self.error_code = error_code
        self.provider = provider
        # P2-6: server-directed wait (parsed `Retry-After` on 429), honored
        # by with_retry(retry_after_attr="retry_after"). None = use backoff.
        self.retry_after = retry_after

    def http_details(self) -> dict[str, Any]:
        """Envelope ``details``: the provider and its own error code.

        ``error_code`` here is the *provider's* code (e.g. OpenAI's
        ``rate_limit_exceeded``), which is why the envelope-level code is
        named ``response_code`` on ``AppException`` — the two must not be
        conflated.

        ``getattr`` with a default rather than direct access: a subclass is
        free to skip ``super().__init__`` and this must not turn a 502 into
        a 500.
        """
        return {
            "provider": getattr(self, "provider", ""),
            "error_code": getattr(self, "error_code", ""),
        }


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
        messages: list["ChatMessage"],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ChatCompletionResponse:
        """Generate text completion.

        Args:
            messages: Full conversation as typed ChatMessage objects
                (P2-11: ChatMessage is THE boundary param — the vestigial
                prompt/messages-in-kwargs seam is gone)
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
        messages: list["ChatMessage"],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str]:
        """Generate text completion with streaming.

        Yields chunks of generated text as they become available.

        Args:
            messages: Full conversation as typed ChatMessage objects
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

    def _map_error(self, error: Exception, operation: str = "") -> AIProviderError:
        """Centralized error mapping for AI provider errors.

        Maps httpx HTTPStatusError, RequestError, and general exceptions to
        appropriate AIProviderError instances with proper error codes and provider context.
        """
        if isinstance(error, AIProviderError):
            if not error.provider:
                error.provider = self.provider_name
            return error

        name = self.provider_name.lower()
        if name == "openai":
            provider_title = "OpenAI"
        elif name == "anthropic":
            provider_title = "Anthropic"
        elif name == "ollama":
            provider_title = "Ollama"
        else:
            provider_title = self.provider_name.capitalize()

        if isinstance(error, HTTPStatusError):
            try:
                err_data = error.response.json()
                if isinstance(err_data, dict):
                    err_val = err_data.get("error")
                    if isinstance(err_val, dict):
                        error_msg = err_val.get("message", str(error))
                    elif isinstance(err_val, str):
                        error_msg = err_val
                    else:
                        error_msg = str(err_data.get("detail", error))
                else:
                    error_msg = str(err_data)
            except Exception:
                error_msg = str(error)

            status_code = str(error.response.status_code)
            logger.error(f"{provider_title} API error:", error_msg=error_msg)
            # P2-6: surface `Retry-After` on 429 so the retry layer can honor
            # it. Only delta-seconds parse; HTTP-date form falls back to None
            # (computed backoff). Capped by with_retry, not here.
            retry_after: float | None = None
            if error.response.status_code == 429:
                raw_retry_after = error.response.headers.get("retry-after")
                if raw_retry_after is not None:
                    try:
                        parsed = float(raw_retry_after)
                        retry_after = parsed if parsed > 0 else None
                    except (TypeError, ValueError):
                        retry_after = None
            return AIProviderError(
                f"{provider_title} API error: {error_msg}",
                error_code=status_code,
                provider=self.provider_name,
                retry_after=retry_after,
            )

        if isinstance(error, RequestError):
            logger.error(f"{provider_title} request error", error=error)
            return AIProviderError(
                f"{provider_title} request error: {error}",
                error_code="request_error",
                provider=self.provider_name,
            )

        logger.error(f"{provider_title} error", error=error)
        return AIProviderError(
            f"{provider_title} error: {error}",
            error_code="provider_error",
            provider=self.provider_name,
        )

    def _extract_json(self, content: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        """Extract and validate JSON from model output text.

        Handles markdown code block stripping and jsonschema validation.
        """
        stripped = content.strip()
        if stripped.startswith("```json"):
            stripped = stripped[7:]
        if stripped.startswith("```"):
            stripped = stripped[3:]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        try:
            parsed: dict[str, Any] = json.loads(stripped.strip())
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse JSON from {self.provider_name} response:", content=content)
            raise AIProviderError(
                f"Failed to parse JSON: {e}",
                error_code="json_parse_error",
                provider=self.provider_name,
            ) from e

        if schema:
            import sys

            mod = sys.modules.get(self.__class__.__module__)
            val_fn = getattr(mod, "validate", None) if mod else None
            if val_fn is None:
                val_fn = validate

            if val_fn is not None:
                try:
                    val_fn(instance=parsed, schema=schema)
                except AIProviderError:
                    raise
                except Exception as val_err:
                    logger.error("JSON schema validation failed", error=str(val_err))
                    raise AIProviderError(
                        f"JSON schema validation failed: {val_err}",
                        error_code="json_validation_error",
                        provider=self.provider_name,
                    ) from val_err
            else:
                logger.warning("jsonschema not installed, skipping schema validation")

        return parsed
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
        """Generate structured JSON output using default template method.

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
        json_system = (
            system_prompt or ""
        ) + "\n\nYou must respond with valid JSON only. Do not include any other text."
        if schema:
            json_system += f"\n\nJSON Schema:\n{json.dumps(schema, indent=2)}"

        # P2-11: ChatMessage is THE provider-boundary param; generate_json
        # stays a prompt-taking convenience that wraps into a typed message.
        response = await self.generate_text(
            messages=[ChatMessage(role="user", content=prompt)],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=json_system,
            **kwargs,
        )

        return self._extract_json(response.content, schema=schema)
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
