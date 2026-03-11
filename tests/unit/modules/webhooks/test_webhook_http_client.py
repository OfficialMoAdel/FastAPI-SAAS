"""Tests for Webhook HTTP client with connection pooling.

Tests verify:
- Singleton pattern for client reuse
- Connection pooling configuration
- HTTP POST method works correctly
- Client lifecycle (create/close)
- Security settings (no redirects)
- Error handling
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.app.modules.webhooks import http_client
from src.app.modules.webhooks.http_client import (
    WEBHOOK_LIMITS,
    WEBHOOK_TIMEOUT,
    close_webhook_http_client,
    get_webhook_http_client,
    webhook_http_post,
)


@pytest.fixture(autouse=True)
async def reset_client():
    """Reset the singleton client before and after each test."""
    # Reset before test
    http_client._webhook_http_client = None
    yield
    # Cleanup after test
    if http_client._webhook_http_client is not None and not http_client._webhook_http_client.is_closed:
        await http_client._webhook_http_client.aclose()
        http_client._webhook_http_client = None


class TestWebhookTimeoutAndLimits:
    """Tests for webhook timeout and limit constants."""

    def test_timeout_configuration(self):
        """Should have correct timeout values."""
        assert WEBHOOK_TIMEOUT.read == 30.0
        assert WEBHOOK_TIMEOUT.connect == 10.0

    def test_limits_configuration(self):
        """Should have correct connection pool limits."""
        assert WEBHOOK_LIMITS.max_connections == 50
        assert WEBHOOK_LIMITS.max_keepalive_connections == 20
        assert WEBHOOK_LIMITS.keepalive_expiry == 30.0


class TestGetWebhookHttpClient:
    """Tests for get_webhook_http_client function."""

    def test_creates_client_on_first_call(self):
        """Should create a new client on first call."""
        assert http_client._webhook_http_client is None

        client = get_webhook_http_client()

        assert client is not None
        assert isinstance(client, httpx.AsyncClient)
        assert http_client._webhook_http_client is client

    def test_returns_same_client_on_subsequent_calls(self):
        """Should return the same singleton client on multiple calls."""
        client1 = get_webhook_http_client()
        client2 = get_webhook_http_client()
        client3 = get_webhook_http_client()

        assert client1 is client2
        assert client2 is client3

    def test_client_has_correct_timeout(self):
        """Should configure correct timeout values."""
        client = get_webhook_http_client()

        assert client.timeout.read == 30.0
        assert client.timeout.connect == 10.0

    def test_client_has_connection_pooling(self):
        """Should configure connection pooling limits."""
        get_webhook_http_client()

        # Verify by checking the WEBHOOK_LIMITS constants
        assert WEBHOOK_LIMITS.max_connections == 50
        assert WEBHOOK_LIMITS.max_keepalive_connections == 20

    def test_client_does_not_follow_redirects(self):
        """Should not follow redirects for security."""
        client = get_webhook_http_client()

        assert client.follow_redirects is False

    @pytest.mark.asyncio
    async def test_recreates_client_if_closed(self):
        """Should create new client if existing one is closed."""
        client1 = get_webhook_http_client()
        await client1.aclose()

        client2 = get_webhook_http_client()

        assert client1 is not client2
        assert not client2.is_closed


class TestCloseWebhookHttpClient:
    """Tests for close_webhook_http_client function."""

    @pytest.mark.asyncio
    async def test_closes_existing_client(self):
        """Should close the client and reset the singleton."""
        # Create client first
        get_webhook_http_client()
        assert http_client._webhook_http_client is not None

        # Close it
        await close_webhook_http_client()

        assert http_client._webhook_http_client is None

    @pytest.mark.asyncio
    async def test_handles_no_client_gracefully(self):
        """Should not raise when no client exists."""
        assert http_client._webhook_http_client is None

        # Should not raise
        await close_webhook_http_client()

        assert http_client._webhook_http_client is None

    @pytest.mark.asyncio
    async def test_handles_already_closed_client(self):
        """Should handle already closed client gracefully."""
        client = get_webhook_http_client()
        await client.aclose()

        # Should not raise even though client is already closed
        await close_webhook_http_client()

    @pytest.mark.asyncio
    async def test_client_is_actually_closed(self):
        """Should actually close the httpx client."""
        client = get_webhook_http_client()

        await close_webhook_http_client()

        assert client.is_closed


class TestWebhookHttpPost:
    """Tests for webhook_http_post function."""

    @pytest.mark.asyncio
    async def test_makes_post_request_with_json(self):
        """Should make POST request with JSON payload."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await webhook_http_post(
                "https://webhook.example.com/endpoint",
                json={"event": "user.created", "data": {"id": "123"}},
                headers={"X-Webhook-Signature": "sha256=abc123"},
            )

            mock_post.assert_called_once_with(
                "https://webhook.example.com/endpoint",
                json={"event": "user.created", "data": {"id": "123"}},
                headers={"X-Webhook-Signature": "sha256=abc123"},
            )
            assert result == mock_response

    @pytest.mark.asyncio
    async def test_returns_response_object(self):
        """Should return the full response object (not just JSON)."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = "OK"

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await webhook_http_post("https://example.com", json={})

            assert result is mock_response
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_post_without_headers(self):
        """Should work without optional headers."""
        mock_response = MagicMock(spec=httpx.Response)

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await webhook_http_post(
                "https://webhook.example.com/endpoint",
                json={"event": "test"},
            )

            mock_post.assert_called_once_with(
                "https://webhook.example.com/endpoint",
                json={"event": "test"},
                headers=None,
            )

    @pytest.mark.asyncio
    async def test_post_without_json(self):
        """Should work without JSON payload."""
        mock_response = MagicMock(spec=httpx.Response)

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await webhook_http_post("https://webhook.example.com/endpoint")

            mock_post.assert_called_once_with(
                "https://webhook.example.com/endpoint",
                json=None,
                headers=None,
            )

    @pytest.mark.asyncio
    async def test_propagates_connection_error(self):
        """Should propagate connection errors."""
        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.ConnectError("Connection refused")

            with pytest.raises(httpx.ConnectError):
                await webhook_http_post("https://unreachable.example.com", json={})

    @pytest.mark.asyncio
    async def test_propagates_timeout_error(self):
        """Should propagate timeout errors."""
        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.TimeoutException("Request timed out")

            with pytest.raises(httpx.TimeoutException):
                await webhook_http_post("https://slow.example.com", json={})

    @pytest.mark.asyncio
    async def test_uses_shared_client(self):
        """Should use the shared singleton client."""
        # Get client first to ensure it exists
        client = get_webhook_http_client()

        mock_response = MagicMock(spec=httpx.Response)

        with patch.object(client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await webhook_http_post("https://example.com", json={})

            mock_post.assert_called_once()


class TestConnectionPoolingBehavior:
    """Tests verifying connection pooling behavior."""

    @pytest.mark.asyncio
    async def test_multiple_requests_reuse_client(self):
        """Multiple requests should reuse the same client instance."""
        mock_response = MagicMock(spec=httpx.Response)

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            # Make multiple requests
            await webhook_http_post("https://example.com/1", json={"n": 1})
            await webhook_http_post("https://example.com/2", json={"n": 2})
            await webhook_http_post("https://example.com/3", json={"n": 3})

            # Should have created only one client
            assert http_client._webhook_http_client is not None
            # All requests should go through the same client
            assert mock_post.call_count == 3

    @pytest.mark.asyncio
    async def test_client_recreated_after_close(self):
        """Should create new client after previous one was closed."""
        client1 = get_webhook_http_client()
        await close_webhook_http_client()

        client2 = get_webhook_http_client()

        assert client1 is not client2
        assert not client2.is_closed


class TestWebhookSecuritySettings:
    """Tests for webhook-specific security settings."""

    def test_no_redirect_following(self):
        """Webhooks should not follow redirects for security."""
        client = get_webhook_http_client()

        # Following redirects could leak webhook signatures to unintended hosts
        assert client.follow_redirects is False

    def test_reasonable_connection_timeout(self):
        """Should have reasonable connection timeout for external endpoints."""
        client = get_webhook_http_client()

        # 10s connect timeout allows for slow external endpoints
        # but prevents hanging indefinitely
        assert client.timeout.connect == 10.0

    def test_total_timeout_prevents_hangs(self):
        """Should have total timeout to prevent request hangs."""
        client = get_webhook_http_client()

        # 30s read timeout matches typical webhook expectations
        assert client.timeout.read == 30.0
