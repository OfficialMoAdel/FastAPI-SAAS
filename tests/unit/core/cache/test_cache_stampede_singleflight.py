"""Cache stampede protection: single-flight, Lua wiring, and hit-rate metrics.

Bead ``FastAPI_saas-nrgi.4``. Two reports contradicted each other about the
``xfetch_get_with_ttl`` Lua script: one said it was registered and never
invoked, the other said it was wired at ``decorator._try_get_cached`` and
``stampede.get_with_stampede_protection``. Reading the source settles the
first half — the script *is* invoked by the XFetch reader — but the second
half was only half true: the decorator ran it on a cache hit and nothing
else, so on a miss every concurrent caller fell through to the handler
together. The stampede the module exists to prevent was unguarded.

These tests pin the properties that make the wiring worth anything:

* concurrent misses on ONE key produce exactly ONE backend fetch
  (single-flight — the only assertion that really matters here),
* the Lua script is actually invoked, by a spy on the real manager, so a
  silent regression to the Python two-round-trip path fails here,
* the hit-rate metric is emitted, or the optimisation is unmaintainable,
* distinct keys are still fetched concurrently (single-flight must not
  serialise the whole cache), and
* a Lua failure falls back to a normal fetch instead of failing the request.

The Redis double emulates the scripts by SHA1, the same dispatch the real
``LuaScriptManager`` uses, so the script bodies' contracts and the call
sites are both exercised without a live server. ``test_xfetch_lua.py``
covers the reader's flag-gated path; this file covers what the endpoint
cache actually does under concurrency.
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import orjson
import pytest

from src.app.core.monitoring import get_r1_metrics, reset_r1_metrics
from src.app.core.tenancy.context import (
    TenantContext,
    clear_tenant_context,
    set_current_tenant,
)
from src.app.core.utils import cache as cache_state
from src.app.core.utils.cache import decorator as deco
from src.app.core.utils.cache import get_with_stampede_protection
from src.app.core.utils.cache.decorator import cache
from src.app.core.utils.lua_manager import get_lua_manager

TENANT_A = "11111111-1111-1111-1111-111111111111"

# Callers contending on one key. Well above "first caller wins by luck".
CONTENDERS = 8


class _Pipe:
    """Buffers commands like redis-py's pipeline, applies them on execute()."""

    def __init__(self, client: ScriptedRedis) -> None:
        self._client = client
        self._queued: list[tuple[str, tuple[Any, ...]]] = []

    def setex(self, key: str, seconds: int, value: Any) -> None:  # noqa: ANN401
        self._queued.append(("setex", (key, seconds, value)))

    def sadd(self, key: str, *values: Any) -> None:  # noqa: ANN401
        self._queued.append(("sadd", (key, *values)))

    def expire(self, key: str, seconds: int) -> None:  # noqa: ANN401
        self._queued.append(("expire", (key, seconds)))

    async def execute(self) -> list[Any]:
        return [self._client._apply(name, args) for name, args in self._queued]


