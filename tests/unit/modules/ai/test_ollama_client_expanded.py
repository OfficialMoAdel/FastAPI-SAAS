from unittest.mock import AsyncMock, patch

import pytest
from httpx import HTTPStatusError, Request, Response, TimeoutException

from src.app.modules.ai.ollama_client import OllamaClient, OllamaError


@pytest.fixture
def client():
    return OllamaClient(base_url="http://localhost:11434")

@pytest.mark.asyncio
async def test_chat_success(client):
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={
            "model": "llama3",
            "message": {"role": "assistant", "content": "Hi!"},
            "done": True,
            "eval_count": 10
        }
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await client.chat("llama3", [{"role": "user", "content": "Hello"}], system_prompt="Sys")

        assert result["message"] == "Hi!"
        assert result["model"] == "llama3"
        assert result["token_count"] == 10
        assert result["finish_reason"] == "stop"

@pytest.mark.asyncio
async def test_chat_timeout(client):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=TimeoutException("Timeout")):
        with pytest.raises(OllamaError) as exc_info:
            await client.chat("llama3", [{"role": "user", "content": "Hello"}])
        assert exc_info.value.error_code == "timeout"

@pytest.mark.asyncio
async def test_chat_http_error(client):
    mock_response = Response(status_code=500, request=Request("POST", "url"), text="Internal Sever Error")
    error = HTTPStatusError("Error", request=Request("POST", "url"), response=mock_response)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(OllamaError) as exc_info:
            await client.chat("llama3", [{"role": "user", "content": "Hello"}])
        assert exc_info.value.error_code == "http_error"

@pytest.mark.asyncio
async def test_list_models_success(client):
    mock_response = Response(
        status_code=200,
        request=Request("GET", "url"),
        json={"models": [{"name": "llama3"}, {"name": "gemma"}]}
    )
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        models = await client.list_models()
        assert models == ["llama3", "gemma"]

@pytest.mark.asyncio
async def test_list_models_failure(client):
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=Exception("Error")):
        models = await client.list_models()
        assert models == []

@pytest.mark.asyncio
async def test_health_check_success(client):
    mock_response = Response(status_code=200, request=Request("GET", "url"))
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        is_healthy = await client.health_check()
        assert is_healthy is True

@pytest.mark.asyncio
async def test_health_check_failure(client):
    mock_response = Response(status_code=500, request=Request("GET", "url"))
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        is_healthy = await client.health_check()
        assert is_healthy is False
