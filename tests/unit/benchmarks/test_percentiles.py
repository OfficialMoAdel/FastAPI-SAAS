"""H-6: the harness tree must have ONE percentile implementation, and it must not be a broken one.

Six mutually inconsistent implementations existed when this was written. The
first five were found by reading the harness tree; the sixth was found by
``test_every_percentile_function_delegates`` below, which searches by NAME
rather than by formula and so caught an alias the survey had missed:

    src/benchmarks/driver_count_io.py      pct(v, p)                nearest rank, after H-1
    src/benchmarks/census.py               pct(s, q)                s[round(q * (n - 1))]
    tests/performance/locustfile.py        get_percentile(name, p)  times[min(int(n * p / 100), n - 1)]  <- BROKEN
    tests/benchmarks/baseline_metrics.py   percentile(p)            linear interpolation
    scripts/profile_refresh.py             _pct(values, q)          s[round(q * (n - 1))]
    tests/benchmarks/middleware_profiler.py ProfileResult.p50/p95/p99  int(n * q) on properties, + median <- BROKEN

The third and the sixth carried the defect that cost something.
``int(20 * 95 / 100)`` and ``int(20 * 99 / 100)`` are both ``19``, so at n = 20
their p95 and p99 were both ``max()`` — and that is how "p95 208.47 ms" came to
be quoted for ``/account/logout``, a handler that performs no bcrypt and no
batched I/O.

Consolidating onto nearest rank was a deliberate choice, not a tidying. An
interpolating estimator returns values that are frequently not in the sample at
all, so it can quote a p99 for a cost no iteration ever paid. Nearest rank
always returns an OBSERVED sample, which means every percentile is traceable to
a real measurement. Migrating ``baseline_metrics`` off interpolation therefore
CHANGES ITS PUBLISHED NUMBERS; the change is called out in that method's
docstring and on the bead rather than absorbed quietly. ``middleware_profiler``
loses its ``statistics.median`` p50 (which averaged the two central samples at
even n) for the same reason.

Two of the six live in modules that are not import-safe and are therefore
checked structurally rather than numerically — see
``test_census_and_profile_refresh_are_not_import_safe_so_their_delegates_are_checked_structurally``.
"""

from __future__ import annotations

import ast
import math
import re
from pathlib import Path

import pytest

from src.benchmarks.driver_count_io import pct as driver_pct
from src.benchmarks.driver_count_io import unresolved_percentiles as driver_unresolved
from src.benchmarks.percentiles import PERCENTILES, nearest_rank, unresolvable_quantiles

REPO_ROOT = Path(__file__).resolve().parents[3]
CANONICAL_MODULE = "src/benchmarks/percentiles.py"

#: Roots that make up "the harness tree" for this rule. ``tests/unit`` is
#: excluded on purpose: a test that deliberately contains the pre-fix formula in
#: order to assert it differs is not a second implementation.
HARNESS_ROOTS = ("src/benchmarks", "tests/performance", "tests/benchmarks", "scripts")

#: Names that could be a percentile estimator. Deliberately generous: the rule
#: below requires each match to delegate, so an unusual name is caught rather
#: than missed. The canonical names are included so that a REDEFINITION of them
#: outside percentiles.py is found — a rule that does not see its own subject
#: is vacuous.
PERCENTILE_NAMES = re.compile(
    r"^_?p(?:ct|ercentile)(?:_?quantiles?)?$"
    r"|^_?p(?:50|95|99)$"
    r"|^(?:nearest_rank|unresolvable_quantiles)$"
    # The two prefixed spellings the harness also uses. Without these the rule
    # is blind to ``MetricsCollector.get_percentile`` - the function H-6 is
    # actually about - which is the same mistake as a rule that cannot see its
    # own subject.
    r"|^.*_percentiles?$"
)

DELEGATES = frozenset({"nearest_rank", "unresolvable_quantiles"})


