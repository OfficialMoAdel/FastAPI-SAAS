import pytest
from src.app.core.utils.pool_metrics import PoolMetrics
from tests.fixtures.query_counter import QueryLog
from tests.fixtures.memory_profiler import measure_memory


def test_pool_metrics_snapshot():
    metrics = PoolMetrics()
    metrics.pool_size = 10
    metrics.checked_out = 5
    snapshot = metrics.get_snapshot()
    assert snapshot["pool_size"] == 10
    assert snapshot["checked_out"] == 5
    assert "total_checkouts" in snapshot


def test_query_log_limit():
    log = QueryLog(max_statements=5)
    for i in range(10):
        log.add_query(f"SELECT {i}")
    assert log.count == 10
    assert len(log.statements) == 5
    # The implementation appends if len < max, so it keeps the first 5
    assert log.statements[0] == "SELECT 0"


def test_query_log_clear():
    log = QueryLog()
    log.add_query("SELECT 1")
    log.clear()
    assert log.count == 0
    assert len(log.statements) == 0


def test_memory_profiler_detailed():
    # Basic smoke test for detailed capture
    with measure_memory(detailed=True, limit=5) as ctx:
        _ = [i for i in range(1000)]

    assert ctx.snapshot is not None
    assert ctx.snapshot.top_stats is not None
    assert len(ctx.snapshot.top_stats) <= 5
