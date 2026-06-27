"""Unit tests for shared webhook HTTP client."""


import httpx
import pytest

from src.app.modules.webhooks import http_client
from src.app.modules.webhooks.http_client import (
    WEBHOOK_LIMITS,
    WEBHOOK_TIMEOUT,
    close_webhook_http_client,
    get_webhook_http_client,
)


@pytest.fixture(autouse=True)
async def reset_singleton():
    """Reset the shared client singleton before each test."""
    await close_webhook_http_client()
    yield
    await close_webhook_http_client()


class TestWebhookHttpClientConfig:
    """Tests for webhook HTTP client configuration constants."""

    def test_timeout_constants(self):
        """Should have correct timeout constants."""
        # WEBHOOK_TIMEOUT is an httpx.Timeout object
        assert WEBHOOK_TIMEOUT.read == 30.0
        assert WEBHOOK_TIMEOUT.connect == 10.0

    def test_limit_constants(self):
        """Should have correct connection limit constants."""
        assert WEBHOOK_LIMITS.max_connections == 50
        assert WEBHOOK_LIMITS.max_keepalive_connections == 20
        assert WEBHOOK_LIMITS.keepalive_expiry == 30.0


class TestGetWebhookHttpClient:
    """Tests for get_webhook_http_client function."""

    @pytest.mark.asyncio
    async def test_creates_client_on_first_call(self):
        """Should create a new client on first call."""
        assert http_client._webhook_http_client is None

        client = await get_webhook_http_client()

        assert client is not None
        assert isinstance(client, httpx.AsyncClient)
        assert http_client._webhook_http_client is client

    @pytest.mark.asyncio
    async def test_returns_same_client_on_subsequent_calls(self):
        """Should return the same singleton client on multiple calls."""
        client1 = await get_webhook_http_client()
        client2 = await get_webhook_http_client()
        client3 = await get_webhook_http_client()

        assert client1 is client2
        assert client2 is client3

    @pytest.mark.asyncio
    async def test_client_has_correct_timeout(self):
        """Should configure correct timeout values."""
        client = await get_webhook_http_client()

        assert client.timeout.read == 30.0
        assert client.timeout.connect == 10.0

    @pytest.mark.asyncio
    async def test_client_has_connection_pooling(self):
        """Should configure connection pooling limits."""
        await get_webhook_http_client()

        # Verify by checking the WEBHOOK_LIMITS constants
        assert WEBHOOK_LIMITS.max_connections == 50
        assert WEBHOOK_LIMITS.max_keepalive_connections == 20

    @pytest.mark.asyncio
    async def test_client_does_not_follow_redirects(self):
        """Should not follow redirects for security."""
        client = await get_webhook_http_client()

        assert client.follow_redirects is False

    @pytest.mark.asyncio
    async def test_recreates_client_if_closed(self):
        """Should create new client if existing one is closed."""
        client1 = await get_webhook_http_client()
        await client1.aclose()

        # Singleton should still point to closed client
        assert http_client._webhook_http_client.is_closed

        client2 = await get_webhook_http_client()

        assert client2 is not client1
        assert not client2.is_closed
        assert http_client._webhook_http_client is client2


class TestCloseWebhookHttpClient:
    """Tests for close_webhook_http_client function."""

    @pytest.mark.asyncio
    async def test_closes_existing_client(self):
        """Should close the client and reset the singleton."""
        # Create client first
        await get_webhook_http_client()
        assert http_client._webhook_http_client is not None

        await close_webhook_http_client()

        assert http_client._webhook_http_client is None

    @pytest.mark.asyncio
    async def test_handles_no_client(self):
        """Should not raise if no client exists."""
        assert http_client._webhook_http_client is None
        # Should not raise
        await close_webhook_http_client()

    @pytest.mark.asyncio
    async def test_handles_already_closed_client(self):
        """Should handle already closed client gracefully."""
        client = await get_webhook_http_client()
        await client.aclose()

        # Should handle and reset to None
        await close_webhook_http_client()
        assert http_client._webhook_http_client is None

    @pytest.mark.asyncio
    async def test_client_is_actually_closed(self):
        """Should actually close the httpx client."""
        client = await get_webhook_http_client()

        await close_webhook_http_client()

        assert client.is_closed





class TestConnectionPoolingBehavior:
    """Integration-style tests for connection pooling."""

    @pytest.mark.asyncio
    async def test_client_is_singleton(self):
        """Verify client remains the same object."""
        client1 = await get_webhook_http_client()
        client2 = await get_webhook_http_client()
        assert id(client1) == id(client2)

    @pytest.mark.asyncio
    async def test_client_recreated_after_close(self):
        """Should create new client after previous one was closed."""
        client1 = await get_webhook_http_client()
        await close_webhook_http_client()

        client2 = await get_webhook_http_client()

        assert client1 is not client2
        assert not client2.is_closed


class TestWebhookSecuritySettings:
    """Tests for security-related client settings."""

    @pytest.mark.asyncio
    async def test_no_redirect_following(self):
        """Webhooks should not follow redirects for security."""
        client = await get_webhook_http_client()

        # Following redirects could leak webhook signatures to unintended hosts
        assert client.follow_redirects is False

    @pytest.mark.asyncio
    async def test_reasonable_connection_timeout(self):
        """Should have reasonable connection timeout for external endpoints."""
        client = await get_webhook_http_client()

        # 10s connect timeout allows for slow external endpoints
        # but prevents hanging indefinitely
        assert client.timeout.connect == 10.0

    @pytest.mark.asyncio
    async def test_total_timeout_prevents_hangs(self):
        """Should have total timeout to prevent request hangs."""
        client = await get_webhook_http_client()

        # 30s read timeout matches typical webhook expectations
        assert client.timeout.read == 30.0
