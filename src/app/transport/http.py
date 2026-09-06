"""Shared HTTP transport client with resilience, pooling, and lifecycle management.

Encapsulates httpx.AsyncClient configuration, pooling limits, and timeouts
so network mechanics are isolated from domain services and integration clients.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_WRITE_TIMEOUT = 10.0
DEFAULT_POOL_TIMEOUT = 10.0

DEFAULT_MAX_CONNECTIONS = 100
DEFAULT_MAX_KEEPALIVE = 20


def create_http_client(
    *,
    timeout: float | httpx.Timeout | None = None,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    max_keepalive_connections: int = DEFAULT_MAX_KEEPALIVE,
    base_url: str = "",
    headers: dict[str, str] | None = None,
    follow_redirects: bool = False,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Create a configured httpx.AsyncClient with standardized connection pooling and timeouts.

    Args:
        timeout: Request timeout in seconds or httpx.Timeout object.
        max_connections: Maximum number of concurrent connections in the pool.
        max_keepalive_connections: Maximum number of idle keepalive connections.
        base_url: Optional base URL for requests.
        headers: Optional default request headers.
        follow_redirects: Whether to follow HTTP redirects.
        **kwargs: Additional arguments passed to httpx.AsyncClient.

    Returns:
        Configured httpx.AsyncClient instance.
    """
    if timeout is None:
        client_timeout = httpx.Timeout(
            connect=DEFAULT_CONNECT_TIMEOUT,
            read=DEFAULT_READ_TIMEOUT,
            write=DEFAULT_WRITE_TIMEOUT,
            pool=DEFAULT_POOL_TIMEOUT,
        )
    elif isinstance(timeout, int | float):
        client_timeout = httpx.Timeout(timeout)
    else:
        client_timeout = timeout

    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive_connections,
    )

    return httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=client_timeout,
        limits=limits,
        follow_redirects=follow_redirects,
        **kwargs,
    )


__all__ = [
    "create_http_client",
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_MAX_CONNECTIONS",
    "DEFAULT_MAX_KEEPALIVE",
    "DEFAULT_READ_TIMEOUT",
]
