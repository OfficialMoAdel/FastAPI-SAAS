"""Concurrency and invalidation contracts for the tenant-JSONB mutators.

Three defects, all pre-existing and all in ``update_tenant_flag`` /
``delete_tenant_flag`` / ``bulk_update_tenant_flags``:

1. The mutators read the Tenant row UNLOCKED and then did a
   read-modify-write on the whole ``feature_flags`` column. Two concurrent
   writes for different flags on the same tenant both loaded the same dict
   and the second commit silently discarded the first flag;
   ``delete_tenant_flag`` had the same window as check-then-act.
2. They committed and then invalidated with no lock at all, while
   ``get_tenant_flags``'s ``_load()`` holds the per-key singleflight lock
   across its ``db.get`` + cache write. A reader that loaded the PRE-commit
   JSONB could therefore write it back AFTER the writer's eviction — stale
   and un-evictable for CACHE_TTL.
3. ``bulk_update_tenant_flags`` evicted ``features`` + ``feature:{name}``
   but not ``feature_value:{name}``, so ``get_flag_value`` served the
   pre-bulk value for CACHE_TTL.

The fakes below model the two database behaviours the fix rests on and the
bug rested on: ``get`` hands back a SNAPSHOT of the persisted row (as a
real session does for a row it has not loaded), and ``with_for_update=True``
serializes the read against ``commit`` the way ``SELECT ... FOR UPDATE``
serializes on the engine — the second reader blocks until the first
commits and is then handed the committed state. ``commit`` yields once to
reproduce the round-trip window a concurrent writer can interleave into.
"""

import asyncio
from contextvars import ContextVar
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.core.utils.cache import build_tenant_key
from src.app.modules.feature_flags import service as ff_service
from src.app.modules.feature_flags.service import FeatureFlagService

TENANT = uuid4()


class FakeTenantSession:
    """AsyncSession stand-in modelling the row-lock and snapshot rules."""

    def __init__(self, initial: dict | None = None, *, honour_for_update: bool = True) -> None:
        self.rows: dict[object, dict] = {TENANT: dict(initial or {})}
        self.honour_for_update = honour_for_update
        self.commits = 0
        self.locked_reads = 0
        self.contended_reads = 0
        self.unlocked_reads = 0
        self._row_lock = asyncio.Lock()
        # Task-local: each gather() child gets its own context copy, so the
        # "snapshot I am about to commit" never leaks between writers.
        self._held: ContextVar[MagicMock] = ContextVar("held_tenant")
        self._held.set(None)  # type: ignore[arg-type]
        self.pause_next_read = False
        self.read_paused: asyncio.Event | None = None
        self.resume_read: asyncio.Event | None = None

    def _snapshot(self, tenant_id) -> MagicMock:
        tenant = MagicMock()
        tenant.id = tenant_id
        tenant.feature_flags = dict(self.rows.setdefault(tenant_id, {}))
        return tenant

    async def get(self, entity, ident, **kwargs) -> MagicMock:
        locked = bool(kwargs.get("with_for_update"))
        if locked and self.honour_for_update:
            if self._row_lock.locked():
                self.contended_reads += 1
            await self._row_lock.acquire()
            self.locked_reads += 1
        else:
            self.unlocked_reads += 1
            await asyncio.sleep(0)
        tenant = self._snapshot(ident)
        self._held.set(tenant)
        if self.pause_next_read:
            self.pause_next_read = False
            assert self.read_paused is not None
            self.read_paused.set()
            assert self.resume_read is not None
            await self.resume_read.wait()
        return tenant

    async def commit(self) -> None:
        await asyncio.sleep(0)  # the round-trip window
        held = self._held.get()
        if held is not None:
            self.rows[held.id] = dict(held.feature_flags)
        self.commits += 1
        if self._row_lock.locked():
            self._row_lock.release()

    async def refresh(self, obj) -> None:
        return None


