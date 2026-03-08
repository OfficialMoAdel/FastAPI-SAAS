from unittest.mock import AsyncMock, patch

import pytest
from httpx import HTTPStatusError, Request, Response

from src.app.modules.ai.ollama_provider import OllamaProvider
from src.app.modules.ai.providers import AIProviderError


@pytest.fixture
def provider():
    return OllamaProvider(base_url="http://localhost:11434")

@pytest.mark.asyncio
async def test_generate_text_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={
            "model": "llama2",
            "message": {"role": "assistant", "content": "Hello Ollama!"},
            "done_reason": "stop",
            "prompt_eval_count": 5,
            "eval_count": 10
        }
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        result = await provider.generate_text("Hi", "llama2", system_prompt="Sys")

        assert result.content == "Hello Ollama!"
        assert result.model == "llama2"
        assert result.token_usage.total_tokens == 15

        mock_post.assert_awaited_once()
        kwargs = mock_post.call_args[1]
        assert kwargs["json"]["model"] == "llama2"
        assert kwargs["json"]["system"] == "Sys"

@pytest.mark.asyncio
async def test_generate_text_http_error(provider):
    mock_response = Response(status_code=500, request=Request("POST", "url"), json={"error": "Internal error"})
    error = HTTPStatusError("Error", request=Request("POST", "url"), response=mock_response)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_text("Hi", "llama2")
        assert exc_info.value.error_code == "500"

@pytest.mark.asyncio
async def test_generate_json_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={
            "message": {"content": '```json\n{"key": "value"}\n```'}
        }
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        result = await provider.generate_json("Prompt", "llama2", schema=schema)

        assert result == {"key": "value"}

@pytest.mark.asyncio
async def test_embed_text_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={
            "embedding": [0.1, 0.2, 0.3],
            "model": "llama2"
        }
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await provider.embed_text("Test", "llama2")

        assert result.embedding == [0.1, 0.2, 0.3]

@pytest.mark.asyncio
async def test_list_models_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("GET", "url"),
        json={"models": [{"name": "llama2"}, {"name": "mistral"}]}
    )
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        models = await provider.list_models()
        assert models == ["llama2", "mistral"]

@pytest.mark.asyncio
async def test_health_check_success(provider):
    mock_response = Response(status_code=200, request=Request("GET", "url"))
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        is_healthy = await provider.health_check()
        assert is_healthy is True
