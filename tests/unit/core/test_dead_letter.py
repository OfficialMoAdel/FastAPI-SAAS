"""Direct contract tests for the unified dead-letter module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.app.core.dead_letter import DeadLetterQueue


class _ReadRedis:
    def __init__(self, entries: list[str]) -> None:
        self.entries = entries

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        assert key == DeadLetterQueue.key
        # Real Redis: -1 means "last element". S-42's filtered read relies on
        # LRANGE 0 -1, so the fake must honour it or the scan returns nothing.
        if end == -1:
            return self.entries[start:]
        return self.entries[start : end + 1]


@pytest.mark.asyncio
async def test_codec_normalizes_json_and_legacy_entries() -> None:
    queue = DeadLetterQueue(redis_client=_ReadRedis([]))

    modern = queue.decode(json.dumps({"job_id": "job-1", "function": "task", "timestamp": 1.0}))
    legacy = queue.decode("job-2|task|failed|1.0", include_legacy=True)

    assert modern is not None
    assert modern["source"] == "arq"
    assert modern["timestamp_iso"].startswith("1970-01-01")
    assert legacy is not None
    # F6: a legacy pipe row is NOT an "arq" row — the server-side filtered
    # pop/clear only understand JSON envelopes, so tagging it "arq" made a
    # source="arq" read show rows the pop could never touch.
    assert legacy["source"] == "legacy"
    assert legacy["error"] == "failed"
    assert legacy["timestamp_iso"].startswith("1970-01-01")


@pytest.mark.asyncio
async def test_read_uses_one_codec_for_all_sources() -> None:
    entries = [
        json.dumps({"job_id": "arq-1", "function": "task", "timestamp": 1.0}),
        json.dumps({"job_id": "bus-1", "source": "event_bus", "timestamp": 2.0}),
        "legacy|task|failed|3.0",
        "malformed",
    ]
    queue = DeadLetterQueue(redis_client=_ReadRedis(entries))

    all_entries = await queue.read(include_invalid=True, include_legacy=True)
    bus_entries = await queue.read(source="event_bus")

    # F6: the legacy row carries its own source, so the read and the
    # server-side filtered pop/clear agree on what an "arq" row is.
    assert [entry.get("source") for entry in all_entries] == ["arq", "event_bus", "legacy", None]
    assert all_entries[-1]["error"] == "parse_error"
    assert len(bus_entries) == 1
    assert bus_entries[0]["job_id"] == "bus-1"


def test_stage_owns_codec_and_retention() -> None:
    queue = DeadLetterQueue(redis_client=object())
    pipeline = MagicMock()
    entry = queue.arq_entry("job-1", "task", "failed")

    queue.stage(pipeline, entry)

    pipeline.lpush.assert_called_once()
    assert pipeline.lpush.call_args.args[0] == DeadLetterQueue.key
    assert json.loads(pipeline.lpush.call_args.args[1])["source"] == "arq"
    pipeline.ltrim.assert_called_once()
