"""Typed middleware request-state seam (B6).

Starlette ``request.state`` is an untyped bag. These helpers declare the
small ordered contract our guard stack shares so middleware stops passing
string keys at a distance:

- ``client_ip`` (set by IPFilter, read by admission/rate-limit)
- ``ip_whitelisted`` (set by IPFilter, read by admission)
- ``request_id`` (set by RequestID, read by error handlers/logging)
- ``is_token_blacklisted`` (set by admission, read by auth deps)

Attribute names on ``request.state`` are unchanged (tests and downstream
readers keep working); this module is the single place that names them.
"""

from __future__ import annotations

from typing import Any

CLIENT_IP_KEY = "client_ip"
IP_WHITELISTED_KEY = "ip_whitelisted"
REQUEST_ID_KEY = "request_id"
TOKEN_BLACKLISTED_KEY = "is_token_blacklisted"


def set_client_ip(state: Any, client_ip: str) -> None:
    """Bind the resolved client IP for downstream middleware."""
    state.client_ip = client_ip


def get_client_ip_state(state: Any, default: str | None = None) -> str | None:
    """Read the bound client IP, if present."""
    return getattr(state, CLIENT_IP_KEY, default)


def set_whitelisted(state: Any, value: bool) -> None:
    """Mark whether the request bypasses admission via IP whitelist."""
    state.ip_whitelisted = value


def is_whitelisted(state: Any) -> bool:
    """Whether the request was marked whitelisted (default False)."""
    return bool(getattr(state, IP_WHITELISTED_KEY, False))


def set_request_id(state: Any, request_id: str) -> None:
    """Bind the request ID for tracing/logging."""
    state.request_id = request_id


def get_request_id(state: Any, default: str | None = None) -> str | None:
    """Read the bound request ID, if present."""
    return getattr(state, REQUEST_ID_KEY, default)


def set_token_blacklisted(state: Any, value: bool | None) -> None:
    """Record the token-blacklist verdict for downstream auth handling."""
    state.is_token_blacklisted = value


def get_token_blacklisted(state: Any) -> bool | None:
    """Read the token-blacklist verdict, if present."""
    return getattr(state, TOKEN_BLACKLISTED_KEY, None)


__all__ = [
    "CLIENT_IP_KEY",
    "IP_WHITELISTED_KEY",
    "REQUEST_ID_KEY",
    "TOKEN_BLACKLISTED_KEY",
    "get_client_ip_state",
    "get_request_id",
    "get_token_blacklisted",
    "is_whitelisted",
    "set_client_ip",
    "set_request_id",
    "set_token_blacklisted",
    "set_whitelisted",
]
