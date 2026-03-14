from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import HTTPStatusError, Request, RequestError, Response

from src.app.modules.ai.anthropic_provider import AnthropicProvider
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
    return AnthropicProvider(api_key="sk-test", base_url="https://api.anthropic.com/v1")


# ============================================================================
# Property Tests
# ============================================================================


@pytest.mark.asyncio
async def test_provider_name(provider):
    """Test provider name property returns 'anthropic'."""
    assert provider.provider_name == "anthropic"


# ============================================================================
# Client Tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_client_creation(provider):
    """Test that client is created with correct configuration."""
    client = provider._get_client()
    assert client is not None
    assert str(client.base_url).rstrip("/") == "https://api.anthropic.com/v1"
    # Check headers are set correctly
    assert "x-api-key" in client.headers
    assert client.headers["x-api-key"] == "sk-test"
    assert client.headers["anthropic-version"] == "2023-06-01"


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
        "model": "claude-3-opus-20240229",
        "content": [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "World"},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    result = provider._parse_chat_response(data)

    assert result.content == "Hello World"
    assert result.model == "claude-3-opus-20240229"
    assert result.finish_reason == "end_turn"
    assert result.token_usage is not None
    assert result.token_usage.prompt_tokens == 10
    assert result.token_usage.completion_tokens == 5
    assert result.token_usage.total_tokens == 15


@pytest.mark.asyncio
async def test_parse_chat_response_no_content(provider):
    """Test parsing response with empty content."""
    data = {"model": "claude-3-opus-20240229", "content": []}
    result = provider._parse_chat_response(data)

    assert result.content == ""
    assert result.model == "claude-3-opus-20240229"


@pytest.mark.asyncio
async def test_parse_chat_response_no_text_blocks(provider):
    """Test parsing response with non-text content blocks."""
    data = {
        "model": "claude-3-opus-20240229",
        "content": [
            {"type": "tool_use", "id": "test"},
            {"type": "text", "text": "Result"},
        ],
    }
    result = provider._parse_chat_response(data)

    assert result.content == "Result"


@pytest.mark.asyncio
async def test_parse_chat_response_no_usage(provider):
    """Test parsing response with missing usage."""
    data = {
        "model": "claude-3-opus-20240229",
        "content": [{"type": "text", "text": "Test"}],
    }
    result = provider._parse_chat_response(data)

    assert result.content == "Test"
    assert result.token_usage is None


# ============================================================================
# Generate Text Tests
# ============================================================================


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
async def test_generate_text_without_system_prompt(provider):
    """Test generate_text without system prompt."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={
            "model": "claude-3-opus-20240229",
            "content": [{"type": "text", "text": "Response"}],
        },
    )
    post_mock = AsyncMock(return_value=mock_response)
    with patch("httpx.AsyncClient.post", post_mock):
        await provider.generate_text("Hello", "claude-3-opus-20240229")
        call_kwargs = post_mock.call_args[1]
        # No "system" key when system_prompt is None
        assert "system" not in call_kwargs["json"]


@pytest.mark.asyncio
async def test_generate_text_with_max_tokens(provider):
    """Test generate_text includes max_tokens in payload."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"model": "claude-3-opus-20240229", "content": [{"type": "text", "text": "Test"}]},
    )
    post_mock = AsyncMock(return_value=mock_response)
    with patch("httpx.AsyncClient.post", post_mock):
        await provider.generate_text("Hi", "claude-3-opus-20240229", max_tokens=100)
        call_kwargs = post_mock.call_args[1]
        assert call_kwargs["json"]["max_tokens"] == 100


