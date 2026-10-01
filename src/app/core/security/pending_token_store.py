"""Generic one-time token store backed by Redis."""

import hashlib
from typing import Any, cast

import orjson
from redis.asyncio import Redis


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

    def _claim_key(self, token: str) -> str:
        """Key holding the verification claim for a token.

        Separate from the token's own key so the claim can be released while
        the challenge itself survives a failed factor verify.
        """
        return f"{self._get_key(token)}:verify_claim"

    async def create(self, token: str, data: dict[str, Any]) -> None:
        """Store token data with TTL.

        Args:
            token: The raw token string.
            data: Dictionary of data to store with the token.
        """
        key = self._get_key(token)
        payload = orjson.dumps(data).decode("utf-8")
        await self._redis.setex(key, self.ttl, payload)

    async def consume(self, token: str) -> dict[str, Any] | None:
        """Atomically retrieve and delete token data (GETDEL).

        Args:
            token: The raw token string.

        Returns:
            Dictionary of stored data if valid, None if expired/used/invalid.
        """
        key = self._get_key(token)
        from src.app.core.utils.lua_manager import get_lua_manager

        data = await get_lua_manager().execute(self._redis, "get_delete", keys=[key])

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

    async def remaining_ttl(self, token: str) -> int:
        """Remaining TTL in seconds for a token's key.

        Returns Redis semantics: -2 when the key does not exist, -1 when it
        exists without expiry. Used by MFA IP binding (S-M3) to derive the
        token's age — must be read BEFORE :meth:`consume` (GETDEL) removes
        the key. Named ``remaining_ttl`` to avoid the ``self.ttl`` seconds
        attribute set in ``__init__``.

        Args:
            token: The raw token string.

        Returns:
            Remaining TTL in seconds, or -2/-1 per Redis TTL semantics.
        """
        return int(await self._redis.ttl(self._get_key(token)))

    async def claim(self, token: str, ttl_seconds: int, nonce: str) -> bool:
        """Take exclusive ownership of a token's verification slot.

        One atomic ``SET NX EX`` decides the winner. Returns True for the
        single request that owns the slot, False for every other request
        carrying the same token — so a loser is refused BEFORE it can mutate
        anything, which is the property a plain ``peek`` cannot give.

        The claim is deliberately NOT the token key: a failed factor verify
        releases it (see :meth:`release_claim`) so a typo costs the user their
        code attempt, not their whole pending challenge.

        Args:
            token: The raw token string.
            ttl_seconds: Claim lifetime; bounds the damage if a holder dies.
            nonce: Unguessable ownership token for compare-and-delete.

        Returns:
            True if the claim was taken, False if another request holds it.
        """
        from src.app.core.utils.lua_manager import get_lua_manager

        result = await get_lua_manager().execute(
            self._redis,
            "mfa_pending_claim",
            keys=[self._claim_key(token)],
            args=[str(ttl_seconds), nonce],
        )
        return bool(int(result)) if result is not None else False

    async def release_claim(self, token: str, nonce: str) -> bool:
        """Release a claim, but only if this caller still owns it.

        Compare-and-delete inside one script, so a caller whose claim already
        expired (and was re-taken by a *different* request) cannot delete the
        new owner's claim. That is what keeps the release path from
        reintroducing the race it exists to bound.

        Args:
            token: The raw token string.
            nonce: The ownership token passed to :meth:`claim`.

        Returns:
            True if this caller's claim was released, False otherwise.
        """
        from src.app.core.utils.lua_manager import get_lua_manager

        result = await get_lua_manager().execute(
            self._redis,
            "lock_release_if_owned",
            keys=[self._claim_key(token)],
            args=[nonce],
        )
        return bool(int(result)) if result is not None else False

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
