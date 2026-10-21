"""Assertions for asynchronous Redis behavior in integration tests."""

import asyncio

import pytest
from redis.asyncio import Redis

_SERVER_CLOCK_STALL_THRESHOLD_S = 1.0


async def _read_server_time(redis: Redis) -> float:
    """Return the Redis server's wall clock as epoch seconds."""
    seconds, microseconds = await redis.time()
    return float(seconds) + float(microseconds) / 1_000_000


async def wait_for_redis_key_expiry(
    redis: Redis,
    key: str,
    *,
    timeout: float = 10.0,
    poll_interval: float = 0.05,
) -> None:
    """Wait until Redis reports that ``key`` has expired.

    Integration tests use a real Redis server, so a fixed sleep can race
    server-side expiration under load. Polling keeps the assertion bounded
    while tolerating normal scheduling and network latency.

    Expiration is driven by the Redis server clock, not the test process
    clock. If the backend clock stalls (for example a suspended VM), the key
    cannot expire no matter how long the client waits; that is an
    infrastructure condition rather than a cache-contract failure, so it is
    reported as a skip with the measured server-clock advance.
    """
    loop = asyncio.get_running_loop()
    server_time_at_start = await _read_server_time(redis)
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await redis.get(key) is None:
            return
        await asyncio.sleep(poll_interval)

    server_elapsed = await _read_server_time(redis) - server_time_at_start
    if server_elapsed < _SERVER_CLOCK_STALL_THRESHOLD_S:
        pytest.skip(
            f"Redis server clock advanced only {server_elapsed:.3f}s in "
            f"{timeout}s, so key {key} cannot expire; backend clock is stalled"
        )
    raise AssertionError(
        f"Redis key did not expire within {timeout}s: {key} "
        f"(server clock advanced {server_elapsed:.3f}s)"
    )
