"""The NOSCRIPT fallback must never re-execute a script that may have run."""

from unittest.mock import AsyncMock

import pytest
from redis.exceptions import NoScriptError, RedisError

from src.app.core.utils.lua_manager import LuaScriptManager
from src.app.core.utils.retry import LuaScriptError


@pytest.fixture
def manager() -> LuaScriptManager:
    return LuaScriptManager()


@pytest.fixture
def redis() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_reload_then_retry_success_does_not_eval(manager: LuaScriptManager, redis: AsyncMock) -> None:
    script = manager.get_script("xfetch_get_with_ttl")
    redis.evalsha.side_effect = [NoScriptError("No matching script"), ["value", 10]]
    redis.script_load.return_value = script.sha1

    result = await manager.execute(redis, "xfetch_get_with_ttl")

    assert result == ["value", 10]
    assert redis.evalsha.await_count == 2
    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_error_after_reload_is_not_retried_with_eval(
    manager: LuaScriptManager, redis: AsyncMock
) -> None:
    script = manager.get_script("xfetch_get_with_ttl")
    redis.evalsha.side_effect = [NoScriptError("No matching script"), ConnectionError("connection lost")]
    redis.script_load.return_value = script.sha1

    with pytest.raises(LuaScriptError, match="after reload"):
        await manager.execute(redis, "xfetch_get_with_ttl")

    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_script_evicted_again_falls_back_to_eval(manager: LuaScriptManager, redis: AsyncMock) -> None:
    script = manager.get_script("xfetch_get_with_ttl")
    redis.evalsha.side_effect = [
        NoScriptError("No matching script"),
        NoScriptError("No matching script"),
    ]
    redis.script_load.return_value = script.sha1
    redis.eval.return_value = ["value", 10]

    result = await manager.execute(redis, "xfetch_get_with_ttl")

    assert result == ["value", 10]
    redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_failure_is_not_papered_over_with_eval(manager: LuaScriptManager, redis: AsyncMock) -> None:
    redis.evalsha.side_effect = NoScriptError("No matching script")
    redis.script_load.side_effect = RedisError("load failed")

    with pytest.raises(LuaScriptError, match="Failed to load script"):
        await manager.execute(redis, "xfetch_get_with_ttl")

    redis.eval.assert_not_awaited()
