"""Unit tests for OllamaClient connection limits.

Tests verify that httpx.Limits are properly configured to prevent
resource exhaustion under high AI load.
"""

import pytest

from src.app.core.config import settings
from src.app.modules.ai.ollama_client import OllamaClient


class TestOllamaClientConnectionLimits:
    """Tests for OllamaClient httpx connection limits."""

    @pytest.mark.asyncio
    async def test_client_has_connection_limits(self) -> None:
        """Verify client is created with connection limits configured."""
        client = OllamaClient(base_url="http://localhost:11434", timeout=30.0)

        try:
            httpx_client = await client._get_client()

            # Access limits via transport pool (httpx internal structure)
            pool = httpx_client._transport._pool

            # Verify limit values match settings
            assert pool._max_connections == settings.OLLAMA_MAX_CONNECTIONS
            assert pool._max_keepalive_connections == settings.OLLAMA_MAX_KEEPALIVE
            assert pool._keepalive_expiry == settings.OLLAMA_KEEPALIVE_EXPIRY
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_connection_limits_use_default_values(self) -> None:
        """Verify connection limits use default values from settings."""
        client = OllamaClient()

        try:
            httpx_client = await client._get_client()
            pool = httpx_client._transport._pool

            # Default values from AISettings
            assert pool._max_connections == 100
            assert pool._max_keepalive_connections == 20
            assert pool._keepalive_expiry == 30.0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_client_reuses_same_instance(self) -> None:
        """Verify _get_client returns the same instance on subsequent calls."""
        client = OllamaClient()

        try:
            httpx_client_1 = await client._get_client()
            httpx_client_2 = await client._get_client()

            assert httpx_client_1 is httpx_client_2, "Should reuse the same httpx client"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_client_recreated_after_close(self) -> None:
        """Verify new client is created after close."""
        client = OllamaClient()

        httpx_client_1 = await client._get_client()
        await client.close()
        httpx_client_2 = await client._get_client()

        assert httpx_client_1 is not httpx_client_2, "Should create new client after close"

        # Cleanup
        await client.close()

    @pytest.mark.asyncio
    async def test_limits_prevent_unbounded_connections(self) -> None:
        """Verify limits are set to prevent unbounded connection creation."""
        client = OllamaClient()

        try:
            httpx_client = await client._get_client()
            pool = httpx_client._transport._pool

            # Key assertion: max_connections must be a reasonable positive number
            assert pool._max_connections > 0
            assert pool._max_connections <= 1000  # Sanity check

            # keepalive connections should be less than or equal to max
            assert pool._max_keepalive_connections <= pool._max_connections
        finally:
            await client.close()