class ScriptedRedis:
    """In-memory Redis double with atomic SET NX EX and SHA-routed Lua.

    Every coroutine body below runs without an intervening ``await``, which
    is what real single-threaded Redis gives us: a SET NX either wins or
    loses, never both. That atomicity is precisely what a read-then-claim
    implemented in Python lacks, and why the guard lives in the script.
    """

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expiry: dict[str, float] = {}
        #: script names whose execution must raise (fallback counter-tests)
        self.failing_scripts: set[str] = set()
        #: count of reads that reached the Lua layer
        self.reads = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    def _now(self) -> float:
        assert self._loop is not None
        return self._loop.time()

    def _apply(self, name: str, args: tuple[Any, ...]) -> Any:
        if name == "setex":
            key, seconds, value = args
            self.data[key] = value
            self.expiry[key] = self._now() + float(seconds)
            return True
        if name == "sadd":
            key, *values = args
            self.data.setdefault(key, "")
            return len(values)
        if name == "expire":
            return True
        raise AssertionError(f"unexpected pipeline op {name!r}")

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def ttl(self, key: str) -> int:
        if key not in self.data:
            return -2
        return int(self.expiry.get(key, 0.0) - self._now())

    async def set(self, key: str, value: Any, *, nx: bool = False, ex: int | None = None) -> bool:  # noqa: ANN401, FBT001, FBT002
        if nx and key in self.data:
            return False
        self.data[key] = value
        if ex is not None:
            self.expiry[key] = self._now() + float(ex)
        return True

    async def setex(self, key: str, seconds: int, value: Any) -> bool:  # noqa: ANN401
        self._apply("setex", (key, seconds, value))
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += self.data.pop(key, None) is not None
            self.expiry.pop(key, None)
        return removed

    async def scan(self, cursor: int = 0, match: str = "*", count: int | None = None) -> tuple[int, list[str]]:  # noqa: ARG002
        import fnmatch

        return 0, [k for k in self.data if fnmatch.fnmatch(k, match)]

    def pipeline(self, **kwargs: Any) -> Any:  # noqa: ARG002, ANN401
        @asynccontextmanager
        async def cm() -> Any:
            yield _Pipe(self)

        return cm()

    async def evalsha(self, sha: str, numkeys: int, *keys_and_args: Any) -> Any:  # noqa: ARG002
        manager = get_lua_manager()
        # BUILTIN_SCRIPTS holds raw sources; LuaScriptManager normalises them
        # before hashing, so resolve the digest through get_script().
        name = next((n for n in manager.BUILTIN_SCRIPTS if manager.get_script(n).sha1 == sha), None)
        if name is None:
            raise RuntimeError(f"unknown script sha {sha}")
        if name in self.failing_scripts:
            raise RuntimeError(f"script {name} exploded")
        if name == "xfetch_get_or_claim":
            self.reads += 1
            cache_key, lock_key = keys_and_args[0], keys_and_args[1]
            lock_ttl, token = keys_and_args[2], keys_and_args[3]
            value = self.data.get(cache_key)
            if value is not None:
                return [value, await self.ttl(cache_key), 0]
            if lock_key in self.data:
                return ["", -2, 0]
            self.data[lock_key] = token
            self.expiry[lock_key] = self._now() + float(lock_ttl)
            return ["", -2, 1]

        if name == "xfetch_get_with_ttl":
            self.reads += 1
            value = self.data.get(keys_and_args[0])
            return ["", -2] if value is None else [value, await self.ttl(keys_and_args[0])]

        if name == "lock_release_if_owned":
            lock_key, token = keys_and_args[0], keys_and_args[1]
            if self.data.get(lock_key) == token:
                del self.data[lock_key]
                self.expiry.pop(lock_key, None)
                return 1
            return 0

        raise RuntimeError(f"script {name} is not emulated")

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:  # noqa: ARG002
        raise RuntimeError("scripts are preloaded; evalsha should win")

    async def script_load(self, source: str) -> str:
        return hashlib.sha1(source.encode(), usedforsecurity=False).hexdigest()


@pytest.fixture
def redis_double() -> ScriptedRedis:
    double = ScriptedRedis()
    double._loop = asyncio.get_event_loop()
    return double


@pytest.fixture(autouse=True)
def _tenant_and_clean_metrics():
    set_current_tenant(TenantContext(tenant_id=TENANT_A, tenant_slug="t", tenant_name="t", is_active=True))
    reset_r1_metrics()
    yield
    clear_tenant_context()
    reset_r1_metrics()


@pytest.fixture
def short_wait() -> Any:
    """Shrink the dedupe wait so the fallback counter-tests stay fast."""
    with patch.object(deco, "_REFRESH_WAIT_TIMEOUT", 0.3):
        yield


def _bind(client: ScriptedRedis) -> Any:
    return patch("src.app.core.utils.cache.client", client)


def _request() -> SimpleNamespace:
    return SimpleNamespace(method="GET", state=SimpleNamespace(), scope={})


def _body(response: Any) -> Any:
    return orjson.loads(response.body)