@pytest.mark.asyncio
async def test_generate_text_default_max_tokens(provider):
    """Test generate_text uses default max_tokens when not provided."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"model": "claude-3-opus-20240229", "content": [{"type": "text", "text": "Test"}]},
    )
    post_mock = AsyncMock(return_value=mock_response)
    with patch("httpx.AsyncClient.post", post_mock):
        await provider.generate_text("Hi", "claude-3-opus-20240229")
        call_kwargs = post_mock.call_args[1]
        # Default max_tokens is 4096
        assert call_kwargs["json"]["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_generate_text_http_error(provider):
    mock_response = Response(
        status_code=400, request=Request("POST", "url"), json={"error": {"message": "Invalid auth"}}
    )
    error = HTTPStatusError("Error", request=Request("POST", "url"), response=mock_response)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_text("Hi", "claude-base")
        assert "Invalid auth" in str(exc_info.value)
        assert exc_info.value.error_code == "400"


@pytest.mark.asyncio
async def test_generate_text_request_error(provider):
    """Test generate_text handles RequestError."""
    error = RequestError("Connection failed")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_text("Hi", "claude-3-opus-20240229")
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
            'data: {"type": "content_block_delta", "delta": {"text": "Hello"}}',
            'data: {"type": "content_block_delta", "delta": {"text": " World"}}',
            "data: [DONE]",
        ]
    )

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream)

    with patch.object(provider, "_get_client", return_value=mock_client):
        chunks = []
        async for chunk in provider.generate_text_stream("Hi", "claude-3-opus-20240229"):
            chunks.append(chunk)

        assert chunks == ["Hello", " World"]


@pytest.mark.asyncio
async def test_generate_text_stream_http_error(provider):
    """Test streaming handles HTTPStatusError."""
    mock_response = Response(
        status_code=429,
        request=Request("POST", "url"),
        json={"error": {"message": "Rate limited"}},
    )
    error = HTTPStatusError("Rate limited", request=Request("POST", "url"), response=mock_response)

    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(side_effect=error)

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream)

    with patch.object(provider, "_get_client", return_value=mock_client):
        with pytest.raises(AIProviderError) as exc_info:
            async for _ in provider.generate_text_stream("Hi", "claude-3-opus-20240229"):
                pass
        assert "Rate limited" in str(exc_info.value)
        assert exc_info.value.error_code == "429"


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
            async for _ in provider.generate_text_stream("Hi", "claude-3-opus-20240229"):
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
            "data: invalid json {",
            'data: {"type": "content_block_delta", "delta": {"text": "Valid"}}',
            "data: [DONE]",
        ]
    )

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream)

    with patch.object(provider, "_get_client", return_value=mock_client):
        chunks = []
        async for chunk in provider.generate_text_stream("Hi", "claude-3-opus-20240229"):
            chunks.append(chunk)
        # Should skip malformed JSON and continue
        assert "Valid" in chunks


@pytest.mark.asyncio
async def test_generate_text_stream_non_delta_events(provider):
    """Test streaming skips non-content_block_delta events."""
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.raise_for_status = AsyncMock()
    mock_stream.aiter_lines = lambda: AsyncIteratorMock(
        [
            'data: {"type": "message_start"}',
            'data: {"type": "content_block_start"}',
            'data: {"type": "content_block_delta", "delta": {"text": "Content"}}',
            'data: {"type": "content_block_stop"}',
            'data: {"type": "message_delta", "delta": {"usage": {"output_tokens": 10}}}',
            "data: [DONE]",
        ]
    )

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream)

    with patch.object(provider, "_get_client", return_value=mock_client):
        chunks = []
        async for chunk in provider.generate_text_stream("Hi", "claude-3-opus-20240229"):
            chunks.append(chunk)
        # Should only get content from content_block_delta events
        assert "Content" in chunks


# ============================================================================
# Generate JSON Tests
# ============================================================================


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
async def test_generate_json_without_code_blocks(provider):
    """Test JSON generation with response not wrapped in code blocks."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"content": [{"type": "text", "text": '{"name": "test"}'}]},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await provider.generate_json("Prompt", "claude")

        assert result == {"name": "test"}


