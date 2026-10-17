"""C-4 oracle: one pipeline with ``transaction=False`` replaces N serial HGETALLs.

Bead ``FastAPI_saas-ynxl``. ``JobMonitoringService.get_job_metrics``
(``src/app/core/job_monitoring.py:120-145``) scans ``arq:stats:*`` and issued
one ``hgetall`` per key — N serial round trips. C-4 batches them into a single
pipeline, and that diff HAS LANDED, so these tests pin PRODUCTION directly
rather than an in-test copy of the intended shape: a helper that merely
mirrors what production does proves nothing about production.

Three properties, all asserted against ``monitor.get_job_metrics()``:

1. ``pipeline(transaction=False)`` is opened EXACTLY ONCE — the keyword is
   asserted, not just pipeline use, because ``transaction=False`` (no
   MULTI/EXEC) is the load-bearing part of the change.
2. The returned mapping matches the PRE-C-4 serial loop exactly, including
   key order — order, not just set (key-order stability of the metrics
   mapping is the point).
3. Command count goes N -> 1 (plus the scan both paths share), with the
   HGETALLs observed zero times outside the pipeline.

Harness note (the bead's "confirm first" requirement): ``fakeredis`` is NOT
installed, and the repo's shared ``FakeRedisPipeline`` accepts the
``transaction`` kwarg but has NO ``hgetall`` branch (it returns None for
unknown commands). So this file defines its own small fake with HGETALL
support — the same precedent as ``test_cache_tenant_scope.py`` and
``test_security_state_cleanup.py``. The first test below pins that
capability, so a future shared-fake upgrade cannot silently void this file.

Line-citation correction for the bead record: the bead says
``core/job_monitoring.py:120`` does N x HGETALL; ``:120`` is the
``get_job_metrics`` DEF and the serial loop is ``:132-140`` (``hgetall`` at
``:138``). The shape is real, the line is off by ~18.
"""

from __future__ import annotations

import fnmatch
from typing import Any

import pytest

from src.app.core.job_monitoring import JOB_STATS_PREFIX, JobMonitoringService

# Fixed fixture in NON-sorted insertion order — load-bearing: a fixture in
# sorted order would pass even if an implementation sorted the keys.
_FIXTURE_HASHES: tuple[tuple[str, dict[str, str]], ...] = (
    ("arq:stats:zeta_worker", {"total": "10", "success": "9", "failed": "1", "total_duration": "4.5"}),
    ("arq:stats:alpha_worker", {"total": "20", "success": "20", "failed": "0", "total_duration": "1.0"}),
    ("arq:stats:mike_worker", {"total": "7", "success": "5", "failed": "2", "total_duration": "9.25"}),
    ("arq:stats:beta_worker", {"total": "3", "success": "3", "failed": "0", "total_duration": "0.5"}),
    ("arq:stats:kappa_worker", {"total": "12", "success": "11", "failed": "1", "total_duration": "6.0"}),
)

_FIXTURE_ORDER: tuple[str, ...] = tuple(name.removeprefix("arq:stats:") for name, _ in _FIXTURE_HASHES)


class _FakeMonitorPipeline:
    """Buffers HGETALLs until ``execute`` — the only pipeline shape C-4 needs."""

    def __init__(self, store: dict[str, dict[str, str]], *, transaction: bool, log: list[tuple[str, Any]]) -> None:
        self._store = store
        self.transaction = transaction
        self._buffered: list[str] = []
        self._log = log

    def hgetall(self, key: str) -> _FakeMonitorPipeline:
        self._buffered.append(key)
        return self

    async def execute(self) -> list[dict[str, str]]:
        self._log.append(("EXECUTE", len(self._buffered)))
        return [dict(self._store.get(key, {})) for key in self._buffered]

    async def __aenter__(self) -> _FakeMonitorPipeline:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeMonitorRedis:
    """Scan + HGETALL + keyword-recording pipeline over an in-memory hash store."""

    def __init__(self, hashes: dict[str, dict[str, str]]) -> None:
        self._hashes = hashes
        self.commands: list[tuple[str, Any]] = []

    async def scan(self, cursor: int, match: str | None = None, count: int | None = None) -> tuple[int, list[str]]:
        self.commands.append(("SCAN", match))
        return 0, [key for key in self._hashes if fnmatch.fnmatch(key, match or "*")]

    async def hgetall(self, key: str) -> dict[str, str]:
        self.commands.append(("HGETALL", key))
        return dict(self._hashes.get(key, {}))

    def pipeline(self, transaction: bool = True) -> _FakeMonitorPipeline:
        self.commands.append(("PIPELINE", transaction))
        return _FakeMonitorPipeline(self._hashes, transaction=transaction, log=self.commands)


