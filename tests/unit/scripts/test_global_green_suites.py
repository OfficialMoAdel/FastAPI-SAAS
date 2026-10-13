"""The five global regression suites, owned rather than assumed.

Unified plan §13 names five suites that must stay green throughout the programme,
and §14 Gate B requires it. Before this file they were named in the plan and
tracked by NO bead, which meant two failures at once: nothing owned keeping them
green, and nothing recorded what breaks them.

They are not passive. Phase-0 Step 1 edits files these suites read - H-1 touches
``driver_count_io.py`` whose units bug they encode, H-4 fixes or deletes
``prof_request_path.py``, H-6 revises the p95 expectation at
``locustfile.py:13-17`` and the in-file ``get_percentile``, H-7 moves the logout
request out of ``on_stop``. Every one of those edits can turn a suite red by
accident rather than by revealing anything.

Why a GUARD and not a wrapper
-----------------------------
CI runs a bare ``uv run pytest`` from ``src/``. Collection reaches these five
today, but nothing makes that a guarantee: rename a file, move it under a
different tree, or mark it skipped, and the suite stops running with no failure
anywhere - the tests job goes green having tested less. That is the failure mode
this file exists to prevent, and it is why the assertions below are about
PRESENCE and WIRING rather than about the benchmarks' own results.

The five, and why each is here:

``tests/unit/test_middleware_order.py``
    Middleware order is invisible in a diff and total in behaviour. H-1 reorders
    guards; a suite that pins the order is the only thing that notices.
``tests/benchmarks/test_nrgi3_scan_cost.py``
    Encodes the scan-cost figure ADR-0008 found stale, also present in
    ``attack_detection.py``. S-7 touches those comments.
``tests/benchmarks/test_p2_micros.py``
    The P-2 micro-cost ceilings. These are FIXED ceilings, not relative ones -
    ``new < old`` is not a pass criterion anywhere in this repository.
``tests/performance/test_equivalence.py``
    An optimisation that changes behaviour is not an optimisation.
``tests/unit/scripts/test_doc_gate_wiring.py``
    Proves the doc gates point at gates that exist. A dangling gate path is a gate
    that cannot fire.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The five suites, with what breaks each one. The reason is not decoration: a
#: future reader who deletes a suite should see what it was protecting before they
#: delete it, which is the entire failure mode this file addresses.
REQUIRED_SUITES: dict[str, str] = {
    "tests/unit/test_middleware_order.py": (
        "middleware order is invisible in a diff and total in behaviour; H-1 reorders guards"
    ),
    "tests/benchmarks/test_nrgi3_scan_cost.py": (
        "encodes the scan-cost figure ADR-0008 found stale, mirrored in attack_detection.py; S-7 touches it"
    ),
    "tests/benchmarks/test_p2_micros.py": (
        "the P-2 micro-cost ceilings; fixed values, never 'new < old'"
    ),
    "tests/performance/test_equivalence.py": (
        "an optimisation that changes behaviour is not an optimisation"
    ),
    "tests/unit/scripts/test_doc_gate_wiring.py": (
        "proves the doc gates point at gates that exist; a dangling path is a gate that cannot fire"
    ),
}


class TestTheFiveSuitesExist:
    @pytest.mark.parametrize("relative", sorted(REQUIRED_SUITES))
    def test_the_suite_is_present(self, relative: str) -> None:
        path = REPO_ROOT / relative
        assert path.is_file(), (
            f"{relative} is gone. It protected: {REQUIRED_SUITES[relative]}. "
            f"Plan §13 requires it green and §14 Gate B requires that it be checked - "
            f"if it is being retired, say so deliberately rather than by rename or move."
        )

    @pytest.mark.parametrize("relative", sorted(REQUIRED_SUITES))
    def test_the_suite_collects_tests(self, relative: str) -> None:
        """Present but empty is the same as absent, and easier to miss.

        A file that survives a rename but collects nothing leaves the job green
        and the coverage gone.
        """
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "def test_" in source, (
            f"{relative} collects no tests. It is present, so it looks owned, while "
            f"protecting nothing."
        )


class TestTheSuitesAreReachableByCollection:
    """CI runs a bare ``uv run pytest`` from the REPOSITORY ROOT.

    That detail is load-bearing and was got wrong at first: collected from
    ``src/`` this repository yields ZERO tests, while from the root it collects
    ~9,150. pytest resolves its rootdir from the invocation directory plus the
    config file, and the config lives at the root, so the root is the only
    directory from which the suite is reachable at all.

    So the guarantee to protect is not a ``testpaths`` entry - this repository
    sets none, and auto-discovery from the root is what finds the tests. It is
    that no collection-narrowing setting is introduced that would silently drop
    these five while leaving everything green.
    """

    @staticmethod
    def _ini() -> dict:
        data = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        return data.get("tool", {}).get("pytest", {}).get("ini_options", {})

    def test_pytest_config_lives_at_the_repository_root(self) -> None:
        """The root is where the config is, and therefore the rootdir.

        Moving or removing it does not fail a test anywhere - it just makes the
        bare ``pytest`` collect nothing, which is green.
        """
        assert (REPO_ROOT / "pyproject.toml").is_file()
        assert "[tool.pytest.ini_options]" in (
            REPO_ROOT / "pyproject.toml"
        ).read_text(encoding="utf-8"), (
            "the pytest ini_options block is gone from the root pyproject.toml. "
            "pytest resolves its rootdir from the config file, so without it a bare "
            "`pytest` collects nothing and every suite is skipped without failing."
        )

    def test_no_testpaths_excludes_any_required_suite(self) -> None:
        ini = self._ini()
        configured = str(ini.get("testpaths", "")).strip()
        if not configured:
            # No testpaths: auto-discovery from the root reaches tests/. Fine.
            return
        for relative in REQUIRED_SUITES:
            assert relative.split("/")[0] in configured or "tests" in configured, (
                f"testpaths={configured!r} no longer reaches {relative}. CI runs a "
                f"bare `uv run pytest` from the root, so anything outside testpaths "
                f"is silently not run."
            )

    def test_no_norecursedirs_excludes_any_required_suite(self) -> None:
        excluded = str(self._ini().get("norecursedirs", ""))
        if not excluded:
            return
        for relative in REQUIRED_SUITES:
            # Compare on the directory beneath tests/, which is the form
            # norecursedirs entries take.
            directory = str(Path(relative).parent).removeprefix("tests/")
            assert not directory or directory not in excluded, (
                f"norecursedirs={excluded!r} excludes {directory!r}, which contains "
                f"{relative}. The suite will not run."
            )


class TestTheSuitesAreActuallyWiredIntoCI:
    """The file existing is not the same as the file running."""

    @staticmethod
    def _workflow() -> str:
        return (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8"
        )

    def test_backend_ci_still_runs_the_test_suite(self) -> None:
        workflow = self._workflow()
        assert "pytest" in workflow, (
            "tests.yml no longer invokes pytest; the five suites are green only in "
            "the sense that nothing runs them."
        )

    def test_no_marker_silences_them(self) -> None:
        """A blanket skip would turn five required suites into five optional ones.

        Checked at the source level rather than by running them, because a skip
        marker is precisely the thing that makes a suite stop running while
        everything stays green.
        """
        for relative in REQUIRED_SUITES:
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            assert "pytestmark" not in source or "skip" not in source.split(
                "pytestmark", 1
            )[1].split("\n", 1)[0], (
                f"{relative} carries a module-level skip. Plan §13 requires it "
                f"green, not skipped."
            )
