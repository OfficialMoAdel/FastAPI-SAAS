"""H-1 verification: the repaired arithmetic in ``driver_count_io.py`` is pinned.

``FastAPI_saas-8lwo`` repaired two defects that made every in-process number
in the perf pack uninterpretable:

1. ``pct()`` was ``s[min(int(len(s) * p), len(s) - 1)]``, which truncates
   instead of rounding up. Because ``int(20 * .95) == int(20 * .99) == 19``,
   both p95 and p99 returned ``max()`` for every 20-sample phase — including
   the 208.47 ms logout p95 the pack quotes.
2. The ``LuaManager.execute`` shim's timed window CONTAINS its own
   ``EVALSHA``; the raw ``TIMES`` sum double-counted every Lua round trip.

A repaired-but-unverified arithmetic path still yields confident wrong
numbers, so every contract below is pinned against a hand-computed value AND
demonstrated to disagree with the pre-fix formula. A test that cannot fail is
not a verification.

SCOPE NOTE ON THE "UNITS" CONTRACT: the bead also asks for a units test that
fails against a pre-fix formula at ``driver_count_io.py:129``. There is no
such pre-fix defect: ``{nother + neval}`` is a count and is correctly NOT
divided, and its neighbours are correctly divided by ``n``. That claim was
checked and refuted (see the bead's notes); the test here pins the units
contract that actually holds — counts stay counts, per-request rates are
divided by n, verified at n=1 and n=20 against hand-computed numbers.
"""

import math
from collections import Counter

import pytest

from src.benchmarks.driver_count_io import (
    REDIS,
    SQL,
    TIMES,
    aggregate_timings,
    pct,
    report,
    reset,
    unresolved_percentiles,
)


def prefix_pct(v: list[float], p: float) -> float:
    """``pct()`` exactly as it was BEFORE ``FastAPI_saas-8lwo``."""
    s = sorted(v)
    return s[min(int(len(s) * p), len(s) - 1)]


def prefix_per_call(times: list[tuple[str, float]]) -> list[float]:
    """``report()``'s "measured Redis wait/request" numerator BEFORE 8lwo.

    The old code summed raw ``TIMES`` while only de-duplicating ``EVALSHA``
    in the per-name breakdown, so this is the double-counting version.
    """
    return [d for _, d in times]


@pytest.fixture(autouse=True)
def _clean_counters():
    reset()
    yield
    reset()


def _wall(n: int, base_ms: float = 1.0) -> list[float]:
    return [base_ms + i for i in range(n)]


# --------------------------------------------------------------------------
# (3) pct() boundaries — the contract that matters most, because the bug was
#     a boundary bug.
# --------------------------------------------------------------------------


def test_pct_at_n1_is_the_only_sample():
    assert pct([7.5], 0.5) == 7.5
    assert pct([7.5], 0.95) == 7.5
    assert pct([7.5], 0.99) == 7.5


def test_pct_at_n20_p95_and_p99_are_not_both_max():
    v = _wall(20)
    assert pct(v, 0.95) == v[18], "nearest rank of p95 at n=20 is the 19th sample"
    assert pct(v, 0.99) == v[19]
    assert pct(v, 0.95) != pct(v, 0.99)
    assert pct(v, 0.95) != max(v)


def test_pct_at_n20_differs_from_the_pre_fix_formula():
    """The negative test: the pre-fix formula is what produced ``max`` twice."""
    v = _wall(20)
    assert prefix_pct(v, 0.95) == max(v)
    assert prefix_pct(v, 0.99) == max(v)
    assert prefix_pct(v, 0.95) == prefix_pct(v, 0.99), "this is the reported defect"
    assert (prefix_pct(v, 0.95), prefix_pct(v, 0.99)) != (pct(v, 0.95), pct(v, 0.99))


def test_pct_at_n1000_is_nearest_rank():
    v = _wall(1000)
    for p, rank in ((0.5, 500), (0.95, 950), (0.99, 990)):
        assert pct(v, p) == v[rank - 1]


def test_pct_p0_is_the_minimum():
    v = [5.0, 1.0, 3.0]
    assert pct(v, 0.0) == min(v)


def test_pct_p1_never_indexes_out_of_range():
    v = _wall(20)
    assert pct(v, 1.0) == max(v)
    assert 0 <= v.index(pct(v, 1.0)) < len(v)


def test_pct_is_monotonic_in_p():
    v = _wall(37)
    ordered = [pct(v, p) for p in (0.0, 0.5, 0.9, 0.95, 0.99, 1.0)]
    assert ordered == sorted(ordered)


