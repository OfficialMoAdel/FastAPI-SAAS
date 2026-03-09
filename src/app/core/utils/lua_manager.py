"""
Lua Script Manager for R1-UNIFIED Optimizations.

Provides centralized management of Redis Lua scripts with:
- Script registration with SHA1 tracking
- Preloading on startup (SCRIPT LOAD)
- Automatic EVALSHA with NOSCRIPT fallback
- Error handling and logging

Usage:
    lua_manager = get_lua_manager()
    await lua_manager.preload_scripts(redis_client)
    result = await lua_manager.execute(
        redis_client,
        "idempotency_check",
        keys=["key1"],
        args=["arg1", "arg2"]
    )
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.app.core.logger import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)


class LuaScriptError(Exception):
    """Error executing Lua script."""

    pass


class ScriptNotFoundError(LuaScriptError):
    """Script not registered in manager."""

    pass


@dataclass
class LuaScript:
    """Registered Lua script with metadata."""

    name: str
    source: str
    sha1: str = field(default="", init=False)
    loaded: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Calculate SHA1 hash of script source."""
        self.sha1 = hashlib.sha1(self.source.encode()).hexdigest()


class LuaScriptManager:
    """Manages Redis Lua scripts with preloading and automatic fallback.

    This manager provides:
    1. Script registration with automatic SHA1 calculation
    2. Batch preloading on startup (SCRIPT LOAD)
    3. EVALSHA execution with automatic NOSCRIPT fallback
    4. Centralized error handling and logging
    """

    # Pre-defined scripts for R1-UNIFIED optimizations
    BUILTIN_SCRIPTS = {
        # P1-1: Event Bus Idempotency - Atomic check-and-set
        "idempotency_check": """
            -- KEYS[1]: idempotency key
            -- ARGV[1]: TTL in seconds
            -- ARGV[2]: value (usually "1")
            -- Returns: 1 if key was set (first time), 0 if already exists
            local result = redis.call('SETNX', KEYS[1], ARGV[2])
            if result == 1 then
                redis.call('EXPIRE', KEYS[1], ARGV[1])
            end
            return result
        """,
        # P2-2: XFetch - Atomic GET + TTL in single roundtrip
        "xfetch_get_with_ttl": """
            -- KEYS[1]: cache key
            -- Returns: [value, ttl] or [nil, -2] if not exists
            local value = redis.call('GET', KEYS[1])
            if value == false then
                return {false, -2}
            end
            local ttl = redis.call('TTL', KEYS[1])
            return {value, ttl}
        """,
        # P3-1: DLQ Bounded Size - LPUSH with LTRIM
        "dlq_bounded_push": """
            -- KEYS[1]: DLQ list key
            -- ARGV[1]: max size
            -- ARGV[2]: JSON value to push
            -- Returns: new length after trim
            redis.call('LPUSH', KEYS[1], ARGV[2])
            redis.call('LTRIM', KEYS[1], 0, tonumber(ARGV[1]) - 1)
            return redis.call('LLEN', KEYS[1])
        """,
        # P5: RBAC Permission Cache - Get with metadata
        "rbac_cache_get": """
            -- KEYS[1]: permissions hash key
            -- ARGV[1]: user_id field
            -- Returns: [value, ttl] or [nil, -2]
            local value = redis.call('HGET', KEYS[1], ARGV[1])
            if value == false then
                return {false, -2}
            end
            local ttl = redis.call('TTL', KEYS[1])
            return {value, ttl}
        """,
        # Session eviction helper - Atomic ZPOPMIN + HDEL
        "session_evict_oldest": """
            -- KEYS[1]: session order zset key
            -- KEYS[2]: session hash key
            -- ARGV[1]: count to evict
            -- Returns: list of evicted session IDs
            local evicted = {}
            local to_evict = redis.call('ZPOPMIN', KEYS[1], tonumber(ARGV[1]))
            for i = 1, #to_evict, 2 do
                local session_id = to_evict[i]
                redis.call('HDEL', KEYS[2], session_id)
                table.insert(evicted, session_id)
            end
            return evicted
        """,
    }

    def __init__(self) -> None:
        """Initialize the Lua script manager."""
        self._scripts: dict[str, LuaScript] = {}
        self._preloaded: bool = False

        # Register built-in scripts
        for name, source in self.BUILTIN_SCRIPTS.items():
            self.register(name, source)

    def register(self, name: str, source: str) -> LuaScript:
        """Register a Lua script.

        Args:
            name: Unique script identifier
            source: Lua script source code

        Returns:
            LuaScript: Registered script object
        """
        # Normalize whitespace for consistent SHA1
        normalized_source = "\n".join(line.strip() for line in source.strip().splitlines())
        script = LuaScript(name=name, source=normalized_source)
        self._scripts[name] = script
        logger.debug("Registered Lua script", name=name, sha1=script.sha1[:8])
        return script

    def get_script(self, name: str) -> LuaScript:
        """Get a registered script by name.

        Args:
            name: Script identifier

        Returns:
            LuaScript: The registered script

        Raises:
            ScriptNotFoundError: If script is not registered
        """
        if name not in self._scripts:
            raise ScriptNotFoundError(f"Script '{name}' not registered")
        return self._scripts[name]

    async def preload_scripts(self, redis: Redis) -> dict[str, bool]:
        """Preload all registered scripts into Redis.

        This uses SCRIPT LOAD to cache scripts server-side,
        enabling faster EVALSHA execution.

        Args:
            redis: Redis client

        Returns:
            dict[str, bool]: Script name -> success status
        """
        results: dict[str, bool] = {}

        for name, script in self._scripts.items():
            try:
                # SCRIPT LOAD returns the SHA1 hash
                loaded_sha = await redis.script_load(script.source)

                # Verify SHA1 matches
                if loaded_sha != script.sha1:
                    logger.warning("Script SHA1 mismatch", name=name, expected=script.sha1[:8], got=loaded_sha[:8])
                    # Update to server's SHA1
                    script.sha1 = loaded_sha

                script.loaded = True
                results[name] = True
                logger.debug("Preloaded script ''", name=name)

            except Exception as e:
                logger.error("Failed to preload script '':", name=name, error=e)
                results[name] = False

        self._preloaded = True
        loaded_count = sum(results.values())
        logger.info("Preloaded / Lua scripts", loaded_count=loaded_count, val=len(self._scripts))

        return results

    async def execute(
        self,
        redis: Redis,
        script_name: str,
        keys: list[str] | None = None,
        args: list[str] | None = None,
    ) -> Any:
        """Execute a registered Lua script.

        Uses EVALSHA for efficiency, with automatic NOSCRIPT fallback.

        Args:
            redis: Redis client
            script_name: Name of registered script
            keys: KEYS arguments for the script
            args: ARGV arguments for the script

        Returns:
            Script execution result

        Raises:
            ScriptNotFoundError: If script is not registered
            LuaScriptError: If script execution fails
        """
        script = self.get_script(script_name)
        keys = keys or []
        args = args or []

        try:
            # Try EVALSHA first (faster if script is cached)
            result = await redis.evalsha(  # type: ignore[misc]
                script.sha1,
                len(keys),
                *keys,
                *args,
            )
            return result

        except Exception as e:
            error_str = str(e).upper()

            # Handle NOSCRIPT error - script not in Redis cache
            if "NOSCRIPT" in error_str:
                logger.debug("Script '' not cached, falling back to EVAL", script_name=script_name)
                try:
                    result = await redis.eval(  # type: ignore[misc]
                        script.source,
                        len(keys),
                        *keys,
                        *args,
                    )
                    # Mark as needing reload
                    script.loaded = False
                    return result

                except Exception as eval_error:
                    raise LuaScriptError(f"EVAL fallback failed for '{script_name}': {eval_error}") from eval_error

            # Other Redis errors
            raise LuaScriptError(f"Script '{script_name}' execution failed: {e}") from e

    async def execute_or_fallback(
        self,
        redis: Redis,
        script_name: str,
        keys: list[str] | None = None,
        args: list[str] | None = None,
        fallback_fn: Any | None = None,
    ) -> Any:
        """Execute script with optional Python fallback.

        Args:
            redis: Redis client
            script_name: Name of registered script
            keys: KEYS arguments
            args: ARGV arguments
            fallback_fn: Async function to call if script fails

        Returns:
            Script result or fallback result
        """
        try:
            return await self.execute(redis, script_name, keys, args)
        except LuaScriptError as e:
            if fallback_fn is not None:
                logger.warning("Script '' failed, using fallback:", script_name=script_name, error=e)
                return await fallback_fn()
            raise

    def is_preloaded(self) -> bool:
        """Check if scripts have been preloaded."""
        return self._preloaded

    def get_script_names(self) -> list[str]:
        """Get list of registered script names."""
        return list(self._scripts.keys())

    def get_stats(self) -> dict[str, Any]:
        """Get manager statistics."""
        return {
            "total_scripts": len(self._scripts),
            "preloaded": self._preloaded,
            "scripts": {
                name: {
                    "sha1": script.sha1[:8],
                    "loaded": script.loaded,
                    "source_lines": len(script.source.splitlines()),
                }
                for name, script in self._scripts.items()
            },
        }


# Singleton instance
_lua_manager: LuaScriptManager | None = None


def get_lua_manager() -> LuaScriptManager:
    """Get the singleton Lua script manager.

    Returns:
        LuaScriptManager: The shared manager instance
    """
    global _lua_manager
    if _lua_manager is None:
        _lua_manager = LuaScriptManager()
    return _lua_manager


def reset_lua_manager() -> None:
    """Reset the singleton manager (for testing)."""
    global _lua_manager
    _lua_manager = None
