"""AI Token Usage Service.

Tracks and logs token usage for AI operations including:
- Prompt tokens
- Completion tokens
- Total tokens
- Cost estimation
- Usage by user/tenant
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger

logger = get_logger(__name__)


class AITokenUsageService:
    """Service for tracking AI token usage."""

    # Token costs per 1M tokens (approximate, update as needed)
    TOKEN_COSTS = {
        "openai": {
            "gpt-4": {"prompt": 30.0, "completion": 60.0},
            "gpt-4-turbo": {"prompt": 10.0, "completion": 30.0},
            "gpt-3.5-turbo": {"prompt": 0.5, "completion": 1.5},
            "default": {"prompt": 0.5, "completion": 1.5},
        },
        "anthropic": {
            "claude-3-opus": {"prompt": 15.0, "completion": 75.0},
            "claude-3-sonnet": {"prompt": 3.0, "completion": 15.0},
            "claude-3-haiku": {"prompt": 0.25, "completion": 1.25},
            "default": {"prompt": 3.0, "completion": 15.0},
        },
        "ollama": {
            "default": {"prompt": 0.0, "completion": 0.0},  # Free for local models
        },
    }

    @classmethod
    def log_token_usage(
        cls,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        user_id: str | None = None,
        tenant_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log token usage for AI operations.

        Args:
            provider: AI provider name (openai, anthropic, ollama).
            model: Model name used.
            prompt_tokens: Number of tokens in the prompt.
            completion_tokens: Number of tokens in the completion.
            user_id: Optional user ID.
            tenant_id: Optional tenant ID.
            session_id: Optional chat session ID.
            metadata: Optional additional metadata.
        """
        total_tokens = prompt_tokens + completion_tokens
        cost = cls._calculate_cost(provider, model, prompt_tokens, completion_tokens)

        usage_data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "session_id": session_id,
            "metadata": metadata or {},
        }

        logger.info(
            "AI Token Usage | provider=%s | model=%s | prompt=%d | completion=%d | total=%d | cost=$%.6f | user=%s | tenant=%s",  # noqa: E501
            provider,
            model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            cost,
            user_id,
            tenant_id,
            extra=usage_data,
        )

    @classmethod
    def _calculate_cost(cls, provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate cost for token usage.

        Args:
            provider: AI provider name.
            model: Model name.
            prompt_tokens: Number of prompt tokens.
            completion_tokens: Number of completion tokens.

        Returns:
            Cost in USD.
        """
        provider_costs = cls.TOKEN_COSTS.get(provider, cls.TOKEN_COSTS["ollama"])
        model_costs = provider_costs.get(model, provider_costs.get("default", {"prompt": 0.0, "completion": 0.0}))

        prompt_cost = (prompt_tokens / 1_000_000) * model_costs["prompt"]
        completion_cost = (completion_tokens / 1_000_000) * model_costs["completion"]

        return prompt_cost + completion_cost

    @classmethod
    async def save_token_usage(
        cls,
        session: AsyncSession,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        user_id: str | None = None,
        tenant_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Save token usage to database for analytics.

        Args:
            session: Database session.
            provider: AI provider name.
            model: Model name used.
            prompt_tokens: Number of tokens in the prompt.
            completion_tokens: Number of tokens in the completion.
            user_id: Optional user ID.
            tenant_id: Optional tenant ID.
            session_id: Optional chat session ID.
            metadata: Optional additional metadata.
        """
        try:
            from src.app.modules.ai.models import AITokenUsage

            usage_record = AITokenUsage(
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cost_usd=cls._calculate_cost(provider, model, prompt_tokens, completion_tokens),
                user_id=user_id,
                tenant_id=tenant_id,
                session_id=session_id,
                usage_metadata=metadata or {},
            )

            session.add(usage_record)
            await session.commit()

            logger.debug(f"Saved token usage record: {usage_record.id}")
        except Exception as e:
            logger.error(f"Failed to save token usage: {e}")
            await session.rollback()


def log_token_usage(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    user_id: str | None = None,
    tenant_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Convenience function to log token usage.

    Args:
        provider: AI provider name.
        model: Model name used.
        prompt_tokens: Number of tokens in the prompt.
        completion_tokens: Number of tokens in the completion.
        user_id: Optional user ID.
        tenant_id: Optional tenant ID.
        session_id: Optional chat session ID.
        metadata: Optional additional metadata.
    """
    AITokenUsageService.log_token_usage(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        user_id=user_id,
        tenant_id=tenant_id,
        session_id=session_id,
        metadata=metadata,
    )


__all__ = [
    "AITokenUsageService",
    "log_token_usage",
]
