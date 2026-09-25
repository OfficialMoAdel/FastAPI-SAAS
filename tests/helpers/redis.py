"""Assertions for asynchronous Redis behavior in integration tests."""

import asyncio

from redis.asyncio import Redis


async def wait_for_redis_key_expiry(
    redis: Redis,
    key: str,
    *,
    timeout: float = 5.0,
    poll_interval: float = 0.05,
) -> None:
    """Wait until Redis reports that ``key`` has expired.

    Integration tests use a real Redis server, so a fixed sleep can race
    server-side expiration under load. Polling keeps the assertion bounded
    while tolerating normal scheduling and network latency.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await redis.get(key) is None:
            return
        await asyncio.sleep(poll_interval)
    raise AssertionError(f"Redis key did not expire within {timeout}s: {key}")
