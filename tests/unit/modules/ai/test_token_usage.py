import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.ai.token_usage import AITokenUsageService, log_token_usage


def test_calculate_cost() -> None:
    # Test openai gpt-4
    cost = AITokenUsageService._calculate_cost("openai", "gpt-4", 1000, 500)
    expected_cost = (1000 / 1_000_000) * 30.0 + (500 / 1_000_000) * 60.0
    assert abs(cost - expected_cost) < 1e-6

    # Test unknown provider defaults to ollama
    cost = AITokenUsageService._calculate_cost("unknown", "model", 1000, 500)
    assert cost == 0.0

    # Test unknown model defaults to default
    cost = AITokenUsageService._calculate_cost("openai", "unknown-model", 1000, 500)
    expected_cost = (1000 / 1_000_000) * 0.5 + (500 / 1_000_000) * 1.5
    assert abs(cost - expected_cost) < 1e-6


@patch("src.app.modules.ai.token_usage.logger")
def test_log_token_usage_method(mock_logger) -> None:
    tenant_id = uuid.uuid4()
    AITokenUsageService.log_token_usage(
        provider="anthropic",
        model="claude-3-haiku",
        prompt_tokens=1000,
        completion_tokens=2000,
        user_id=1,
        tenant_id=tenant_id,
        session_id=123,
        metadata={"test": "data"},
    )

    # Ensure logger was called
    assert mock_logger.info.called
    kwargs = mock_logger.info.call_args.kwargs
    assert "extra" in kwargs
    extra = kwargs["extra"]
    assert extra["provider"] == "anthropic"
    assert extra["model"] == "claude-3-haiku"
    assert extra["total_tokens"] == 3000
    expected_cost = (1000 / 1_000_000) * 0.25 + (2000 / 1_000_000) * 1.25  # 0.00025 + 0.0025 = 0.00275
    assert abs(extra["cost_usd"] - expected_cost) < 1e-6


@patch("src.app.modules.ai.token_usage.AITokenUsageService.log_token_usage")
def test_log_token_usage_function(mock_log) -> None:
    log_token_usage("openai", "gpt-4", 10, 20)
    mock_log.assert_called_once_with(
        provider="openai",
        model="gpt-4",
        prompt_tokens=10,
        completion_tokens=20,
        user_id=None,
        tenant_id=None,
        session_id=None,
        metadata=None,
    )


@pytest.mark.asyncio
async def test_save_token_usage_success() -> None:
    mock_session = AsyncMock()
    with patch("src.app.modules.ai.models.AITokenUsage") as MockModel:
        mock_instance = MagicMock()
        mock_instance.id = 1
        MockModel.return_value = mock_instance

        await AITokenUsageService.save_token_usage(
            session=mock_session, provider="openai", model="gpt-4", prompt_tokens=100, completion_tokens=200
        )

        assert mock_session.add.called
        assert mock_session.commit.called
        assert not mock_session.rollback.called

        # Verify model initialization
        MockModel.assert_called_once()
        kwargs = MockModel.call_args.kwargs
        assert kwargs["total_tokens"] == 300
        assert kwargs["usage_metadata"] == {}


@pytest.mark.asyncio
async def test_save_token_usage_error() -> None:
    mock_session = AsyncMock()
    mock_session.add = MagicMock(side_effect=Exception("DB Error"))

    with patch("src.app.modules.ai.models.AITokenUsage"):
        await AITokenUsageService.save_token_usage(
            session=mock_session, provider="openai", model="gpt-4", prompt_tokens=100, completion_tokens=200
        )

        assert mock_session.rollback.called
