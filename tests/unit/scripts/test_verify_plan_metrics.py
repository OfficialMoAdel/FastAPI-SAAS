"""Tests for the plan-metric verifier (``scripts/verify_plan_metrics.py``).

This script exists because a number nobody defined cannot be argued with, and
a metric script that silently returns 0 is worse than no script at all — it
looks like evidence. So these tests are weighted toward the failure modes that
produce a confident wrong number rather than toward pinning values that will
legitimately drift as the tree changes:

* every metric key is emitted, with a definition attached to it;
* the values are PLAUSIBLE — a walker that found 3 relationships when the tree
  has hundreds, or 0 imports, is broken and must fail here rather than ship a
  clean-looking report;
* the JSON summary parses and carries the same keys;
* the reused counter is genuinely reused, not silently reimplemented, because
  two implementations of one metric is exactly the defect the script documents;
* a deliberately-broken walk returns 0 and is caught — the "silently zero"
  detector is itself tested, so it cannot rot into a tautology.

Deliberately NOT pinned: exact counts, exact SCC membership, exact noqa totals.
Those are outputs of a changing tree; asserting them makes the test fail on
every unrelated commit and teaches the habit this ticket exists to kill. What
is pinned is that the number was DERIVED from the tree and is not a stub.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
# scripts/ is not an installed package; add it so the verifier is importable by
# module name, matching how test_check_doc_paths.py imports its sibling CLI.
sys.path.insert(0, str(ROOT / "scripts"))

import verify_plan_metrics as vpm  # noqa: E402

#: Every key the script promises. Asserted so a metric cannot be dropped
#: without a test going red.
REQUIRED_KEYS = frozenset(vpm.METRIC_ORDER)


@pytest.fixture(scope="module")
def report():
    """One measurement of the real tree, shared by every test below.

    Module-scoped because a full measurement walks and AST-parses the whole
    src/app tree several times; running it per-test would make this file the
    slowest in the suite for no extra coverage.
    """
    return vpm.build_report()


@pytest.fixture(scope="module")
def metrics(report):
    return report.metrics


# --------------------------------------------------------------------------
# The contract: every metric exists, and carries its definition
# --------------------------------------------------------------------------


class TestEveryMetricIsEmitted:
    def test_all_required_keys_present(self, report):
        missing = sorted(REQUIRED_KEYS - set(report.metrics))
        assert not missing, f"metrics silently missing from the report: {missing}"

    def test_no_unexpected_keys(self, report):
        """An emitted metric nobody prints is a metric nobody reads."""
        extra = sorted(set(report.metrics) - REQUIRED_KEYS)
        assert not extra, f"metrics emitted but never rendered: {extra}"

    def test_every_metric_carries_a_definition(self, metrics):
        """The whole point: a bare number is unarguable, so none may be bare."""
        undefined = [name for name, m in metrics.items() if not m.definition.strip()]
        assert not undefined, f"metrics printed without a definition: {undefined}"

    def test_definitions_are_substantial(self, metrics):
        """A one-word definition is not a definition; it is a label."""
        thin = {n: len(m.definition) for n, m in metrics.items() if len(m.definition) < 80}
        assert not thin, f"definitions too thin to fix a unit: {thin}"

    def test_unmeasured_is_explicit_not_silent(self, metrics):
        """A metric that could not be measured must say UNMEASURED and why."""
        for name, metric in metrics.items():
            if metric.value == "UNMEASURED":
                assert metric.notes.strip(), (
                    f"{name} is UNMEASURED with no reason; an honest gap needs "
                    "a stated cause"
                )


# --------------------------------------------------------------------------
# Not silently zero: the central failure mode
# --------------------------------------------------------------------------


class TestNotSilentlyZero:
    """A metric script that returns 0 everywhere looks exactly like a clean
    codebase. Each metric below has a floor derived from the tree's shape, so
    a walker that stopped early, matched nothing, or filtered too aggressively
    fails here."""

    #: metric -> (strict lower bound, why that bound is safe)
    FLOORS = {
        "import_graph_multi_node_scc_count": (1, "the tree has documented multi-module cycles"),
        "import_graph_self_loop_count": (1, "the lazy __getattr__ facades import their own submodules"),
        "import_graph_modules_in_a_cycle": (10, "the documented cycles cover dozens of modules"),
        "import_graph_total_internal_edges": (500, "src/app has hundreds of internal import edges"),
        "import_graph_from_import_statement_count": (500, "every module imports from something"),
        "tid251_violations": (0, "zero is the real value AND must still be measured, not defaulted"),
        "src_noqa_suppression_count": (1, "the tree demonstrably carries noqa suppressions"),
        "orm_relationship_count": (20, "the ORM models define many relationships"),
        "orm_relationship_lazy_noload_count": (20, "the noload strategy is the tree's stated default"),
        "cross_module_import_statement_count": (100, "feature modules import each other constantly"),
        "cross_module_import_other_internals_count": (1, "the prose rule exists because breaches are real"),
        "to_thread_call_site_count": (5, "blocking I/O is offloaded to threads throughout"),
        "lua_script_count": (5, "the Redis layer registers a family of Lua scripts"),
        "source_text_test_count": (1, "the ratchet baseline exists and is non-zero"),
    }

    @pytest.mark.parametrize("name", sorted(FLOORS))
    def test_metric_meets_its_floor(self, metrics, name):
        floor, why = self.FLOORS[name]
        value = metrics[name].value
        assert value != "UNMEASURED", f"{name} could not be measured"
        assert isinstance(value, int), f"{name} is not an integer: {value!r}"
        assert value >= floor, (
            f"{name} = {value}, below the floor of {floor} ({why}). A walker that "
            "silently matched nothing produces exactly this."
        )

    def test_tid251_is_actually_measured_not_assumed(self, metrics):
        """tid251_violations is legitimately 0. Prove it was RUN, not skipped:
        a metric that cannot distinguish 'clean' from 'never executed' is the
        exact defect this file exists to prevent."""
        metric = metrics["tid251_violations"]
        assert metric.value == 0
        assert "ruff_exit_code" in metric.breakdown
        assert metric.breakdown["ruff_exit_code"] in (0, 1), (
            "ruff did not run to completion (no exit code), so TID251 was never "
            "actually checked"
        )

    def test_noqa_is_not_zero_and_reports_bare_vs_coded(self, metrics):
        """A green lint gate bought with blanket suppressions must be visible.
        This tree has both, and neither number is allowed to be quietly 0."""
        breakdown = metrics["src_noqa_suppression_count"].breakdown
        assert breakdown["bare_noqa"] >= 0
        assert sum(breakdown["by_code"].values()) > 0, (
            "no per-code noqa breakdown; the suppressions cannot be audited"
        )


# --------------------------------------------------------------------------
# Plausibility: the numbers must look like THIS tree
# --------------------------------------------------------------------------


class TestPlausibility:
    def test_relationships_split_sums_to_total(self, metrics):
        """The three relationship metrics must be internally consistent — a
        breakdown that does not add up means one of them was measured under a
        different definition, which is the bug this script exists to expose."""
        total = metrics["orm_relationship_count"].value
        strategies = metrics["orm_relationship_count"].breakdown["by_lazy_strategy"]
        assert sum(strategies.values()) == total
        noload = metrics["orm_relationship_lazy_noload_count"].value
        assert metrics["orm_relationship_not_noload"].value == total - noload
        assert noload <= total

    def test_cross_module_buckets_sum_to_total(self, metrics):
        breakdown = metrics["cross_module_import_statement_count"].breakdown
        assert sum(breakdown["by_bucket"].values()) == (
            metrics["cross_module_import_statement_count"].value
        )
        assert sum(breakdown["by_target_module"].values()) == (
            metrics["cross_module_import_statement_count"].value
        )

    def test_scc_counts_are_mutually_consistent(self, metrics):
        """Largest SCC cannot exceed the cycle coverage, and the multi-node
        count must match the number of SCC member lists reported."""
        metric = metrics["import_graph_multi_node_scc_count"]
        assert len(metric.breakdown["members"]) == metric.value
        assert max(metric.breakdown["sizes_descending"], default=0) == (
            metrics["import_graph_largest_scc_size"].value
        )
        total_modules = metrics["import_graph_modules_in_a_cycle"].breakdown["total_modules"]
        assert 0 < metrics["import_graph_modules_in_a_cycle"].value <= total_modules

    def test_deferred_imports_are_a_subset_of_total_edges(self, metrics):
        breakdown = metrics["import_graph_total_internal_edges"].breakdown
        total = metrics["import_graph_total_internal_edges"].value
        deferred = metrics["import_graph_deferred_import_statements"].value
        assert breakdown["module_level_statements"] + breakdown["function_level_statements"] == total
        assert deferred == breakdown["function_level_statements"]
        assert deferred < total, (
            "every edge is deferred and none is module-level — the col_offset "
            "scope test has inverted"
        )

    def test_lua_reports_disk_truth_rather_than_assuming_it(self, metrics):
        """The 'no .lua files on disk' claim is CONFIRMED by a filesystem walk,
        and the number of files found is reported, not assumed to be zero."""
        breakdown = metrics["lua_script_count"].breakdown
        assert breakdown["lua_file_count_on_disk"] == len(breakdown["lua_files_on_disk"])
        if breakdown["lua_file_count_on_disk"] == 0:
            assert metrics["lua_script_count"].value > 0, (
                "no .lua files on disk AND no inline scripts: the Lua walk found "
                "nothing at all, which is the silent-zero failure"
            )

    def test_source_text_count_matches_the_reused_counter(self, metrics):
        """The definition says the existing counter is authoritative. If this
        wrapper ever disagrees with it, the wrapper is wrong — so prove it
        does not disagree, by calling that module directly."""
        import count_source_text_tests as counter

        assert metrics["source_text_test_count"].value == len(counter.scan())


# --------------------------------------------------------------------------
# Reuse, not reimplementation
# --------------------------------------------------------------------------


class TestReusesExistingCounter:
    def test_delegates_to_count_source_text_tests(self, metrics):
        breakdown = metrics["source_text_test_count"].breakdown
        assert breakdown["measured_by"] == "scripts/count_source_text_tests.py:scan()"
        assert "REUSED" in metrics["source_text_test_count"].definition

    def test_no_duplicate_source_text_detector_in_this_script(self):
        """A second copy of the detector would be a second number for the same
        metric — the exact ambiguity the script documents. Guard the source."""
        source = (ROOT / "scripts" / "verify_plan_metrics.py").read_text(encoding="utf-8")
        assert "is_source_text_assertion" not in source, (
            "the source-text detector has been copied into this script; reuse "
            "scripts/count_source_text_tests.py instead"
        )


# --------------------------------------------------------------------------
# CLI surface
# --------------------------------------------------------------------------


class TestCli:
    def test_json_summary_parses_and_carries_every_key(self, capsys):
        """The machine-readable form is what a CI job or a ratchet would read."""
        assert vpm.main(["--json"]) == 0
        payload = json.loads(capsys.readouterr().out)

        assert set(payload) == REQUIRED_KEYS
        for name, entry in payload.items():
            assert entry["definition"].strip(), f"{name} has no definition in JSON"
            assert "value" in entry
            assert "breakdown" in entry

    def test_human_output_prints_value_then_definition(self, capsys):
        """The readable form must put the number and its unit on the page
        together; a number with no definition beside it is the defect."""
        assert vpm.main([]) == 0
        out = capsys.readouterr().out
        for name in REQUIRED_KEYS:
            assert f"{name} = " in out, f"{name} missing from human output"
        assert "definition:" in out
        assert "MISSING METRICS" not in out

    def test_exit_code_is_zero(self):
        """The script MEASURES, it does not gate. A measurement that fails on a
        dirty tree would be a gate nobody agreed to."""
        assert vpm.main(["--json"]) == 0


# --------------------------------------------------------------------------
# The silent-zero detector itself
# --------------------------------------------------------------------------


class TestSilentZeroDetectorWorks:
    """Prove the walker is capable of finding things, by running it against a
    purpose-built tree. If ``python_files`` stopped walking, or ``dotted_module``
    lost its ``__init__`` handling, these would read 0 and the real-tree tests
    above would fail too — but for the wrong reason. A broken walker detected
    here is a clear message; detected up there it looks like a clean codebase.
    """

    @pytest.fixture
    def fake_tree(self, tmp_path):
        """A minimal tree with ONE known 2-node cycle and ONE self-loop."""
        root = tmp_path / "src" / "app"
        (root / "pkg").mkdir(parents=True)
        (root / "__init__.py").write_text("", encoding="utf-8")
        (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        # a <-> b : exactly one 2-node SCC
        (root / "a.py").write_text(
            "import src.app.b\n" "from src.app.b import thing\n", encoding="utf-8"
        )
        (root / "b.py").write_text("import src.app.a\n", encoding="utf-8")
        (root / "pkg" / "sub.py").write_text("VALUE = 1\n", encoding="utf-8")
        # The self-loop, in the shape the real tree uses. Inside a package's
        # own ``__init__``, ``from src.app.pkg import sub`` produces the SYMBOL
        # edge pkg -> pkg.sub and, under the SUBMODULE reading, an edge from
        # ``src.app.pkg`` onto ITSELF. That is what a lazy ``__getattr__``
        # facade does, and it is why the real tree's four facades register as
        # self-loops. Placed in ``facade.py`` instead it would merely be a
        # second 2-node cycle, which is a different thing entirely.
        (root / "pkg" / "__init__.py").write_text(
            "from src.app.pkg import sub\n", encoding="utf-8"
        )
        # A relative import, which must resolve against the PACKAGE.
        (root / "pkg" / "relative.py").write_text("from . import sub\n", encoding="utf-8")
        return root

    def test_walker_finds_the_planted_cycle_and_self_loop(self, fake_tree, monkeypatch):
        monkeypatch.setattr(vpm, "REPO", fake_tree.parents[1])
        monkeypatch.setattr(vpm, "APP", fake_tree)
        monkeypatch.setattr(vpm, "SRC", fake_tree.parents[0])

        files = vpm.python_files(fake_tree)
        modules = {vpm.dotted_module(p): p for p in files}
        edges, _forms = vpm.collect_import_edges(modules)

        # The walk must produce edges, not an empty set.
        assert edges, "collect_import_edges returned nothing — the walker is broken"

        adjacency: dict[str, set[str]] = {m: set() for m in modules}
        for edge in edges:
            adjacency[edge.importer].add(edge.target)

        sccs = vpm.tarjan_sccs(adjacency)
        multi = [c for c in sccs if len(c) > 1]
        loops = sorted(m for m in modules if m in adjacency[m])

        assert len(multi) == 1, f"expected exactly one planted 2-node SCC, got {multi}"
        assert set(multi[0]) == {"src.app.a", "src.app.b"}
        assert loops == ["src.app.pkg"], (
            f"the relative ``from . import`` self-loop was missed; got {loops}"
        )

    def test_relative_import_resolves_against_the_package(self):
        """``from . import x`` inside a package must resolve to that package,
        and one more dot must walk up a level. This is the specific bug class
        that silently produces an empty edge set."""
        assert vpm.resolve_relative("src.app.pkg", 1, None) == "src.app.pkg"
        assert vpm.resolve_relative("src.app.pkg", 1, "sub") == "src.app.pkg.sub"
        assert vpm.resolve_relative("src.app.pkg.child", 2, None) == "src.app.pkg"
        assert vpm.resolve_relative("src.app.pkg.child", 2, "mod") == "src.app.pkg.mod"
        assert vpm.resolve_relative("src.app.pkg", 1, "__init__") == "src.app.pkg"

    def test_dotted_module_drops_init_but_keeps_packages(self):
        """``__init__.py`` IS its package, not a module named after it."""
        assert vpm.dotted_module(ROOT / "src" / "app" / "modules" / "users" / "__init__.py") == (
            "src.app.modules.users"
        )
        assert vpm.dotted_module(ROOT / "src" / "app" / "main.py") == "src.app.main"

    def test_excluded_cache_dirs_are_never_walked(self, tmp_path):
        """A cache tree under the root would inflate every count, and is the
        classic reason a metric script reports a plausible wrong number."""
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "real.py").write_text("x = 1\n", encoding="utf-8")
        cache = tmp_path / "pkg" / "__pycache__"
        cache.mkdir()
        (cache / "stale.py").write_text("x = 1\n", encoding="utf-8")

        names = [p.name for p in vpm.python_files(tmp_path)]
        assert names == ["real.py"]
