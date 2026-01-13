"""Shared HTTP client for webhook delivery.

This module provides a singleton httpx.AsyncClient with connection pooling
for efficient webhook delivery. The client is reused across all webhook
deliveries to avoid connection overhead.

Performance Benefits:
- Connection pooling eliminates per-request connection overhead
- Keep-alive connections reduce latency for repeated deliveries
- Centralized timeout and retry configuration
"""

from typing import Any

import httpx

from src.app.core.logger import get_logger

logger = get_logger(__name__)

# Singleton client instance
_webhook_http_client: httpx.AsyncClient | None = None

# Webhook-specific timeout configuration
# Longer timeout for webhook delivery (external endpoints may be slow)
WEBHOOK_TIMEOUT = httpx.Timeout(
    timeout=30.0,  # 30s total timeout
    connect=10.0,  # 10s connection timeout
)

# Connection pool limits for webhook delivery
WEBHOOK_LIMITS = httpx.Limits(
    max_connections=50,  # Max concurrent connections
    max_keepalive_connections=20,  # Keep-alive pool size
    keepalive_expiry=30.0,  # 30s keep-alive expiry
)


def get_webhook_http_client() -> httpx.AsyncClient:
    """Get or create the shared webhook HTTP client.

    Returns:
        Singleton httpx.AsyncClient configured for webhook delivery.
    """
    global _webhook_http_client

    if _webhook_http_client is None or _webhook_http_client.is_closed:
        _webhook_http_client = httpx.AsyncClient(
            timeout=WEBHOOK_TIMEOUT,
            limits=WEBHOOK_LIMITS,
            follow_redirects=False,  # Don't follow redirects for security
        )
        logger.debug("Created shared webhook HTTP client")

    return _webhook_http_client


async def close_webhook_http_client() -> None:
    """Close the shared webhook HTTP client.

    Should be called during application shutdown.
    """
    global _webhook_http_client

    if _webhook_http_client is not None and not _webhook_http_client.is_closed:
        await _webhook_http_client.aclose()
        _webhook_http_client = None
        logger.debug("Closed shared webhook HTTP client")


async def webhook_http_post(
    url: str,
    *,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Make HTTP POST request using shared webhook client.

    Args:
        url: Target URL for webhook delivery
        json: JSON payload to send
        headers: Optional headers to include

    Returns:
        httpx.Response object

    Raises:
        httpx.HTTPError: On connection/timeout errors
    """
    client = get_webhook_http_client()
    return await client.post(url, json=json, headers=headers)
