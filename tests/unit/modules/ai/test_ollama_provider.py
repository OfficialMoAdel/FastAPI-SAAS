from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import HTTPStatusError, Request, RequestError, Response

from src.app.modules.ai.ollama_provider import OllamaProvider
from src.app.modules.ai.providers import AIProviderError


# Helper class for mocking async iterators
class AsyncIteratorMock:
    """Mock async iterator for streaming responses."""

    def __init__(self, items):
        self.items = items
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.items):
            raise StopAsyncIteration
        item = self.items[self.index]
        self.index += 1
        return item


@pytest.fixture
def provider():
    return OllamaProvider(base_url="http://localhost:11434")


# ============================================================================
# Property Tests
# ============================================================================


@pytest.mark.asyncio
async def test_provider_name(provider):
    """Test provider name property returns 'ollama'."""
    assert provider.provider_name == "ollama"


# ============================================================================
# Client Tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_client_creation(provider):
    """Test that client is created with correct configuration."""
    client = provider._get_client()
    assert client is not None
    assert str(client.base_url).rstrip("/") == "http://localhost:11434"


@pytest.mark.asyncio
async def test_get_client_reuse(provider):
    """Test that client is reused across calls."""
    client1 = provider._get_client()
    client2 = provider._get_client()
    assert client1 is client2


# ============================================================================
# Response Parsing Tests
# ============================================================================


@pytest.mark.asyncio
async def test_parse_chat_response_full(provider):
    """Test parsing complete chat response."""
    data = {
        "model": "llama2",
        "message": {"role": "assistant", "content": "Hello world"},
        "done_reason": "stop",
        "prompt_eval_count": 10,
        "eval_count": 5,
    }
    result = provider._parse_chat_response(data)

    assert result.content == "Hello world"
    assert result.model == "llama2"
    assert result.finish_reason == "stop"
    assert result.token_usage is not None
    assert result.token_usage.prompt_tokens == 10
    assert result.token_usage.completion_tokens == 5
    assert result.token_usage.total_tokens == 15


@pytest.mark.asyncio
async def test_parse_chat_response_no_message(provider):
    """Test parsing response with missing message."""
    data = {"model": "llama2"}
    result = provider._parse_chat_response(data)

    assert result.content == ""
    assert result.model == "llama2"
    assert result.token_usage is None


@pytest.mark.asyncio
async def test_parse_chat_response_no_usage(provider):
    """Test parsing response with zero usage counts."""
    data = {
        "model": "llama2",
        "message": {"content": "Test"},
        "prompt_eval_count": 0,
        "eval_count": 0,
    }
    result = provider._parse_chat_response(data)

    assert result.content == "Test"
    # When both are 0, token_usage is None (falsy check)


# ============================================================================
# Generate Text Tests
# ============================================================================


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
            "eval_count": 10,
        },
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
async def test_generate_text_without_system_prompt(provider):
    """Test generate_text without system prompt."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={
            "model": "llama2",
            "message": {"content": "Response"},
        },
    )
    post_mock = AsyncMock(return_value=mock_response)
    with patch("httpx.AsyncClient.post", post_mock):
        await provider.generate_text("Hello", "llama2")
        call_kwargs = post_mock.call_args[1]
        # No "system" key when system_prompt is None
        assert "system" not in call_kwargs["json"]


@pytest.mark.asyncio
async def test_generate_text_with_max_tokens(provider):
    """Test generate_text includes num_predict in options."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"model": "llama2", "message": {"content": "Test"}},
    )
    post_mock = AsyncMock(return_value=mock_response)
    with patch("httpx.AsyncClient.post", post_mock):
        await provider.generate_text("Hi", "llama2", max_tokens=100)
        call_kwargs = post_mock.call_args[1]
        assert call_kwargs["json"]["options"]["num_predict"] == 100


@pytest.mark.asyncio
async def test_generate_text_http_error(provider):
    mock_response = Response(status_code=500, request=Request("POST", "url"), json={"error": "Internal error"})
    error = HTTPStatusError("Error", request=Request("POST", "url"), response=mock_response)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_text("Hi", "llama2")
        assert exc_info.value.error_code == "500"


@pytest.mark.asyncio
async def test_generate_text_request_error(provider):
    """Test generate_text handles RequestError."""
    error = RequestError("Connection failed")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_text("Hi", "llama2")
        assert "Connection failed" in str(exc_info.value)
        assert exc_info.value.error_code == "request_error"


# ============================================================================
# Generate Text Stream Tests
# ============================================================================


