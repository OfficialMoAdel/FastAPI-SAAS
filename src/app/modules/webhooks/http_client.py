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


class SSRFSafePoolTransport(httpx.AsyncHTTPTransport):
    """Transport that allows pinning a request to a pre-resolved IP address.

    Looks for 'resolved_ip' in request.extensions. If found, directs the connection
    to that IP while preserving the original Host header and SNI hostname.
    This prevents DNS rebinding TOCTOU attacks.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resolved_ip = request.extensions.get("resolved_ip") if request.extensions else None

        if resolved_ip:
            original_host = request.url.host

            # Rewrite URL to use the resolved IP to avoid a second DNS resolution
            request.url = request.url.copy_with(host=resolved_ip)

            # Ensure the Host header remains the original hostname
            if "host" not in request.headers:
                request.headers["host"] = original_host
            else:
                # Some versions might normalize to lower-case 'host'
                pass # Already set by the user/client

            # Set SNI hostname in extensions so TLS handshake uses the correct name
            # instead of the IP address, preventing SSL validation failures.
            if "sni_hostname" not in request.extensions:
                request.extensions["sni_hostname"] = original_host

        return await super().handle_async_request(request)



def get_webhook_http_client() -> httpx.AsyncClient:
    """Get or create the shared webhook HTTP client.

    Returns:
        Singleton httpx.AsyncClient configured for webhook delivery.
    """
    global _webhook_http_client

    if _webhook_http_client is None or _webhook_http_client.is_closed:
        transport = SSRFSafePoolTransport(
            limits=WEBHOOK_LIMITS,
            retries=0,
        )
        _webhook_http_client = httpx.AsyncClient(
            transport=transport,
            timeout=WEBHOOK_TIMEOUT,
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