def _fresh_fake() -> _FakeMonitorRedis:
    return _FakeMonitorRedis({name: dict(values) for name, values in _FIXTURE_HASHES})


async def _serial_snapshot(
    monitor: JobMonitoringService, client: _FakeMonitorRedis
) -> dict[str, dict[str, Any]]:
    """The PRE-C-4 shape: N serial HGETALLs, one round trip per key.

    Kept as the equivalence reference now that production is pipelined. If the
    batching ever changed which keys are returned, in what order, or how their
    values are parsed, production would drift from this and the equality test
    below would bite. Reading it through the same fake keeps the two paths
    comparable command-for-command.
    """
    _, keys = await client.scan(0, match=f"{JOB_STATS_PREFIX}*", count=100)
    stats: dict[str, dict[str, Any]] = {}
    for key in keys:
        key_str = key.decode("utf-8") if isinstance(key, bytes) else key
        func_name = key_str.replace(JOB_STATS_PREFIX, "")
        data = await client.hgetall(key)
        if data:
            stats[func_name] = monitor._parse_metrics(data)
    return stats


def _hgetall_count(client: _FakeMonitorRedis) -> int:
    return sum(1 for entry in client.commands if entry[0] == "HGETALL")


def _executes(client: _FakeMonitorRedis) -> list[tuple[str, Any]]:
    return [entry for entry in client.commands if entry[0] == "EXECUTE"]


class TestTheFakeSupportsWhatTheOracleNeeds:
    async def test_pipeline_accepts_the_transaction_keyword_and_hgetall_returns_in_order(self) -> None:
        client = _fresh_fake()
        pipe = client.pipeline(transaction=False)
        assert pipe.transaction is False
        pipe.hgetall("arq:stats:alpha_worker")
        pipe.hgetall("arq:stats:zeta_worker")
        datas = await pipe.execute()
        assert [d["total"] for d in datas] == ["20", "10"]

    async def test_scan_returns_fixture_keys_in_fixture_order(self) -> None:
        client = _fresh_fake()
        _, keys = await client.scan(0, match=f"{JOB_STATS_PREFIX}*", count=100)
        assert keys == [name for name, _ in _FIXTURE_HASHES]


class TestProductionBatchesAndStillAgreesWithTheSerialLoop:
    async def test_production_returns_the_same_ordered_mapping_as_the_serial_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monitor = JobMonitoringService()
        serial_client = _fresh_fake()
        serial_stats = await _serial_snapshot(monitor, serial_client)
        assert serial_stats, "an empty serial result would make the equality vacuous"

        pipelined_client = _fresh_fake()
        monkeypatch.setattr("src.app.core.job_monitoring.get_redis", lambda: pipelined_client)
        production_stats = await monitor.get_job_metrics()

        assert production_stats == serial_stats
        assert list(production_stats.keys()) == list(serial_stats.keys())
        assert list(production_stats.keys()) == list(_FIXTURE_ORDER)

    async def test_production_opens_one_non_transactional_pipeline_and_never_pipelines_twice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monitor = JobMonitoringService()
        client = _fresh_fake()
        monkeypatch.setattr("src.app.core.job_monitoring.get_redis", lambda: client)
        await monitor.get_job_metrics()

        pipeline_opens = [entry for entry in client.commands if entry[0] == "PIPELINE"]
        assert pipeline_opens == [("PIPELINE", False)]

    async def test_production_command_count_goes_from_n_to_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monitor = JobMonitoringService()

        serial_client = _fresh_fake()
        await _serial_snapshot(monitor, serial_client)
        assert _hgetall_count(serial_client) == len(_FIXTURE_HASHES)

        production_client = _fresh_fake()
        monkeypatch.setattr("src.app.core.job_monitoring.get_redis", lambda: production_client)
        await monitor.get_job_metrics()

        assert _hgetall_count(production_client) == 0
        assert _executes(production_client) == [("EXECUTE", len(_FIXTURE_HASHES))]