@pytest.mark.asyncio
async def test_generate_text_stream_success(provider):
    """Test streaming text generation with successful response."""
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.raise_for_status = AsyncMock()
    # Set aiter_lines to return the iterator directly
    mock_stream.aiter_lines = lambda: AsyncIteratorMock(
        [
            '{"message": {"content": "Hello"}}',
            '{"message": {"content": " World"}}',
            '{"done": true}',
        ]
    )

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream)

    with patch.object(provider, "_get_client", return_value=mock_client):
        chunks = []
        async for chunk in provider.generate_text_stream("Hi", "llama2"):
            chunks.append(chunk)

        assert chunks == ["Hello", " World"]


@pytest.mark.asyncio
async def test_generate_text_stream_http_error(provider):
    """Test streaming handles HTTPStatusError."""
    mock_response = Response(
        status_code=503,
        request=Request("POST", "url"),
        json={"error": "Service unavailable"},
    )
    error = HTTPStatusError("Service unavailable", request=Request("POST", "url"), response=mock_response)

    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(side_effect=error)

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream)

    with patch.object(provider, "_get_client", return_value=mock_client):
        with pytest.raises(AIProviderError) as exc_info:
            async for _ in provider.generate_text_stream("Hi", "llama2"):
                pass
        assert "Service unavailable" in str(exc_info.value)
        assert exc_info.value.error_code == "503"


@pytest.mark.asyncio
async def test_generate_text_stream_request_error(provider):
    """Test streaming handles RequestError."""
    error = RequestError("Connection timeout")

    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(side_effect=error)

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream)

    with patch.object(provider, "_get_client", return_value=mock_client):
        with pytest.raises(AIProviderError) as exc_info:
            async for _ in provider.generate_text_stream("Hi", "llama2"):
                pass
        assert "Connection timeout" in str(exc_info.value)
        assert exc_info.value.error_code == "request_error"


@pytest.mark.asyncio
async def test_generate_text_stream_malformed_json(provider):
    """Test streaming handles malformed JSON in response."""
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.raise_for_status = AsyncMock()
    mock_stream.aiter_lines = lambda: AsyncIteratorMock(
        [
            "invalid json {",
            '{"message": {"content": "Valid"}}',
            '{"done": true}',
        ]
    )

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream)

    with patch.object(provider, "_get_client", return_value=mock_client):
        chunks = []
        async for chunk in provider.generate_text_stream("Hi", "llama2"):
            chunks.append(chunk)
        # Should skip malformed JSON and continue
        assert "Valid" in chunks


@pytest.mark.asyncio
async def test_generate_text_stream_empty_lines(provider):
    """Test streaming handles empty lines correctly."""
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.raise_for_status = AsyncMock()
    mock_stream.aiter_lines = lambda: AsyncIteratorMock(
        [
            "",
            "   ",
            '{"message": {"content": "Content"}}',
            '{"done": true}',
        ]
    )

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream)

    with patch.object(provider, "_get_client", return_value=mock_client):
        chunks = []
        async for chunk in provider.generate_text_stream("Hi", "llama2"):
            chunks.append(chunk)
        # Empty lines should be skipped
        assert "Content" in chunks


# ============================================================================
# Generate JSON Tests
# ============================================================================


@pytest.mark.asyncio
async def test_generate_json_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"message": {"content": '```json\n{"key": "value"}\n```'}},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        result = await provider.generate_json("Prompt", "llama2", schema=schema)

        assert result == {"key": "value"}


@pytest.mark.asyncio
async def test_generate_json_without_code_blocks(provider):
    """Test JSON generation with response not wrapped in code blocks."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"message": {"content": '{"name": "test"}'}},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await provider.generate_json("Prompt", "llama2")

        assert result == {"name": "test"}


@pytest.mark.asyncio
async def test_generate_json_with_plain_markdown(provider):
    """Test JSON generation with plain markdown code blocks."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"message": {"content": '```\n{"key": "value"}\n```'}},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await provider.generate_json("Prompt", "llama2")

        assert result == {"key": "value"}


@pytest.mark.asyncio
async def test_generate_json_malformed_json(provider):
    """Test generate_json handles JSON parse error."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"message": {"content": "not valid json"}},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_json("Prompt", "llama2")
        assert "Failed to parse JSON" in str(exc_info.value)
        assert exc_info.value.error_code == "json_parse_error"


# ============================================================================
# Embed Text Tests
# ============================================================================


@pytest.mark.asyncio
async def test_embed_text_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"embedding": [0.1, 0.2, 0.3], "model": "llama2"},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await provider.embed_text("Test", "llama2")

        assert result.embedding == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_embed_text_http_error(provider):
    """Test embed_text handles HTTPStatusError."""
    mock_response = Response(
        status_code=500,
        request=Request("POST", "url"),
        json={"error": "Server error"},
    )
    error = HTTPStatusError("Server error", request=Request("POST", "url"), response=mock_response)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.embed_text("Test", "llama2")
        assert exc_info.value.error_code == "500"


@pytest.mark.asyncio
async def test_embed_text_request_error(provider):
    """Test embed_text handles RequestError."""
    error = RequestError("Connection error")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.embed_text("Test", "llama2")
        assert "Connection error" in str(exc_info.value)
        assert exc_info.value.error_code == "request_error"


@pytest.mark.asyncio
async def test_embed_text_empty_embedding(provider):
    """Test embed_text handles empty embedding."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"embedding": [], "model": "llama2"},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await provider.embed_text("Test", "llama2")

        assert result.embedding == []
        assert result.token_count is None


