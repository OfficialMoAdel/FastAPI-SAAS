"""Redis cache client export for cache warming.

This module provides backward compatibility for imports expecting
redis_client from the cache module.
"""
from src.app.core.utils.cache import client as redis_client

__all__ = ["redis_client"]
