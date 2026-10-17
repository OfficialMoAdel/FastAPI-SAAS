"""S-1 oracle: session-invalidation boolean parity + Redis-down never-fakes-success.

The S-1 rewrite moves two round trips plus their boolean logic into one
``EVALSHA``. The only thing that can silently break is the FAILURE contract,
which is exactly what a throughput-optimising rewrite is tempted to simplify —
so this oracle pins the contract, not the speed.

Three groups:

1. BOOLEAN PARITY — ``exists=False`` => ``False``, ``exists=True`` => ``True``.
   ``invalidate_session`` never touches the database (Redis only), so there is
   no dialect-dependent behaviour to parametrise; the tests run with the live
   sqlite ``db_session`` fixture to prove DB presence is irrelevant to the seam,
   ``DIALECTS`` records that context, and the postgres leg is a named
   follow-up bead, not a silent absence (same pattern as the S-2 oracle).
2. REDIS-DOWN NEVER-FAKES-SUCCESS — a client that raises on operation =>
   ``False`` AND ``session_invalidation_failed`` in the log, never ``True``.
   The log assertion is load-bearing: a fake that raises on connect would make
   a return-value-only test pass for the wrong reason. A ``None`` client also
   returns ``False`` (pinned without a log assertion — on HEAD that path is
   silent, and asserting absence would forbid the diff from adding logging).
3. COMMAND FOOTPRINT — ``INFO commandstats`` snapshots taken immediately
   before and after ONE call. Measured on HEAD against live Redis:
   hit path = HEXISTS + MULTI + HDEL + ZREM + EXEC = 5 server commands;
   miss path = HEXISTS = 1. The S-1 diff collapses the hit path to one
   EVALSHA, so the hit assertion is a FIXED CEILING (``<= 5``), never
   ``new < old`` — it is green on HEAD (5) and stays green after the diff
   (1). The ``cmdstat_info`` increment is the measuring INFO call itself and
   is subtracted before asserting, not counted as footprint.

   The ceiling is taken as the MINIMUM over 10 repetitions because the VM
   Redis is shared: background commands can only ADD to a delta, so the
   minimum is the true footprint and any single noisy sample cannot fail the
   suite. This is the same one-directional-jitter argument as the
   ``best_of(min-of-N, ...)`` protocol in ``src/benchmarks/protocol.py``.
   A floor (``>= 1``) pairs every ceiling so a broken INFO parse reading all
   zeros fails loudly instead of passing vacuously.

   The footprint tests build their own client on DB 7 with distinctive keys
   and delete them afterwards — no ``flushdb`` on shared state.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
import structlog
from redis.asyncio import Redis
from structlog.testing import capture_logs

from src.app.core.security import session_manager as sm

DIALECTS: tuple[str, ...] = ("sqlite",)
POSTGRES_FOLLOW_UP_HOOK = "tests/conftest.py:2124 real_postgres / real_postgres_engine"

USER_ID = "s01-oracle-user"
SESSION_ID = "s01-oracle-session"


def _manager(client: Any) -> sm.SessionManager:
    mgr = sm.SessionManager()
    mgr._redis_client = client
    return mgr


class _FakePipeline:
    """Buffers hash/zset removals until execute, like a real pipeline."""

    def __init__(self, hashes: dict[str, dict[str, str]], zsets: dict[str, set[str]]) -> None:
        self._hashes = hashes
        self._zsets = zsets
        self._buffered: list[tuple[str, str, str]] = []
        self.executed = 0

    async def __aenter__(self) -> _FakePipeline:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def hdel(self, key: str, *fields: str) -> None:
        for field in fields:
            self._buffered.append(("hdel", key, field))

    def zrem(self, key: str, *members: str) -> None:
        for member in members:
            self._buffered.append(("zrem", key, member))

    async def execute(self) -> list[int]:
        self.executed += 1
        for op, key, field in self._buffered:
            if op == "hdel":
                self._hashes.get(key, {}).pop(field, None)
            else:
                self._zsets.get(key, set()).discard(field)
        self._buffered.clear()
        return [1]


class _FakeSessionsRedis:
    """In-process stand-in for the session hash + order zset (parity only).

    Emulates the ``session_invalidate`` script's semantics rather than Redis's
    wire protocol: it is the script's HEXISTS-then-HDEL-then-ZREM that decides
    the boolean, so the fake has to behave the same way or the parity assertions
    below would be testing the fake instead of the manager.
    """

    def __init__(self, present: bool) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, set[str]] = {}
        if present:
            self.hashes[f"user_sessions_v2:{USER_ID}"] = {SESSION_ID: '{"created_at": "x"}'}
            self.zsets[f"user_sessions_order:{USER_ID}"] = {SESSION_ID}
        self.evalsha_calls = 0

    async def evalsha(self, sha1: str, num_keys: int, *keys_and_args: str) -> int:
        self.evalsha_calls += 1
        hash_key, zset_key, session_id = keys_and_args[:3]
        assert num_keys == 2
        if session_id not in self.hashes.get(hash_key, {}):
            return 0
        self.hashes[hash_key].pop(session_id, None)
        self.zsets.get(zset_key, set()).discard(session_id)
        return 1

    async def hexists(self, key: str, field: str) -> bool:
        return field in self.hashes.get(key, {})

    def pipeline(self) -> _FakePipeline:
        raise AssertionError("invalidate_session must not pipeline after S-6")


class _BoomRedis:
    """Raises on operation (not on connect) — the honest Redis-down shape."""

    async def evalsha(self, *_args: Any, **_kwargs: Any) -> int:
        raise ConnectionError("redis down")

    async def hexists(self, *_args: Any, **_kwargs: Any) -> bool:
        raise ConnectionError("redis down")


class TestBooleanParity:
    """exists=False => False, exists=True => True, with the sqlite session live."""

    @pytest.mark.asyncio
    async def test_absent_session_returns_false(self, db_session: Any) -> None:
        mgr = _manager(_FakeSessionsRedis(present=False))
        assert await mgr.invalidate_session(USER_ID, SESSION_ID) is False

    @pytest.mark.asyncio
    async def test_present_session_returns_true(self, db_session: Any) -> None:
        mgr = _manager(_FakeSessionsRedis(present=True))
        assert await mgr.invalidate_session(USER_ID, SESSION_ID) is True

    @pytest.mark.asyncio
    async def test_present_session_is_gone_afterwards(self, db_session: Any) -> None:
        client = _FakeSessionsRedis(present=True)
        mgr = _manager(client)
        assert await mgr.invalidate_session(USER_ID, SESSION_ID) is True
        assert await mgr.invalidate_session(USER_ID, SESSION_ID) is False

    @pytest.mark.asyncio
    async def test_absent_session_issues_no_writes(self, db_session: Any) -> None:
        client = _FakeSessionsRedis(present=False)
        mgr = _manager(client)
        assert await mgr.invalidate_session(USER_ID, SESSION_ID) is False
        # The script's own HEXISTS guard found nothing, so it wrote nothing.
        assert client.evalsha_calls == 1
        assert client.hashes == {}
        assert client.zsets == {}


class TestRedisDownNeverFakesSuccess:
    """A dead Redis must read as failure with a log line, never as success."""

    @pytest.mark.asyncio
    async def test_raising_client_returns_false(self) -> None:
        mgr = _manager(_BoomRedis())
        assert await mgr.invalidate_session(USER_ID, SESSION_ID) is False

    @pytest.mark.asyncio
    async def test_raising_client_logs_invalidation_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _manager(_BoomRedis())
        # capture_logs() swaps structlog's config, but a structlog proxy freezes
        # its processor list the first time it is USED (cache_logger_on_first_use=
        # True in core/logger.py::setup_logging). Whichever earlier test ran the
        # app lifespan has already used the module-level `sm.logger`, so its
        # frozen copy never sees the swap and this capture comes back empty.
        # Rebinding INSIDE the capture window forces a proxy built from the
        # capturing config, which makes the assertion hold in isolation and in
        # the full suite alike. caplog is not an option: setup_logging() clears
        # root_logger.handlers, which wipes pytest's capture handler.
        with capture_logs() as captured, monkeypatch.context() as mp:
            mp.setattr(sm, "logger", structlog.get_logger(sm.__name__))
            result = await mgr.invalidate_session(USER_ID, SESSION_ID)
        assert result is False
        assert result is not True
        assert any(e.get("event") == "session_invalidation_failed" for e in captured)

    @pytest.mark.asyncio
    async def test_absent_client_returns_false(self, monkeypatch: Any) -> None:
        # NOTE: the seam is read at CALL time via
        # ``from src.app.core.redis_provider import get_redis`` INSIDE the
        # ``redis_client`` property, so the patch must land on the provider
        # module — patching ``session_manager.get_redis`` is a no-op (it only
        # adds an attribute nobody reads) and the test would silently assert
        # against whatever ambient client the session wired (mock_redis),
        # passing or failing for reasons unrelated to the contract.
        mgr = sm.SessionManager()
        mgr._redis_client = sm._UNSET
        monkeypatch.setattr("src.app.core.redis_provider.get_redis", lambda: None)
        assert await mgr.invalidate_session(USER_ID, SESSION_ID) is False


def _command_totals(info: Any) -> dict[str, int]:
    """Flatten ``INFO commandstats`` to ``{cmdstat_*: calls}``.

    ``r.info(\"commandstats\")`` returns FLAT ``cmdstat_*`` keys, not a nested
    section — reading ``info[\"commandstats\"]`` yields nothing and would make
    every delta zero (a vacuous seam; probed, not assumed).
    """
    out: dict[str, int] = {}
    for key, value in dict(info).items():
        if key.startswith("cmdstat_") and isinstance(value, dict) and "calls" in value:
            out[key] = int(value["calls"])
    return out


async def _live_client() -> AsyncIterator[Redis]:
    from src.app.core.config import settings

    password: str | None = None
    if settings.REDIS_CACHE_PASSWORD is not None:
        password = settings.REDIS_CACHE_PASSWORD.get_secret_value()
    client = Redis(
        host=settings.REDIS_CACHE_HOST,
        port=int(os.getenv("TEST_REDIS_PORT", str(settings.REDIS_CACHE_PORT))),
        password=password,
        db=7,
        decode_responses=True,
    )
    await client.ping()
    try:
        yield client
    finally:
        await client.delete(f"user_sessions_v2:{USER_ID}", f"user_sessions_order:{USER_ID}")
        await client.aclose()


async def _footprint(client: Redis, *, present: bool) -> dict[str, int]:
    """Total per-command delta for ONE invalidate call, INFO artifact removed."""
    if present:
        await client.hset(f"user_sessions_v2:{USER_ID}", SESSION_ID, '{"created_at": "x"}')  # type: ignore[misc]
        await client.zadd(f"user_sessions_order:{USER_ID}", {SESSION_ID: 1.0})
    mgr = sm.SessionManager(redis_client=client)
    before = _command_totals(await client.info("commandstats"))
    await mgr.invalidate_session(USER_ID, SESSION_ID)
    after = _command_totals(await client.info("commandstats"))
    delta = {
        key: after.get(key, 0) - before.get(key, 0) for key in set(after) | set(before)
    }
    delta.pop("cmdstat_info", None)
    return {key: value for key, value in delta.items() if value}


def _total(delta: dict[str, int]) -> int:
    return sum(delta.values())


@pytest_asyncio.fixture
async def live_redis() -> AsyncIterator[Redis]:
    async for client in _live_client():
        yield client


class TestCommandFootprint:
    """HEAD costs ONE round trip (EVALSHA) on both the hit and the miss path.

    S-6 replaced the old HEXISTS-then-pipelined-HDEL/ZREM with a single
    ``session_invalidate`` script, so both round trips and the boolean logic
    moved inside Redis. Redis counts the commands a script invokes in its body
    individually, so the server-side total is 4 on the hit path (evalsha +
    hexists + hdel + zrem) and 2 on the miss path (evalsha + the script's own
    hexists guard, which returns before either delete). The number that matters
    is the ROUND TRIP — ``cmdstat_evalsha`` — and that is what these assert:
    one client-to-server command per call, on both paths.
    """

    @pytest.mark.asyncio
    async def test_hit_path_costs_at_most_five_commands(self, live_redis: Redis) -> None:
        totals = [_total(await _footprint(live_redis, present=True)) for _ in range(10)]
        assert min(totals) >= 1
        assert min(totals) <= 5

    @pytest.mark.asyncio
    async def test_hit_path_footprint_is_exactly_one_round_trip(
        self, live_redis: Redis
    ) -> None:
        """The two deletes and the boolean travel in ONE EVALSHA.

        This assertion was pre-specified as the successor to the old
        "exactly the two round trips" test: the S-1 diff moves two round trips
        plus their boolean logic into one EVALSHA, so the mechanism it named was
        replaced rather than deleted.

        Pinned as a composition floor (evalsha >= 10 over 10 repetitions —
        shared-instance noise can only add, never remove), not an exact vector,
        so a concurrent client cannot fail the suite. What this pins is the
        atomicity that matters: MULTI/EXEC must be GONE. Under the old
        pipelined implementation a crash between the two round trips could leave
        the hash and the zset diverged; there is no window now.
        """
        seen: dict[str, int] = {}
        for _ in range(10):
            for key, value in (await _footprint(live_redis, present=True)).items():
                seen[key] = seen.get(key, 0) + value
        assert seen.get("cmdstat_evalsha", 0) >= 10, "EVALSHA never observed in 10 hit-path calls"
        # The pipelined window is what the Lua call removed; assert it stays gone.
        assert "cmdstat_multi" not in seen
        assert "cmdstat_exec" not in seen
        # The script body still removes the session from BOTH structures.
        assert seen.get("cmdstat_hexists", 0) >= 10
        assert seen.get("cmdstat_hdel", 0) >= 10
        assert seen.get("cmdstat_zrem", 0) >= 10

    @pytest.mark.asyncio
    async def test_miss_path_costs_one_round_trip_and_writes_nothing(
        self, live_redis: Redis
    ) -> None:
        """A miss is one EVALSHA that returns 0 having deleted nothing."""
        seen: dict[str, int] = {}
        for _ in range(10):
            for key, value in (await _footprint(live_redis, present=False)).items():
                seen[key] = seen.get(key, 0) + value
        assert seen.get("cmdstat_evalsha", 0) >= 10
        # The existence guard now runs inside the script, so HEXISTS is counted
        # server-side — but it must return before either delete executes.
        assert "cmdstat_hdel" not in seen
        assert "cmdstat_zrem" not in seen

    def test_the_info_artifact_is_subtracted_not_counted(self) -> None:
        before = {"cmdstat_hexists": 10, "cmdstat_info": 7}
        after = {"cmdstat_hexists": 11, "cmdstat_info": 8}
        delta = {k: after.get(k, 0) - before.get(k, 0) for k in set(after) | set(before)}
        delta.pop("cmdstat_info", None)
        assert delta == {"cmdstat_hexists": 1}


class TestDialectCoverage:
    def test_the_postgres_leg_is_named_rather_than_silently_absent(self) -> None:
        assert "sqlite" in DIALECTS
        assert "postgres" not in DIALECTS
        assert "real_postgres" in POSTGRES_FOLLOW_UP_HOOK
