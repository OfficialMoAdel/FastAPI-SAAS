"""S-42 / S-43 — dead-letter queue read paging and legacy drain.

S-42  `read()` applied a `source` filter AFTER the LRANGE slice, so
      offset/limit paged over RAW list positions rather than over matching
      entries: a filtered page returned fewer than `limit` rows (often none)
      while matches sat further down, and advancing `offset` skipped entries.
      `size()` was unfiltered, so no caller could page a filtered list at all.

S-43  `drain_legacy()` RPOPped first and then `continue`d on a parse error —
      destroying the row it was supposed to migrate, with no record anywhere.
      The rows most likely to fail `json_loads` are the legacy pipe format
      (`job|fn|error|ts`), which `decode(..., include_legacy=True)` handles
      elsewhere. It also LPUSHed without `stage()`'s bounded LTRIM.

Pure-logic tests with a fake Redis — no server needed.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from src.app.core.dead_letter import DeadLetterQueue


class FakeRedis:
    """Minimal async stand-in for the list ops the DLQ uses."""

    def __init__(self, items: list[str] | None = None) -> None:
        self.lists: dict[str, list[str]] = {}
        if items:
            self.lists["arq:dlq"] = list(items)
        self.other: list[str] = []

    def _key(self, key: str) -> list[str]:
        return self.lists.setdefault(key, [])

    # --- list ops ---
    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        data = self._key(key)
        return data[start:] if stop == -1 else data[start : stop + 1]

    async def lpush(self, key: str, value: str) -> int:
        self._key(key).insert(0, value)
        return len(self._key(key))

    async def rpop(self, key: str) -> str | None:
        data = self._key(key)
        return data.pop() if data else None

    async def ltrim(self, key: str, start: int, stop: int) -> int:
        data = self._key(key)
        kept = data[start : stop + 1] if stop >= 0 else data[start:]
        data[:] = kept
        return len(kept)

    async def llen(self, key: str) -> int:
        return len(self._key(key))

    async def delete(self, key: str) -> int:
        n = len(self._key(key))
        self._key(key).clear()
        return n

    async def lrem(self, key: str, count: int, value: str) -> int:
        data = self._key(key)
        removed = 0
        while removed < count and value in data:
            data.remove(value)
            removed += 1
        return removed

    # --- pipeline ---
    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self)

    def scan_iter(self, match: str | None = None):
        raise NotImplementedError


class _FakePipeline:
    def __init__(self, client: FakeRedis) -> None:
        self._c = client
        self._queued: list[tuple[str, tuple[Any, ...]]] = []

    async def __aenter__(self) -> _FakePipeline:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def lpush(self, key: str, value: str) -> None:
        self._queued.append(("lpush", (key, value)))

    def ltrim(self, key: str, start: int, stop: int) -> None:
        self._queued.append(("ltrim", (key, start, stop)))

    def llen(self, key: str) -> None:
        self._queued.append(("llen", (key,)))

    async def execute(self) -> list[int]:
        out = []
        for op, args in self._queued:
            out.append(await getattr(self._c, op)(*args))
        self._queued.clear()
        return out


def _dlq(client: FakeRedis) -> DeadLetterQueue:
    return DeadLetterQueue(redis_client=client)


def _entry(source: str, n: int) -> str:
    return json.dumps({"job_id": f"j{n}", "function": "f", "error": "e", "timestamp": 0, "source": source})


class TestS42FilteredPaging:
    @pytest.mark.asyncio
    async def test_filtered_page_returns_a_full_page(self) -> None:
        """Interleaved sources: a limit-2 'arq' page must yield 2 arq rows."""
        client = FakeRedis([_entry("arq", i) for i in range(3)] + [_entry("bus", i) for i in range(3)])
        got = await _dlq(client).read(limit=2, source="arq")
        assert len(got) == 2, f"filtered page short: {len(got)}"
        assert all(e["source"] == "arq" for e in got)

    @pytest.mark.asyncio
    async def test_paging_walks_the_filtered_sequence(self) -> None:
        """offset must advance over MATCHING entries, not raw positions."""
        client = FakeRedis([_entry("bus", i) for i in range(3)] + [_entry("arq", i) for i in range(3)])
        dlq = _dlq(client)
        first = await dlq.read(limit=2, offset=0, source="arq")
        second = await dlq.read(limit=2, offset=2, source="arq")
        ids = [e["job_id"] for e in first + second]
        # 3 arq entries exist; paging over the FILTERED sequence must surface
        # all 3 exactly once (no overlap, no skip), whatever the raw positions.
        assert len(ids) == len(set(ids)), f"pages overlapped: {ids}"
        assert set(ids) == {"j0", "j1", "j2"}, f"pages skipped entries: {ids}"

    @pytest.mark.asyncio
    async def test_unfiltered_paging_is_unchanged(self) -> None:
        client = FakeRedis([_entry("arq", i) for i in range(5)])
        got = await _dlq(client).read(limit=2, offset=0)
        assert [e["job_id"] for e in got] == ["j0", "j1"]

    @pytest.mark.asyncio
    async def test_size_can_be_scoped_to_a_source(self) -> None:
        client = FakeRedis([_entry("arq", i) for i in range(2)] + [_entry("bus", i) for i in range(4)])
        dlq = _dlq(client)
        assert await dlq.size() == 6
        assert await dlq.size(source="arq") == 2


class TestS43LegacyDrain:
    @pytest.mark.asyncio
    async def test_unparseable_row_is_preserved_not_destroyed(self) -> None:
        """The regression: a legacy pipe-format row must survive the drain."""
        client = FakeRedis()
        client.other = ["evt|func|boom|1700000000.0"]  # legacy pipe format
        client.lists["event_bus:dlq"] = list(client.other)

        moved = await _dlq(client).drain_legacy()

        assert moved == 1, "the row was dropped instead of migrated"
        entries = await _dlq(client).read(limit=10, include_invalid=True)
        assert len(entries) == 1
        blob = json.dumps(entries[0])
        assert "evt|func|boom" in blob, f"raw text not preserved: {entries[0]}"
        assert entries[0].get("source") == "event_bus"

    @pytest.mark.asyncio
    async def test_parseable_dict_still_migrates_normally(self) -> None:
        client = FakeRedis()
        client.lists["event_bus:dlq"] = [json.dumps({"job_id": "j1", "function": "f"})]
        assert await _dlq(client).drain_legacy() == 1
        entries = await _dlq(client).read(limit=10)
        assert entries[0]["job_id"] == "j1"
        assert entries[0]["source"] == "event_bus"

    @pytest.mark.asyncio
    async def test_drain_respects_the_size_bound(self) -> None:
        """stage()'s LTRIM must apply to migrated rows."""
        client = FakeRedis()
        client.lists["event_bus:dlq"] = [json.dumps({"job_id": f"j{i}", "function": "f"}) for i in range(5)]

        # patch.object restores the descriptor correctly. Assigning
        # `DeadLetterQueue._settings = staticmethod(fn)` and restoring with the
        # class attribute LEAKS: reading it off the class resolves the
        # descriptor to a plain function, so the restore installs a bound-style
        # function and every later `self._settings()` raises
        # "takes 0 positional arguments but 1 was given".
        with patch.object(DeadLetterQueue, "_settings", staticmethod(lambda: (True, 3, 80.0))):
            await _dlq(client).drain_legacy()

        assert len(client.lists["arq:dlq"]) == 3, f"LTRIM did not apply: {len(client.lists['arq:dlq'])}"

    @pytest.mark.asyncio
    async def test_empty_legacy_queue_is_a_noop(self) -> None:
        assert await _dlq(FakeRedis()).drain_legacy() == 0