def _percentile_functions() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Every percentile-shaped function in the harness tree, with its module path."""
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for root in HARNESS_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and PERCENTILE_NAMES.match(node.name):
                    found.append((path.relative_to(REPO_ROOT).as_posix(), node))
    return found


def _pre_fix_percentile(samples: list[float], p: float) -> float:
    """The formula that shipped in locustfile.py:200, retained to be argued with.

    ``int(len(times) * percentile / 100)`` TRUNCATES, so any quantile whose
    ``n * q`` has a fractional part lands on the same index as the next
    quantile up.
    """
    ordered = sorted(samples)
    index = int(len(ordered) * p / 100)
    return ordered[min(index, len(ordered) - 1)]


# --------------------------------------------------------------------------
# the estimator itself
# --------------------------------------------------------------------------


def test_percentiles_are_fractions_not_percentages():
    assert PERCENTILES == (0.5, 0.95, 0.99)
    assert all(0.0 < q <= 1.0 for q in PERCENTILES)


def test_nearest_rank_returns_an_observed_sample():
    samples = [0.4, 0.1, 0.3, 0.2]
    for q in PERCENTILES:
        assert nearest_rank(samples, q) in samples


def test_at_n1_every_quantile_is_the_single_sample():
    samples = [7.5]
    for q in PERCENTILES:
        assert nearest_rank(samples, q) == 7.5
    assert unresolvable_quantiles(1) == list(PERCENTILES)


def test_at_n20_p95_and_p99_are_not_both_max():
    samples = [float(i) for i in range(1, 21)]
    p95, p99 = nearest_rank(samples, 0.95), nearest_rank(samples, 0.99)
    assert p95 == samples[18]
    assert p99 == samples[19]
    assert p95 != p99
    assert p95 != max(samples)


def test_the_pre_fix_formula_really_did_collapse_p95_and_p99():
    """Why the consolidation exists, asserted rather than asserted-to."""
    samples = [float(i) for i in range(1, 21)]
    pre_p95 = _pre_fix_percentile(samples, 95)
    pre_p99 = _pre_fix_percentile(samples, 99)
    assert pre_p95 == pre_p99 == max(samples)


@pytest.mark.parametrize(
    ("n", "q", "index"),
    [
        (1000, 0.50, 499),
        (1000, 0.95, 949),
        (1000, 0.99, 989),
    ],
)
def test_at_n1000_the_rank_is_ceil_of_q_times_n(n: int, q: float, index: int):
    samples = [float(i) for i in range(1, n + 1)]
    assert nearest_rank(samples, q) == samples[index]
    assert index == math.ceil(q * n) - 1


def test_p_zero_is_the_minimum_and_p_one_is_the_maximum():
    samples = [5.0, 1.0, 3.0]
    assert nearest_rank(samples, 0.0) == min(samples)
    assert nearest_rank(samples, 1.0) == max(samples)


def test_the_result_index_is_always_in_range():
    samples = [2.0, 8.0, 4.0]
    for q in (0.0, 0.5, 0.95, 0.99, 1.0):
        assert nearest_rank(samples, q) in samples


def test_a_quantile_is_monotone_in_q():
    samples = [float(i % 37) for i in range(200)]
    values = [nearest_rank(samples, q) for q in (0.0, 0.25, 0.5, 0.75, 0.95, 0.99, 1.0)]
    assert values == sorted(values)


def test_an_empty_sample_raises_rather_than_reporting_zero():
    """A percentile of nothing is not 0.0 ms; reporting it produces a number
    that reads like data and is not."""
    with pytest.raises(ValueError, match="empty"):
        nearest_rank([], 0.95)


@pytest.mark.parametrize("q", [-0.01, 1.01, 50.0])
def test_a_percentage_where_a_fraction_belongs_is_rejected(q: float):
    samples = [1.0, 2.0]
    with pytest.raises(ValueError, match="fraction"):
        nearest_rank(samples, q)


@pytest.mark.parametrize(
    ("n", "unresolvable"),
    [(1, [0.5, 0.95, 0.99]), (20, [0.99]), (100, []), (1000, [])],
)
def test_quantiles_a_sample_count_cannot_resolve(n: int, unresolvable: list[float]):
    assert unresolvable_quantiles(n) == unresolvable


def test_a_sample_count_below_one_is_an_error():
    with pytest.raises(ValueError):
        unresolvable_quantiles(0)


# --------------------------------------------------------------------------
# the delegates: same answers, or the consolidation is cosmetic
# --------------------------------------------------------------------------


@pytest.mark.parametrize("q", PERCENTILES)
def test_the_driver_harness_delegate_agrees_with_the_canonical_implementation(q: float):
    samples = [float(i % 23) + 0.5 for i in range(57)]
    assert driver_pct(samples, q) == nearest_rank(samples, q)


def test_the_driver_harness_unresolved_delegate_agrees_too():
    for n in (1, 5, 19, 20, 21, 100, 1000):
        assert driver_unresolved(n) == unresolvable_quantiles(n)


def test_the_baseline_metrics_delegate_agrees_with_the_canonical_implementation():
    """baseline_metrics took 0-100 and interpolated; it now takes 0-100 and does not."""
    from tests.benchmarks.baseline_metrics import BenchmarkResult

    samples_ns = [1_000_000 * (i + 1) for i in range(20)]
    result = BenchmarkResult(name="x", category="y", iterations=20, times_ns=samples_ns)
    expected_ms = [s / 1_000_000 for s in samples_ns]
    for p in (50, 95, 99):
        assert result.percentile(p) == pytest.approx(nearest_rank(expected_ms, p / 100))


# --------------------------------------------------------------------------
# the rule: a second implementation must not reappear
# --------------------------------------------------------------------------


def test_the_scan_finds_the_functions_it_is_meant_to_find():
    """A scan that finds nothing passes every rule below. Pin its reach."""
    found = _percentile_functions()
    modules = {module for module, _ in found}
    assert CANONICAL_MODULE in modules, "the canonical module was not scanned; the rule below is vacuous"
    assert len(found) >= 6, f"expected the five known estimators plus delegates, found {sorted(modules)}"


@pytest.mark.parametrize(
    ("module", "function"),
    _percentile_functions(),
    ids=lambda v: v if isinstance(v, str) else v.name,
)
def test_every_percentile_function_delegates(module: str, function: ast.FunctionDef | ast.AsyncFunctionDef):
    """Each match either IS the canonical implementation or derives its value from it.

    The rule is on RETURN VALUES, not on the number of them, because an
    empty-result guard is legitimate: ``middleware_profiler.ProfileResult``
    keeps ``if not self.times_us: return 0.0`` because ``to_dict`` calls all
    three percentiles unconditionally and raising would turn a failed
    benchmark into a crashed one. That guard is an ABSENT measurement, not a
    percentile of data, and it is documented as such in the property.

    So every ``return`` must be either the constant ``0.0`` or a call to a
    known delegate. A hand-rolled ``sorted(v)[int(len(v) * q)]`` fails both
    halves, which is the property the rule exists to guarantee.
    """
    if module == CANONICAL_MODULE:
        assert function.name in {"nearest_rank", "unresolvable_quantiles"}
        return

    returns = [n for n in ast.walk(function) if isinstance(n, ast.Return)]
    assert returns, f"{module}:{function.lineno} {function.name}() returns nothing"
    for node in returns:
        value = node.value
        if isinstance(value, ast.Constant) and value.value == 0.0:
            continue
        assert isinstance(value, ast.Call), (
            f"{module}:{function.lineno} {function.name}() returns {type(value).__name__}, which is neither the "
            f"0.0 empty-result guard nor a delegate call"
        )
        assert isinstance(value.func, ast.Name) and value.func.id in DELEGATES, (
            f"{module}:{function.lineno} {function.name}() returns a call to "
            f"{getattr(value.func, 'id', getattr(value.func, 'attr', '?'))}(), not one of {sorted(DELEGATES)} — "
            f"either delegate, or accept that the harness has a second percentile implementation"
        )


def test_the_canonical_functions_are_defined_in_exactly_one_place():
    definitions: list[str] = []
    for module, function in _percentile_functions():
        if function.name in {"nearest_rank", "unresolvable_quantiles"}:
            definitions.append(f"{module}:{function.lineno}:{function.name}")
    assert sorted(definitions) == [
        f"{CANONICAL_MODULE}:{node.lineno}:{node.name}"
        for node in ast.parse((REPO_ROOT / CANONICAL_MODULE).read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef) and node.name in {"nearest_rank", "unresolvable_quantiles"}
    ], "nearest_rank/unresolvable_quantiles must be defined only in percentiles.py"


def test_census_and_profile_refresh_are_not_import_safe_so_their_delegates_are_checked_structurally():
    """Both set process environment at import time; importing them would leak.

    ``census.py`` calls ``os.chdir(ROOT)`` and ``load_dotenv(..., override=True)``,
    which would replace the environment the pytest session runs under.
    ``profile_refresh.py`` calls ``os.environ.setdefault("AUTO_BAN_ENABLED",
    "false")``, which would silently disable auto-ban for every later test in
    the session if the variable were unset. Neither is imported here; their
    delegates are covered by ``test_every_percentile_function_delegates``, which
    reads the source instead of executing it.
    """
    expected = {
        "src/benchmarks/census.py": ("chdir", "load_dotenv"),
        "scripts/profile_refresh.py": ("environ",),
    }
    for module, markers in expected.items():
        source = (REPO_ROOT / module).read_text(encoding="utf-8")
        tree = ast.parse(source)
        module_level = "\n".join(ast.dump(node) for node in tree.body)
        for marker in markers:
            assert marker in module_level, (
                f"{module} no longer has a module-level '{marker}' side effect, so its delegate can be checked "
                f"numerically like the others. This assertion failing is the signal to import it in a test instead."
            )