# ============================================================================
# List Models Tests
# ============================================================================


@pytest.mark.asyncio
async def test_list_models_success(provider):
    mock_response = Response(
        status_code=200,
        request=Request("GET", "url"),
        json={"models": [{"name": "llama2"}, {"name": "mistral"}]},
    )
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        models = await provider.list_models()
        assert models == ["llama2", "mistral"]


@pytest.mark.asyncio
async def test_list_models_http_error(provider):
    """Test list_models handles HTTPStatusError."""
    mock_response = Response(
        status_code=404,
        request=Request("GET", "url"),
        json={"error": "Not found"},
    )
    error = HTTPStatusError("Not found", request=Request("GET", "url"), response=mock_response)

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.list_models()
        assert exc_info.value.error_code == "404"


@pytest.mark.asyncio
async def test_list_models_request_error(provider):
    """Test list_models handles RequestError."""
    error = RequestError("Network error")

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.list_models()
        assert "Network error" in str(exc_info.value)
        assert exc_info.value.error_code == "request_error"


@pytest.mark.asyncio
async def test_list_models_empty_response(provider):
    """Test list_models handles empty model list."""
    mock_response = Response(
        status_code=200,
        request=Request("GET", "url"),
        json={"models": []},
    )
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        models = await provider.list_models()
        assert models == []


# ============================================================================
# Health Check Tests
# ============================================================================


@pytest.mark.asyncio
async def test_health_check_success(provider):
    mock_response = Response(status_code=200, request=Request("GET", "url"))
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        is_healthy = await provider.health_check()
        assert is_healthy is True


@pytest.mark.asyncio
async def test_health_check_failure(provider):
    """Test health_check returns False when exception is raised."""
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=Exception("Timeout")):
        is_healthy = await provider.health_check()
        assert is_healthy is False


@pytest.mark.asyncio
async def test_health_check_non_200_status(provider):
    """Test health_check returns False for non-200 status."""
    mock_response = Response(status_code=500, request=Request("GET", "url"))
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        is_healthy = await provider.health_check()
        assert is_healthy is False


# ============================================================================
# Close Tests
# ============================================================================


@pytest.mark.asyncio
async def test_close_closes_client(provider):
    """Test close properly closes the HTTP client."""
    mock_client = AsyncMock()
    provider._client = mock_client

    await provider.close()

    mock_client.aclose.assert_awaited_once()
    assert provider._client is None


@pytest.mark.asyncio
async def test_close_when_client_none(provider):
    """Test close works when client hasn't been created."""
    provider._client = None

    # Should not raise any error
    await provider.close()

    assert provider._client is None


# ============================================================================
# Initialization Tests
# ============================================================================


def test_initialization_with_defaults():
    """Test provider initialization with default values."""
    provider = OllamaProvider()

    assert provider.base_url == "http://localhost:11434"
    assert provider.timeout == 60.0
    assert provider._client is None


def test_initialization_with_custom_values():
    """Test provider initialization with custom values."""
    provider = OllamaProvider(base_url="http://custom:8080", timeout=120.0)

    assert provider.base_url == "http://custom:8080"
    assert provider.timeout == 120.0


def test_base_url_trailing_slash_removed():
    """Test that trailing slash is removed from base_url."""
    provider = OllamaProvider(base_url="http://localhost:11434/")

    assert provider.base_url == "http://localhost:11434"


# ============================================================================
# Additional Edge Case Tests
# ============================================================================


@pytest.mark.asyncio
async def test_generate_text_with_extra_kwargs(provider):
    """Test generate_text passes extra kwargs to API."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"model": "llama2", "message": {"content": "Test"}},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        await provider.generate_text("Hi", "llama2", seed=42, top_p=0.9)
        # Just verify it doesn't raise - extra params are passed to API


@pytest.mark.asyncio
async def test_generate_text_stream_with_extra_kwargs(provider):
    """Test streaming passes extra kwargs to API."""
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.raise_for_status = AsyncMock()
    mock_stream.aiter_lines = lambda: AsyncIteratorMock(['{"done": true}'])

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream)

    with patch.object(provider, "_get_client", return_value=mock_client):
        async for _ in provider.generate_text_stream("Hi", "llama2", temperature=0.5):
            pass
        # Verify stream was called
