"""Generic one-time token store backed by Redis."""

import hashlib
from typing import Any, cast

import orjson
from redis.asyncio import Redis

from src.app.core.utils.redis_utils import _GETDEL_LUA


class PendingTokenStore:
    """Redis-backed one-time token store with atomic GETDEL consumption.

    Replaces MFAPendingTokenManager, SSOStateManager, and MagicLinkManager
    with a single parameterized implementation.
    """

    def __init__(self, redis: Redis, prefix: str, ttl_seconds: int, use_hash: bool = False):
        """Initialize the pending token store.

        Args:
            redis: Async Redis client instance.
            prefix: Prefix for the Redis keys.
            ttl_seconds: Time-to-live for the tokens in seconds.
            use_hash: Whether to hash the token using SHA-256 before using it as a key.
        """
        self._redis = redis
        self.prefix = prefix
        self.ttl = ttl_seconds
        self.use_hash = use_hash

    def _get_key(self, token: str) -> str:
        """Generate the Redis key for a given token."""
        token_val = hashlib.sha256(token.encode()).hexdigest() if self.use_hash else token
        return f"{self.prefix}:{token_val}"

    async def create(self, token: str, data: dict[str, Any]) -> None:
        """Store token data with TTL.

        Args:
            token: The raw token string.
            data: Dictionary of data to store with the token.
        """
        key = self._get_key(token)
        payload = orjson.dumps(data).decode('utf-8')
        await self._redis.setex(key, self.ttl, payload)

    async def consume(self, token: str) -> dict[str, Any] | None:
        """Atomically retrieve and delete token data (GETDEL).

        Args:
            token: The raw token string.

        Returns:
            Dictionary of stored data if valid, None if expired/used/invalid.
        """
        key = self._get_key(token)
        data = await self._redis.eval(_GETDEL_LUA, 1, key)  # type: ignore[misc]

        if data is None:
            return None

        try:
            return cast(dict[str, Any], orjson.loads(data))
        except orjson.JSONDecodeError:
            return None

    async def peek(self, token: str) -> dict[str, Any] | None:
        """Retrieve token data without deleting it (GET).

        Args:
            token: The raw token string.

        Returns:
            Dictionary of stored data if valid, None if expired/invalid.
        """
        key = self._get_key(token)
        data = await self._redis.get(key)

        if data is None:
            return None

        try:
            return cast(dict[str, Any], orjson.loads(data))
        except orjson.JSONDecodeError:
            return None

    async def delete(self, token: str) -> bool:
        """Delete token data.

        Args:
            token: The raw token string.

        Returns:
            True if the token was successfully deleted, False if not found.
        """
        key = self._get_key(token)
        result = await self._redis.delete(key)
        return int(result) > 0 if result is not None else False
