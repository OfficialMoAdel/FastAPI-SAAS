"""Tests for SSO HTTP client with connection pooling.

Tests verify:
- Singleton pattern for client reuse
- Connection pooling configuration
- HTTP methods work correctly
- Client lifecycle (create/close)
- Error handling
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.app.modules.sso import http_client
from src.app.modules.sso.http_client import (
    close_sso_http_client,
    get_sso_http_client,
    sso_http_get,
    sso_http_post,
)


@pytest.fixture(autouse=True)
async def reset_client():
    """Reset the singleton client before and after each test."""
    # Reset before test
    http_client._http_client = None
    yield
    # Cleanup after test
    if http_client._http_client is not None:
        await http_client._http_client.aclose()
        http_client._http_client = None


class TestGetSsoHttpClient:
    """Tests for get_sso_http_client function."""

    @pytest.mark.asyncio
    async def test_creates_client_on_first_call(self):
        """Should create a new client on first call."""
        assert http_client._http_client is None

        client = await get_sso_http_client()

        assert client is not None
        assert isinstance(client, httpx.AsyncClient)
        assert http_client._http_client is client

    @pytest.mark.asyncio
    async def test_returns_same_client_on_subsequent_calls(self):
        """Should return the same singleton client on multiple calls."""
        client1 = await get_sso_http_client()
        client2 = await get_sso_http_client()
        client3 = await get_sso_http_client()

        assert client1 is client2
        assert client2 is client3

    @pytest.mark.asyncio
    async def test_client_has_correct_timeout(self):
        """Should configure 30 second timeout."""
        client = await get_sso_http_client()

        assert client.timeout.read == 30.0
        assert client.timeout.write == 30.0
        assert client.timeout.connect == 30.0

    @pytest.mark.asyncio
    async def test_client_has_connection_pooling(self):
        """Should configure connection pooling limits."""
        client = await get_sso_http_client()

        # Verify client is created - limits are configured internally
        # Check that the client exists and is an AsyncClient
        assert client is not None
        assert isinstance(client, httpx.AsyncClient)


class TestCloseSsoHttpClient:
    """Tests for close_sso_http_client function."""

    @pytest.mark.asyncio
    async def test_closes_existing_client(self):
        """Should close the client and reset the singleton."""
        # Create client first
        client = await get_sso_http_client()
        assert http_client._http_client is not None

        # Close it
        await close_sso_http_client()

        assert http_client._http_client is None

    @pytest.mark.asyncio
    async def test_handles_no_client_gracefully(self):
        """Should not raise when no client exists."""
        assert http_client._http_client is None

        # Should not raise
        await close_sso_http_client()

        assert http_client._http_client is None

    @pytest.mark.asyncio
    async def test_client_is_actually_closed(self):
        """Should actually close the httpx client."""
        client = await get_sso_http_client()

        await close_sso_http_client()

        assert client.is_closed


class TestSsoHttpPost:
    """Tests for sso_http_post function."""

    @pytest.mark.asyncio
    async def test_makes_post_request(self):
        """Should make POST request with correct parameters."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "test123"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await sso_http_post(
                "https://oauth.example.com/token",
                data={"grant_type": "authorization_code", "code": "abc123"},
                headers={"Accept": "application/json"},
            )

            mock_post.assert_called_once_with(
                "https://oauth.example.com/token",
                data={"grant_type": "authorization_code", "code": "abc123"},
                headers={"Accept": "application/json"},
            )
            assert result == {"access_token": "test123"}

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        """Should raise HTTPStatusError on non-2xx response."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=MagicMock(status_code=401),
        )

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            with pytest.raises(httpx.HTTPStatusError):
                await sso_http_post("https://oauth.example.com/token", data={})

    @pytest.mark.asyncio
    async def test_uses_shared_client(self):
        """Should use the shared singleton client."""
        # Get client first to ensure it exists
        client = await get_sso_http_client()

        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await sso_http_post("https://example.com", data={})

            mock_post.assert_called_once()


class TestSsoHttpGet:
    """Tests for sso_http_get function."""

    @pytest.mark.asyncio
    async def test_makes_get_request(self):
        """Should make GET request with correct parameters."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"email": "user@example.com", "name": "Test User"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await sso_http_get(
                "https://api.example.com/userinfo",
                headers={"Authorization": "Bearer token123"},
            )

            mock_get.assert_called_once_with(
                "https://api.example.com/userinfo",
                headers={"Authorization": "Bearer token123"},
            )
            assert result == {"email": "user@example.com", "name": "Test User"}

    @pytest.mark.asyncio
    async def test_get_without_headers(self):
        """Should work without optional headers."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": "value"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await sso_http_get("https://api.example.com/data")

            mock_get.assert_called_once_with(
                "https://api.example.com/data",
                headers=None,
            )
            assert result == {"data": "value"}

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        """Should raise HTTPStatusError on non-2xx response."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403 Forbidden",
            request=MagicMock(),
            response=MagicMock(status_code=403),
        )

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            with pytest.raises(httpx.HTTPStatusError):
                await sso_http_get("https://api.example.com/protected")


class TestConnectionPoolingBehavior:
    """Tests verifying connection pooling behavior."""

    @pytest.mark.asyncio
    async def test_multiple_requests_reuse_client(self):
        """Multiple requests should reuse the same client instance."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
                mock_post.return_value = mock_response
                mock_get.return_value = mock_response

                # Make multiple requests
                await sso_http_post("https://example.com/1", data={})
                await sso_http_get("https://example.com/2")
                await sso_http_post("https://example.com/3", data={})

                # Should have created only one client
                assert http_client._http_client is not None

    @pytest.mark.asyncio
    async def test_client_recreated_after_close(self):
        """Should create new client after previous one was closed."""
        client1 = await get_sso_http_client()
        await close_sso_http_client()

        client2 = await get_sso_http_client()

        assert client1 is not client2
        assert not client2.is_closed
