"""Shared HTTP client for SSO providers with connection pooling.

This module provides a singleton httpx.AsyncClient that is reused across
all SSO provider HTTP requests, eliminating TCP/TLS handshake overhead.
"""

from typing import Any, cast

import httpx

from src.app.core.logger import get_logger

logger = get_logger(__name__)

# Module-level client with connection pooling
_http_client: httpx.AsyncClient | None = None


async def get_sso_http_client() -> httpx.AsyncClient:
    """Get or create the shared SSO HTTP client.

    Returns a singleton httpx.AsyncClient configured with:
    - 30 second timeout for SSO provider latency
    - Connection pooling (100 max connections, 20 keepalive)

    Returns
    -------
    httpx.AsyncClient
        Shared HTTP client instance
    """
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        logger.debug("Created shared SSO HTTP client with connection pooling")
    return _http_client


async def close_sso_http_client() -> None:
    """Close the shared SSO HTTP client.

    Should be called during application shutdown.
    """
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
        logger.debug("Closed shared SSO HTTP client")


async def sso_http_post(url: str, data: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Make a POST request using the shared SSO client.

    Parameters
    ----------
    url : str
        Target URL
    data : dict[str, Any]
        Form data to post
    headers : dict[str, str] | None
        Optional headers

    Returns
    -------
    dict[str, Any]
        JSON response
    """
    client = await get_sso_http_client()
    response = await client.post(url, data=data, headers=headers)
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


async def sso_http_get(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Make a GET request using the shared SSO client.

    Parameters
    ----------
    url : str
        Target URL
    headers : dict[str, str] | None
        Optional headers

    Returns
    -------
    dict[str, Any]
        JSON response
    """
    client = await get_sso_http_client()
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    return cast(dict[str, Any], response.json())