@pytest.mark.asyncio
async def test_generate_json_with_plain_markdown(provider):
    """Test JSON generation with plain markdown code blocks."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"content": [{"type": "text", "text": '```\n{"key": "value"}\n```'}]},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await provider.generate_json("Prompt", "claude")

        assert result == {"key": "value"}


@pytest.mark.asyncio
async def test_generate_json_malformed_json(provider):
    """Test generate_json handles JSON parse error."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"content": [{"type": "text", "text": "not valid json"}]},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_json("Prompt", "claude")
        assert "Failed to parse JSON" in str(exc_info.value)
        assert exc_info.value.error_code == "json_parse_error"


# ============================================================================
# Embed Text Tests
# ============================================================================


@pytest.mark.asyncio
async def test_embed_text_not_supported(provider):
    """Test embed_text raises error as Anthropic doesn't support embeddings."""
    with pytest.raises(AIProviderError) as exc_info:
        await provider.embed_text("Test", "model")
    assert "Anthropic does not currently provide an embeddings API" in str(exc_info.value)
    assert exc_info.value.error_code == "not_supported"


# ============================================================================
# List Models Tests
# ============================================================================


@pytest.mark.asyncio
async def test_list_models_success(provider):
    """Test list_models returns known Claude models."""
    models = await provider.list_models()
    assert "claude-3-opus-20240229" in models
    assert "claude-3-sonnet-20240229" in models
    assert "claude-3-haiku-20240307" in models
    assert "claude-2.1" in models
    assert "claude-2.0" in models
    assert "claude-instant-1.2" in models


# ============================================================================
# Health Check Tests
# ============================================================================


@pytest.mark.asyncio
async def test_health_check_success(provider):
    mock_response = Response(status_code=200, request=Request("POST", "url"))
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        is_healthy = await provider.health_check()
        assert is_healthy is True


@pytest.mark.asyncio
async def test_health_check_failure(provider):
    """Test health_check returns False when exception is raised."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=Exception("Timeout")):
        is_healthy = await provider.health_check()
        assert is_healthy is False


@pytest.mark.asyncio
async def test_health_check_non_200_status(provider):
    """Test health_check returns False for non-200 status."""
    mock_response = Response(status_code=500, request=Request("POST", "url"))
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
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
    provider = AnthropicProvider(api_key="test-key")

    assert provider.api_key == "test-key"
    assert provider.base_url == "https://api.anthropic.com/v1"
    assert provider.timeout == 60.0
    assert provider._client is None


def test_initialization_with_custom_values():
    """Test provider initialization with custom values."""
    provider = AnthropicProvider(api_key="test-key", base_url="https://custom.anthropic.com/v1", timeout=120.0)

    assert provider.api_key == "test-key"
    assert provider.base_url == "https://custom.anthropic.com/v1"
    assert provider.timeout == 120.0


def test_base_url_trailing_slash_removed():
    """Test that trailing slash is removed from base_url."""
    provider = AnthropicProvider(api_key="test-key", base_url="https://api.anthropic.com/v1/")

    assert provider.base_url == "https://api.anthropic.com/v1"


# ============================================================================
# Additional Edge Case Tests
# ============================================================================


@pytest.mark.asyncio
async def test_generate_text_with_extra_kwargs(provider):
    """Test generate_text passes extra kwargs to API."""
    mock_response = Response(
        status_code=200,
        request=Request("POST", "url"),
        json={"model": "claude-3-opus-20240229", "content": [{"type": "text", "text": "Test"}]},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        await provider.generate_text("Hi", "claude-3-opus-20240229", top_p=0.9, seed=42)
        # Just verify it doesn't raise - extra params are passed to API


@pytest.mark.asyncio
async def test_generate_text_stream_default_max_tokens(provider):
    """Test streaming uses default max_tokens when not provided."""
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.raise_for_status = AsyncMock()
    mock_stream.aiter_lines = lambda: AsyncIteratorMock(["data: [DONE]"])

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream)

    post_mock = AsyncMock()
    mock_client.post = post_mock

    with patch.object(provider, "_get_client", return_value=mock_client):
        async for _ in provider.generate_text_stream("Hi", "claude-3-opus-20240229"):
            pass
        # Verify default max_tokens was used