class FakeCache:
    """Redis-shaped dict cache with working get/set/delete."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.deletes: list[tuple[str, ...]] = []

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ttl=None):
        self.data[key] = value

    async def delete(self, *keys):
        self.deletes.append(keys)
        for key in keys:
            self.data.pop(key, None)

    @property
    def client(self):
        return None


@pytest.fixture(autouse=True)
def _isolate():
    """Per-test isolation for the two module-level globals these paths touch.

    ``flag_modified`` needs a real ORM state and the fakes have none; and
    ``_flag_cache_locks`` is a module-level table whose entries bind to the
    event loop that first awaited them, so a lock left behind by one test
    cannot be awaited by the next test's loop.
    """
    ff_service._flag_cache_locks.clear()
    with patch.object(ff_service, "flag_modified", MagicMock()):
        yield
    ff_service._flag_cache_locks.clear()


def _service(session: FakeTenantSession, cache: FakeCache) -> FeatureFlagService:
    return FeatureFlagService(db=session)


@pytest.fixture
def cache() -> FakeCache:
    return FakeCache()


@pytest.fixture
def wired(cache: FakeCache):
    """Patch the module's cache_client for the duration of a test."""
    with patch.object(ff_service, "cache_client", cache):
        yield cache


async def _turns(n: int = 6) -> None:
    """Give every ready task a chance to run to its next suspension."""
    for _ in range(n):
        await asyncio.sleep(0)


class TestRowLockOnTheTenantRead:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("update_tenant_flag", ("a", True)),
            ("delete_tenant_flag", ("a",)),
            ("bulk_update_tenant_flags", ({"a": True},)),
        ],
    )
    async def test_mutator_reads_the_row_for_update(self, wired, method: str, args: tuple) -> None:
        """A plain ``db.get`` can be answered from the identity map.

        In a request the Tenant row is usually already loaded, so the
        unlocked read emitted no SQL and took no row lock at all. Passing
        ``with_for_update`` is what makes the SELECT FOR UPDATE happen.
        """
        session = FakeTenantSession({"a": True})
        svc = _service(session, wired)

        await getattr(svc, method)(TENANT, *args)

        assert session.locked_reads == 1, f"{method} read the Tenant row without FOR UPDATE"

    @pytest.mark.asyncio
    async def test_concurrent_updates_both_survive(self, wired) -> None:
        """The lost update: two writers, two flags, one row."""
        session = FakeTenantSession({"existing": True})
        svc = _service(session, wired)

        await asyncio.gather(
            svc.update_tenant_flag(TENANT, "alpha", True),
            svc.update_tenant_flag(TENANT, "beta", True),
        )

        assert session.rows[TENANT] == {"existing": True, "alpha": True, "beta": True}

    @pytest.mark.asyncio
    async def test_concurrent_update_and_delete_both_survive(self, wired) -> None:
        session = FakeTenantSession({"old": True, "keep": True})
        svc = _service(session, wired)

        await asyncio.gather(
            svc.update_tenant_flag(TENANT, "new", True),
            svc.delete_tenant_flag(TENANT, "old"),
        )

        assert session.rows[TENANT] == {"keep": True, "new": True}

    @pytest.mark.asyncio
    async def test_concurrent_bulk_and_single_both_survive(self, wired) -> None:
        session = FakeTenantSession({"old": True})
        svc = _service(session, wired)

        await asyncio.gather(
            svc.bulk_update_tenant_flags(TENANT, {"bulk_a": 1, "bulk_b": 2}),
            svc.update_tenant_flag(TENANT, "single", 3),
        )

        assert session.rows[TENANT] == {"old": True, "bulk_a": 1, "bulk_b": 2, "single": 3}

    @pytest.mark.asyncio
    async def test_without_the_row_lock_a_flag_is_lost(self, wired) -> None:
        """Negative control: the fake can express the bug it fixed.

        Same fake, same two writers, engine that ignores FOR UPDATE (what
        the old plain ``db.get`` produced): the second commit discards the
        first writer's flag.
        """
        session = FakeTenantSession({"existing": True}, honour_for_update=False)
        svc = _service(session, wired)

        await asyncio.gather(
            svc.update_tenant_flag(TENANT, "alpha", True),
            svc.update_tenant_flag(TENANT, "beta", True),
        )

        assert session.rows[TENANT] == {"existing": True, "beta": True}
        assert "alpha" not in session.rows[TENANT], "the control is only meaningful if it loses a flag"


