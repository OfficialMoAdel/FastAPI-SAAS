"""Compatibility exports for the security module.

IP filtering is implemented by the core-side :mod:`core.guard.ip_rules`
module. These re-exports keep existing security-module imports stable while
removing the core -> feature dependency.
"""

from src.app.core.guard.ip_rules import (
    IPFilterService,
    get_redis_client,
    invalidate_ip_status_cache,
    ip_filter_service,
)

__all__ = [
    "IPFilterService",
    "get_redis_client",
    "invalidate_ip_status_cache",
    "ip_filter_service",
]
