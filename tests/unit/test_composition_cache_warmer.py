"""Ownership contract for the startup cache-warming claim."""

import asyncio
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.app import composition

_LOCK_KEY = "system:startup:cache_warmup_lock"
_HEX_TOKEN = re.compile(r"^[0-9a-f]{32}$")


async def _run_warmer(*, warm_side_effect: Exception | None = None) -> tuple[MagicMock, AsyncMock]:
    fake_client = MagicMock()
    fake_client.set = AsyncMock(return_value=True)
    release = AsyncMock(return_value=True)
    captured: dict[str, Any] = {}

    def capture(coro: Any, name: str | None = None) -> MagicMock:
        captured["coro"] = coro
        captured["name"] = name
        return MagicMock()

    warm_caches = AsyncMock(side_effect=warm_side_effect)
    with (
        patch("src.app.composition.cache.client", fake_client),
        patch("src.app.core.cache.warmer.warm_caches", warm_caches),
        patch("src.app.core.cache.client.cache_client.release_lock_if_owned", release),
        patch("src.app.core.utils.background_task.create_safe_task", side_effect=capture),
    ):
        await composition._start_cache_warmer(asyncio.Event())
        await captured["coro"]

    return fake_client, release


async def test_warmer_claims_with_owner_token_and_releases_it() -> None:
    fake_client, release = await _run_warmer()

    token = fake_client.set.await_args.args[1]
    assert token != "1"
    assert _HEX_TOKEN.match(token), token
    fake_client.set.assert_awaited_once_with(_LOCK_KEY, token, nx=True, ex=300)
    release.assert_awaited_once_with(_LOCK_KEY, token)


async def test_warmer_releases_claim_when_warmup_fails() -> None:
    fake_client, release = await _run_warmer(warm_side_effect=RuntimeError("boom"))

    token = fake_client.set.await_args.args[1]
    release.assert_awaited_once_with(_LOCK_KEY, token)


async def test_warmer_does_not_release_when_claim_was_not_acquired() -> None:
    fake_client = MagicMock()
    fake_client.set = AsyncMock(return_value=False)
    release = AsyncMock(return_value=True)
    captured: dict[str, Any] = {}

    def capture(coro: Any, name: str | None = None) -> MagicMock:
        captured["coro"] = coro
        return MagicMock()

    with (
        patch("src.app.composition.cache.client", fake_client),
        patch("src.app.core.cache.warmer.warm_caches", AsyncMock()) as warm_caches,
        patch("src.app.core.cache.client.cache_client.release_lock_if_owned", release),
        patch("src.app.core.utils.background_task.create_safe_task", side_effect=capture),
    ):
        await composition._start_cache_warmer(asyncio.Event())
        await captured["coro"]

    warm_caches.assert_not_awaited()
    release.assert_not_awaited()