class TestStaleRepopulation:
    @pytest.mark.asyncio
    async def test_reader_cannot_repopulate_a_key_after_a_write_evicts_it(self, wired) -> None:
        """Commit-then-invalidate with no lock let a reader win the race.

        Interleaving: reader's ``_load`` takes the PRE-commit JSONB, the
        writer commits and deletes the key, the reader then writes the
        stale map back — served for CACHE_TTL. The reader holds
        ``_flag_cache_locks[features]`` across its read AND its write, so
        the writer must wait for it; the eviction therefore lands last.
        """
        session = FakeTenantSession({"before": True})
        session.pause_next_read = True
        session.read_paused = asyncio.Event()
        session.resume_read = asyncio.Event()
        svc = _service(session, wired)
        cache_key = build_tenant_key("features", TENANT)

        reader = asyncio.create_task(svc.get_tenant_flags(TENANT))
        await session.read_paused.wait()
        # The reader is parked holding the singleflight lock, with a
        # pre-commit snapshot in hand.
        assert cache_key not in wired.data

        writer = asyncio.create_task(svc.update_tenant_flag(TENANT, "after", True))
        await _turns()

        assert session.commits == 0, "the writer committed without waiting for the reader's cache write"

        session.resume_read.set()
        await asyncio.gather(reader, writer)

        assert session.rows[TENANT] == {"before": True, "after": True}
        assert cache_key not in wired.data, f"a stale pre-commit map was cached: {wired.data}"


class TestBulkInvalidationCoversEveryDecisionShape:
    @pytest.mark.asyncio
    async def test_bulk_evicts_the_typed_value_keys(self, wired) -> None:
        """``get_flag_value`` caches under ``feature_value:{name}``.

        The bulk path evicted ``features`` and ``feature:{name}`` only, so
        the typed accessor kept serving the pre-bulk value for CACHE_TTL.
        """
        session = FakeTenantSession({})
        svc = _service(session, wired)

        await svc.bulk_update_tenant_flags(TENANT, {"dark_mode": True, "theme": "dark"})

        assert len(wired.deletes) == 1, "bulk invalidation must be ONE round trip"
        evicted = " ".join(wired.deletes[0])
        expected_shapes = (
            "features",
            "feature:dark_mode",
            "feature_value:dark_mode",
            "feature:theme",
            "feature_value:theme",
        )
        for expected in expected_shapes:
            assert expected in evicted, f"{expected} was not evicted: {evicted}"

    @pytest.mark.asyncio
    async def test_single_flag_and_bulk_agree_on_the_key_shapes(self, wired) -> None:
        session = FakeTenantSession({"a": True})
        svc = _service(session, wired)

        await svc.update_tenant_flag(TENANT, "a", False)
        await svc.bulk_update_tenant_flags(TENANT, {"a": False})

        single, bulk = wired.deletes
        shapes = ("features", "feature:a", "feature_value:a")
        assert set(single) == {build_tenant_key(suffix, TENANT) for suffix in shapes}
        assert set(bulk) == set(single), "the two invalidation paths drifted apart"


class TestCacheDownPosture:
    @pytest.mark.asyncio
    async def test_a_failing_invalidation_warns_and_returns(self, caplog) -> None:
        """The commit must not be undone by a dead Redis."""
        session = FakeTenantSession({})
        svc = _service(session, MagicMock())

        async def boom(*args, **kwargs):
            raise RuntimeError("redis down")

        svc_cache = MagicMock()
        svc_cache.get = AsyncMock(side_effect=boom)
        svc_cache.set = AsyncMock(side_effect=boom)
        svc_cache.delete = AsyncMock(side_effect=boom)
        with patch.object(ff_service, "cache_client", svc_cache):
            tenant = await svc.update_tenant_flag(TENANT, "a", True)

        assert tenant.feature_flags == {"a": True}
        assert session.commits == 1
