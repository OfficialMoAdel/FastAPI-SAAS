import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import Response, HTTPStatusError, RequestError, Request

from src.app.modules.ai.openai_provider import OpenAIProvider
from src.app.modules.ai.providers import AIProviderError

@pytest.fixture
def provider():
    return OpenAIProvider(api_key="sk-test", base_url="https://api.openai.com/v1")

@pytest.mark.asyncio
async def test_generate_text_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={
            "id": "chatcmpl-123",
            "model": "gpt-4",
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        }
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        result = await provider.generate_text("Hi", "gpt-4", system_prompt="You are a bot.")
        
        assert result.content == "Hello!"
        assert result.model == "gpt-4"
        assert result.token_usage.total_tokens == 15
        
        mock_post.assert_awaited_once()
        kwargs = mock_post.call_args[1]
        assert kwargs["json"]["model"] == "gpt-4"
        assert kwargs["json"]["messages"][0]["role"] == "system"

@pytest.mark.asyncio
async def test_generate_text_http_error(provider):
    mock_response = Response(
        status_code=400,
        request=Request("POST", "url"),
        json={"error": {"message": "Invalid request"}}
    )
    error = HTTPStatusError("Error message", request=Request("POST", "url"), response=mock_response)
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_text("Hi", "gpt-4")
        assert "Invalid request" in str(exc_info.value)
        assert exc_info.value.error_code == "400"

@pytest.mark.asyncio
async def test_generate_json_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={
            "choices": [{"message": {"content": '```json\n{"key": "value"}\n```'}}]
        }
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        result = await provider.generate_json("Prompt", "gpt-4", schema=schema)
        
        assert result == {"key": "value"}

@pytest.mark.asyncio
async def test_embed_text_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={
            "data": [{"embedding": [0.1, 0.2, 0.3]}],
            "model": "text-embedding-ada-002",
            "usage": {"total_tokens": 5}
        }
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await provider.embed_text("Test", "text-embedding-ada-002")
        
        assert result.embedding == [0.1, 0.2, 0.3]
        assert result.token_count == 5

@pytest.mark.asyncio
async def test_list_models_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("GET", "url"),
        json={"data": [{"id": "gpt-4"}, {"id": "gpt-3.5-turbo"}]}
    )
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        models = await provider.list_models()
        assert models == ["gpt-4", "gpt-3.5-turbo"]

@pytest.mark.asyncio
async def test_health_check_success(provider):
    mock_response = Response(status_code=200, request=Request("GET", "url"))
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        is_healthy = await provider.health_check()
        assert is_healthy is True

@pytest.mark.asyncio
async def test_health_check_failure(provider):
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=Exception("Timeout")):
        is_healthy = await provider.health_check()
        assert is_healthy is False