def test_pct_of_an_empty_sample_raises():
    with pytest.raises(ValueError):
        pct([], 0.95)


def test_unresolved_percentiles_names_what_n_cannot_resolve():
    assert unresolved_percentiles(20) == [0.99]
    assert unresolved_percentiles(1000) == []
    assert unresolved_percentiles(1) == [0.5, 0.95, 0.99]
    assert math.ceil(0.99 * 20) == 20


# --------------------------------------------------------------------------
# (2) Deduplicated aggregate.
# --------------------------------------------------------------------------


def test_aggregate_timings_drops_the_evalsha_contained_in_each_lua_sample():
    times = [
        ("EVALSHA", 0.002),
        ("LUA:gcra", 0.003),
        ("EVALSHA", 0.002),
        ("LUA:gcra", 0.003),
        ("GET", 0.001),
    ]
    agg = aggregate_timings(times)
    assert agg == {"LUA:gcra": [0.003, 0.003], "GET": [0.001]}
    deduped = sum(d for ds in agg.values() for d in ds)
    assert deduped == pytest.approx(0.007)
    assert deduped != pytest.approx(sum(prefix_per_call(times))), "raw sum double-counts"


def test_aggregate_timings_keeps_a_standalone_evalsha():
    times = [
        ("EVALSHA", 0.002),
        ("LUA:gcra", 0.003),
        ("EVALSHA", 0.005),
    ]
    agg = aggregate_timings(times)
    assert agg["EVALSHA"] == [0.005]
    assert agg["LUA:gcra"] == [0.003]


def test_aggregate_timings_is_a_no_op_without_lua_samples():
    times = [("EVALSHA", 0.002), ("GET", 0.001)]
    agg = aggregate_timings(times)
    assert agg == {"EVALSHA": [0.002], "GET": [0.001]}
    assert sum(d for ds in agg.values() for d in ds) == pytest.approx(sum(prefix_per_call(times)))


# --------------------------------------------------------------------------
# (1) Units: counts stay counts, per-request rates are divided by n.
# --------------------------------------------------------------------------


def test_report_units_at_n1(capsys):
    REDIS.update({"GET": 3, "EVALSHA": 1})
    SQL.update({"select 1": 2})
    TIMES.extend([("GET", 0.001), ("GET", 0.001), ("GET", 0.001), ("EVALSHA", 0.002)])

    report("n=1", 1, _wall(1, base_ms=4.0))
    out = capsys.readouterr().out

    # 4 round trips over 1 request: the total is a COUNT, the breakdown divides.
    assert "REDIS round trips/request: 4  (1.00 EVALSHA, 3.00 other)" in out
    # (3 + 1) ms of Redis wait over 1 request.
    assert "measured Redis wait/request: 5.00 ms" in out
    assert "SQL statements/request: 2.00" in out


def test_report_units_at_n20(capsys):
    REDIS.update({"GET": 60, "EVALSHA": 20})
    SQL.update({"select 1": 40})
    TIMES.extend([("GET", 0.001)] * 60)
    TIMES.extend([("EVALSHA", 0.001)] * 20)

    report("n=20", 20, _wall(20))
    out = capsys.readouterr().out

    # 80 round trips over 20 requests -> 4.00/request, and the split is 1/3.
    assert "REDIS round trips/request: 80  (1.00 EVALSHA, 3.00 other)" in out
    # 80 ms of Redis wait over 20 requests -> 4.00 ms/request, NOT 80 ms.
    assert "measured Redis wait/request: 4.00 ms" in out
    assert "SQL statements/request: 2.00" in out
    assert "cannot resolve p99" in out


def test_report_redis_wait_excludes_the_evalsha_contained_in_lua(capsys):
    for _ in range(4):
        TIMES.append(("EVALSHA", 0.002))
        TIMES.append(("LUA:gcra", 0.003))
    REDIS.update({"EVALSHA": 4, "LUA:gcra": 4})

    report("dedup", 4, _wall(4, base_ms=10.0))
    out = capsys.readouterr().out

    # 4 x 3 ms = 12 ms over 4 requests = 3.00 ms/request. The raw sum would be
    # (4 x 2 + 4 x 3) / 4 = 5.00 ms/request, i.e. 40% inflated.
    assert "measured Redis wait/request: 3.00 ms" in out
    assert "measured Redis wait/request: 5.00 ms" not in out


def test_report_counter_reset_clears_all_three(capsys):
    REDIS.update({"GET": 1})
    SQL.update({"select 1": 1})
    TIMES.append(("GET", 0.001))
    reset()
    assert REDIS == Counter()
    assert SQL == Counter()
    assert TIMES == []
