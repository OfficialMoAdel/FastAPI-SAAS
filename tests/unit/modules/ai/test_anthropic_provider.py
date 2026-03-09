from unittest.mock import AsyncMock, patch

import pytest
from httpx import HTTPStatusError, Request, Response

from src.app.modules.ai.anthropic_provider import AnthropicProvider
from src.app.modules.ai.providers import AIProviderError


@pytest.fixture
def provider():
    return AnthropicProvider(api_key="sk-test", base_url="https://api.anthropic.com/v1")


@pytest.mark.asyncio
async def test_generate_text_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={
            "model": "claude-3-opus-20240229",
            "content": [{"type": "text", "text": "Hello Anthropic!"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        result = await provider.generate_text("Hi", "claude-3-opus-20240229", system_prompt="Sys")

        assert result.content == "Hello Anthropic!"
        assert result.model == "claude-3-opus-20240229"
        assert result.token_usage.total_tokens == 15

        mock_post.assert_awaited_once()
        kwargs = mock_post.call_args[1]
        assert kwargs["json"]["model"] == "claude-3-opus-20240229"
        assert kwargs["json"]["system"] == "Sys"


@pytest.mark.asyncio
async def test_generate_text_http_error(provider):
    mock_response = Response(
        status_code=400, request=Request("POST", "url"), json={"error": {"message": "Invalid auth"}}
    )  # noqa: E501
    error = HTTPStatusError("Error", request=Request("POST", "url"), response=mock_response)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_text("Hi", "claude-base")
        assert exc_info.value.error_code == "400"


@pytest.mark.asyncio
async def test_generate_json_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"content": [{"type": "text", "text": '```json\n{"key": "value"}\n```'}]},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        result = await provider.generate_json("Prompt", "claude", schema=schema)

        assert result == {"key": "value"}


@pytest.mark.asyncio
async def test_embed_text_not_supported(provider):
    with pytest.raises(AIProviderError) as exc_info:
        await provider.embed_text("Test", "model")
    assert exc_info.value.error_code == "not_supported"


@pytest.mark.asyncio
async def test_list_models_success(provider):
    models = await provider.list_models()
    assert "claude-3-opus-20240229" in models


@pytest.mark.asyncio
async def test_health_check_success(provider):
    mock_response = Response(status_code=200, request=Request("POST", "url"))
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        is_healthy = await provider.health_check()
        assert is_healthy is True