def _endpoint(gate: asyncio.Event | None = None, delay: float = 0.0) -> Any:
    """A ``@cache`` GET endpoint plus a counter of backend executions."""
    fetches: list[int] = []

    @cache(key_prefix="report", resource_id_name="item_id", expiration=60)
    async def read_item(request, item_id: int):  # noqa: ANN001, ANN202
        fetches.append(item_id)
        if delay:
            await asyncio.sleep(delay)
        if gate is not None:
            # Hold the handler open for the whole test window, so every
            # contender is provably in flight at the same moment — this is
            # exactly the window a stampede exploits.
            await asyncio.wait_for(gate.wait(), timeout=10.0)
        return {"item_id": item_id, "payload": "computed"}

    return read_item, fetches


# --- primary property: single-flight ------------------------------------


async def test_concurrent_misses_on_one_key_trigger_exactly_one_fetch(redis_double: ScriptedRedis) -> None:
    """The property stampede protection exists for: N concurrent misses, 1 fetch.

    The count is asserted *while the winner is still computing*, so an
    implementation that merely finishes fast enough to hide the duplication
    cannot pass.
    """
    gate = asyncio.Event()
    read_item, fetches = _endpoint(gate=gate)

    with _bind(redis_double):
        tasks = [asyncio.create_task(read_item(_request(), item_id=1)) for _ in range(CONTENDERS)]
        # Every caller must have reached the read layer before any answer can
        # exist, so all of them contend on the same empty key.
        while redis_double.reads < CONTENDERS:
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.05)
        assert len(fetches) == 1, f"expected 1 backend fetch while others are waiting, got {len(fetches)}"

        gate.set()
        responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20.0)

    assert len(fetches) == 1, f"expected 1 backend fetch for {CONTENDERS} concurrent misses, got {len(fetches)}"
    assert [_body(r) for r in responses] == [{"item_id": 1, "payload": "computed"}] * CONTENDERS


async def test_single_flight_dedupes_onto_the_winner_value(redis_double: ScriptedRedis) -> None:
    """A guard that computed N divergent values would fail this even if a
    count-only assertion somehow passed: every caller is served the winner's
    bytes, and the value is published for later readers."""
    gate = asyncio.Event()
    read_item, fetches = _endpoint(gate=gate)

    with _bind(redis_double):
        tasks = [asyncio.create_task(read_item(_request(), item_id=7)) for _ in range(CONTENDERS)]
        while redis_double.reads < CONTENDERS:
            await asyncio.sleep(0.001)
        gate.set()
        responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20.0)
        # A later, non-contending reader is served from the published entry.
        later = await read_item(_request(), item_id=7)

    assert len(fetches) == 1
    assert len({bytes(r.body) for r in responses}) == 1
    assert _body(later) == {"item_id": 7, "payload": "computed"}
    assert len(fetches) == 1, "the published entry was not reused"


async def test_refresh_claim_is_released_after_publishing(redis_double: ScriptedRedis) -> None:
    """A claim leaked past its own write would make the next miss wait the
    full timeout instead of refreshing, turning protection into an outage."""
    read_item, fetches = _endpoint()

    with _bind(redis_double):
        await read_item(_request(), item_id=1)
        # Invalidate, then miss again: a stale claim would block this.
        await redis_double.delete(f"tenant:{TENANT_A}:v2:report:1")
        await asyncio.wait_for(read_item(_request(), item_id=1), timeout=5.0)

    assert len(fetches) == 2, "the second miss did not refresh; a claim was leaked"


# --- Lua wiring ----------------------------------------------------------


async def test_lua_script_is_invoked_on_the_read_path(redis_double: ScriptedRedis) -> None:
    """A spy wraps the REAL manager, so a silent fall back to GET+TTL in
    Python (which is what made the miss path unguarded) fails here."""
    manager = get_lua_manager()
    read_item, _ = _endpoint()

    with (
        _bind(redis_double),
        patch.object(manager, "execute", wraps=manager.execute) as spy,
    ):
        await read_item(_request(), item_id=1)
        await read_item(_request(), item_id=1)

    invoked = [c.args[1] for c in spy.call_args_list]
    assert invoked, "no Lua script was invoked; the read path regressed to plain GET+TTL"
    assert "xfetch_get_or_claim" in invoked, f"the atomic read+claim script was not used; invoked {invoked}"


