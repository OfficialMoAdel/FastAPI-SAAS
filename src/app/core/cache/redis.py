"""Redis cache client export for cache warming.

Backward-compat re-export; delegates to the Redis provider seam.
"""

from typing import Any

from src.app.core.redis_provider import get_redis

__all__ = ["get_redis"]


def __getattr__(name: str) -> Any:
    if name == "redis_client":
        return get_redis()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