async def test_xfetch_reader_still_uses_its_registered_script(redis_double: ScriptedRedis) -> None:
    """The bead's original question: is ``xfetch_get_with_ttl`` wired, or just
    registered? It is invoked by the XFetch reader when its flag is on, and
    that flag defaults OFF — which is why the decorator must not rely on it."""
    manager = get_lua_manager()
    flags = cache_state.settings.r1_unified
    assert flags.XFETCH_USE_LUA_SCRIPT is False, "flag default changed; this test must follow it"

    with (
        _bind(redis_double),
        patch.object(flags, "XFETCH_USE_LUA_SCRIPT", True),
        patch.object(manager, "execute", wraps=manager.execute) as spy,
    ):
        await get_with_stampede_protection("reader:key", lambda: asyncio.sleep(0, result={"a": 1}), ttl=60)

    invoked = [c.args[1] for c in spy.call_args_list]
    assert "xfetch_get_with_ttl" in invoked, f"xfetch_get_with_ttl is registered but never invoked; got {invoked}"


# --- hit-rate metric -----------------------------------------------------


async def test_hit_rate_metric_is_emitted_for_hits_and_misses(redis_double: ScriptedRedis) -> None:
    """Without the metric the optimisation is unmaintainable — it cannot be
    shown to work, so nobody can tell when it stops working. /api/metrics
    publishes ``xfetch_hit_rate`` from exactly these counters."""
    metrics = get_r1_metrics()
    read_item, _ = _endpoint()

    with _bind(redis_double):
        await read_item(_request(), item_id=1)  # miss
        await read_item(_request(), item_id=1)  # hit
        await read_item(_request(), item_id=2)  # miss on a different key

    assert metrics.get_counter("r1_xfetch_cache_misses_total") == 2, "cache misses are not counted"
    assert metrics.get_counter("r1_xfetch_cache_hits_total") == 1, "cache hits are not counted"
    assert metrics.get_histogram("r1_xfetch_latency_seconds")["count"] == 3, "latency observations are not recorded"


# --- counter-tests: the guard must not over-reach -----------------------


async def test_distinct_keys_are_fetched_concurrently(redis_double: ScriptedRedis) -> None:
    """Single-flight on one key must not serialise the whole cache."""
    inflight = 0
    peak = 0

    @cache(key_prefix="report", resource_id_name="item_id", expiration=60)
    async def read_item(request, item_id: int):  # noqa: ANN001, ANN202
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.2)
        inflight -= 1
        return {"item_id": item_id}

    with _bind(redis_double):
        await asyncio.wait_for(
            asyncio.gather(*(read_item(_request(), item_id=i) for i in range(4))),
            timeout=20.0,
        )

    assert peak > 1, f"distinct keys were serialised (peak concurrent fetches = {peak})"


async def test_lua_failure_falls_back_to_a_normal_fetch(redis_double: ScriptedRedis, short_wait: None) -> None:
    """A broken or missing script must degrade to a served request, never a
    500: the handler still runs and the value is still cached."""
    redis_double.failing_scripts = {"xfetch_get_or_claim", "xfetch_get_with_ttl"}
    read_item, fetches = _endpoint()

    with _bind(redis_double):
        response = await asyncio.wait_for(read_item(_request(), item_id=1), timeout=20.0)

    assert len(fetches) == 1
    assert _body(response) == {"item_id": 1, "payload": "computed"}


async def test_lua_failure_does_not_recompute_a_warm_entry(redis_double: ScriptedRedis, short_wait: None) -> None:
    """The error path must not turn a warm cache into repeated recomputation."""
    redis_double.failing_scripts = {"xfetch_get_or_claim", "xfetch_get_with_ttl"}
    read_item, fetches = _endpoint()

    with _bind(redis_double):
        warm = await read_item(_request(), item_id=1)
        again = await asyncio.wait_for(read_item(_request(), item_id=1), timeout=20.0)

    assert len(fetches) == 1, "a cached value was recomputed while the script was failing"
    assert _body(again) == _body(warm)
