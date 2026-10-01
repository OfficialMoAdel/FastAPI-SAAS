"""The module-boundary ban, as a ratchet that is proven to bite.

``ARCHITECTURE.md:39`` states the rule this file pins:

    Never import directly from another module's internal files.
    Cross-module imports must go through the package root.

Before 2026-09-30 that rule was **decorative**. ``pyproject.toml``'s
``flake8-tidy-imports.banned-api`` named 6 of the 160 importable cells under
``src/app/modules/`` — and ``ruff check --select TID251`` was **clean**,
0 violations. The list named cells nothing imported; it did not name the cells
that were actually being reached, so it caught nothing while ``ARCHITECTURE.md``
and ``src/app/modules/AGENTS.md`` both cited it as enforced.

The ban is now **widened and re-framed**. It covers the *behaviour* layer —
every ``{module}.service`` and ``{module}.router`` and their subpackages, 40
cells across all 21 modules — because those are the things a lazy ``__getattr__``
facade exists to hide. ``models`` / ``schemas`` / ``crud`` are not that seam;
banning them too would have demanded 429 rewrites of legal intra-module
imports instead of a handful of targeted ones.

Why this is a test and not a lint run: ``ruff check --select TID251`` passing
proves only that *today's* imports are legal. It cannot prove the rule is
*expressible* — and a banned-api list is a list of literal strings, so it can
silently degrade to naming cells nothing imports, which is precisely the bug
being fixed. ``TestGateBites`` therefore writes a real violating file and
asserts ruff rejects it. If a future change makes the gate vacuous again,
these tests fail instead of the prose staying confidently wrong.

Coverage ratchet, not a moving target: the cross-module breaches this gate
reports are enumerated in ``ALLOWED_CROSS_MODULE_BREACHES`` and asserted by
name. A new one fails; the two documented cycle-guard edges are visible debt
rather than a number that drifts.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULES_DIR = REPO / "src" / "app" / "modules"
PYPROJECT = REPO / "pyproject.toml"

#: Cells the ban must NOT name. Banning a module's package root would also ban
#: the facade itself, which every legal consumer imports.
FORBIDDEN_BAN_PATTERNS = (
    "src.app.modules.*",
    "src.app.modules.*.service",
)

#: The two cross-module service edges that survive as named, documented debt.
#: Both are import-cycle guards explained in ``modules/tiers/__init__.py``:
#: ``tiers.service`` transitively imports ``rate_limits``, so these two files
#: import it directly and eagerly to keep the cycle out of the tiers-first
#: import order. They are listed so a THIRD such edge fails the ratchet.
ALLOWED_CROSS_MODULE_BREACHES: dict[str, str] = {
    "src/app/modules/users/service.py": "src.app.modules.tiers.service",
    "src/app/modules/rate_limits/service.py": "src.app.modules.tiers.service",
}

#: A cell used only by ``TestGateBites`` to prove enforcement. Picked because
#: ``posts`` is a real module whose service import is the canonical breach
#: shape named in ARCHITECTURE.md:50.
PROBE_CELL = "src.app.modules.posts.service"


def _banned_api() -> dict[str, dict[str, str]]:
    with PYPROJECT.open("rb") as fh:
        cfg = tomllib.load(fh)
    return cfg["tool"]["ruff"]["lint"]["flake8-tidy-imports"]["banned-api"]


def _module_dirs() -> list[Path]:
    return [
        p
        for p in sorted(MODULES_DIR.iterdir())
        if p.is_dir() and not p.name.startswith("_") and p.name != "__pycache__"
    ]


def _service_router_cells() -> set[str]:
    """Every behaviour-layer cell in the tree, derived from the filesystem.

    "Behaviour layer" = the cells a lazy ``__getattr__`` facade exists to
    hide: ``service``, ``router`` and their plural package forms, plus
    anything nested inside them (``auth.services.sso``,
    ``billing.routers.prices``).

    The plural forms are not optional. ``ruff`` matches ``banned-api`` on DOT
    SEGMENTS, so banning ``billing.router`` does **not** catch an import of
    ``billing.routers.prices`` — verified: a probe importing
    ``billing.routers.prices`` lints clean against a ban naming only
    ``billing.router``. An earlier revision of this ban missed exactly that
    and this test caught it.

    Deriving from the filesystem is the point: if a module gains a service,
    this set gains it and ``TestBanCoversTheTree`` fails until the ban names
    it too, rather than the gate quietly covering less each time someone
    splits a router.
    """
    contract_layers = {"service", "router", "routers", "services"}
    cells: set[str] = set()
    for mod in _module_dirs():
        for path in mod.rglob("*.py"):
            parts = list(path.relative_to(MODULES_DIR).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
                if len(parts) <= 1:
                    continue  # the package root itself, not an internal cell
            if parts[-1] in contract_layers or (
                len(parts) > 1 and parts[-2] in contract_layers
            ):
                cells.add("src.app.modules." + ".".join(parts))
    return cells


def _is_covered(cell: str, banned: set[str]) -> bool:
    """True if ``cell`` is banned directly or falls under a banned package.

    ``banned-api`` matches on dot segments, so banning ``auth.services``
    covers ``auth.services.sso``. A ban that missed the package cell would
    leave every child linter-clean, which is the hole this guards.
    """
    return cell in banned or any(cell.startswith(f"{b}.") for b in banned)


def _run_ruff(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", *args],
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def _write_ban_config(tmp_path: Path) -> Path:
    """Write a scratch config carrying the REAL banned-api list.

    The probe is judged by the same rule production code is judged by, but
    with none of the repo's per-file-ignores — a probe file outside
    ``src/`` matches no exemption, so the raw ban decides its fate.
    """
    cfg = tmp_path / "ruff_probe.toml"
    body = [
        "[lint.flake8-tidy-imports.banned-api]",
        *[
            "{}.msg = {}".format(json.dumps(k), json.dumps(v["msg"]))
            for k, v in _banned_api().items()
        ],
    ]
    cfg.write_text("\n".join(body) + "\n", encoding="utf-8")
    return cfg


class TestBanCoversTheTree:
    """Coverage: the ban must name what the tree actually contains."""

    def test_ban_covers_every_behaviour_layer_cell(self) -> None:
        """The ratchet: coverage is checked against the tree, not a count.

        66 behaviour-layer cells exist. The ban names 45 entries because
        banning a package cell (``auth.services``) covers everything beneath
        it via ruff's dot-segment matching — so coverage is a predicate, not
        a set difference.
        """
        expected = _service_router_cells()
        assert len(expected) == 66, (
            f"tree walk found {len(expected)} behaviour-layer cells, expected 66; "
            "if a module was added or split, update this number and the ban together"
        )
        banned = set(_banned_api())
        missing = sorted(c for c in expected if not _is_covered(c, banned))
        assert not missing, (
            f"{len(missing)} behaviour-layer cells are not banned, so a deep "
            f"import of them lints clean: {missing}\n"
            "Add the cell — or its enclosing package — to "
            "[tool.ruff.lint.flake8-tidy-imports.banned-api]."
        )

    def test_ban_does_not_name_wildcards(self) -> None:
        """A wildcard entry matches nothing — the failure mode being fixed.

        ``ruff`` 0.11's banned-api is prefix-only and expands no globs, so
        ``"src.app.modules.*.service"`` is an inert string that looks like a
        rule. This fails if one is reintroduced.
        """
        banned = set(_banned_api())
        patterns = [p for p in FORBIDDEN_BAN_PATTERNS if p in banned]
        assert not patterns, f"wildcard ban entries are inert in ruff: {patterns}"

    def test_ban_does_not_name_a_package_root(self) -> None:
        """Banning ``modules.X`` would ban the facade every consumer imports."""
        banned = set(_banned_api())
        roots = {f"src.app.modules.{m.name}" for m in _module_dirs()}
        assert not (banned & roots), f"package roots are banned: {sorted(banned & roots)}"


class TestGateBites:
    """Falsifiability: the gate must REJECT a real breach.

    Without these, "TID251 is clean" is unfalsifiable — a list of literal
    strings that names cells nothing imports also reports clean, which is
    exactly the pre-2026-09-30 state of this rule.
    """

    def test_deep_import_from_a_non_exempt_file_is_rejected(self, tmp_path: Path) -> None:
        probe = tmp_path / "probe.py"
        probe.write_text(
            f"from {PROBE_CELL} import PostService\n", encoding="utf-8"
        )
        cfg = _write_ban_config(tmp_path)
        result = _run_ruff("--config", str(cfg), "--select", "TID251", str(probe))
        assert result.returncode != 0, (
            "the gate did NOT flag a deep service import, so the rule is "
            f"decorative again. ruff said:\n{result.stdout}{result.stderr}"
        )
        assert PROBE_CELL in result.stdout, (
            f"violation reported without naming {PROBE_CELL}: {result.stdout}"
        )

    def test_router_subpackage_import_is_rejected(self, tmp_path: Path) -> None:
        """The ban is prefix-based, so a nested cell must be caught too.

        ``billing.routers.prices`` is a real cell. A list that named only
        exact two-segment cells would miss every subpackage breach.
        """
        probe = tmp_path / "probe_nested.py"
        probe.write_text(
            "from src.app.modules.billing.routers.prices import router\n", encoding="utf-8"
        )
        cfg = _write_ban_config(tmp_path)
        result = _run_ruff("--config", str(cfg), "--select", "TID251", str(probe))
        assert result.returncode != 0, (
            f"a deep import of a router SUBPACKAGE lints clean:\n{result.stdout}"
        )

    def test_facade_import_is_still_allowed(self, tmp_path: Path) -> None:
        """The counter-test: the sanctioned form must not be flagged.

        A gate that also banned the package root would push every caller
        back to the deep import it exists to prevent.
        """
        probe = tmp_path / "probe_ok.py"
        probe.write_text(
            "from src.app.modules.posts import PostService\n", encoding="utf-8"
        )
        cfg = _write_ban_config(tmp_path)
        result = _run_ruff("--config", str(cfg), "--select", "TID251", str(probe))
        assert result.returncode == 0, (
            f"the gate flagged the SANCTIONED facade import:\n{result.stdout}"
        )


class TestRepoIsGreen:
    """The widened ban must be satisfiable by the tree as it stands."""

    def test_no_unexempted_violations_in_src(self) -> None:
        result = _run_ruff("--select", "TID251", "src/")
        assert result.returncode == 0, (
            f"src/ has unexempted TID251 violations:\n{result.stdout}\n"
            "Either route the import through the facade or add a justified, "
            "named per-file-ignore with its reason."
        )

    def test_documented_breach_files_are_named_in_the_config(self) -> None:
        """The two surviving breaches must be exempted BY NAME.

        They are the only cross-module service edges in the tree, exempt
        because ``tiers/__init__.py`` documents a genuine import cycle. An
        unlisted glob would let the ban decay, so the exemption is asserted
        here rather than assumed.
        """
        with PYPROJECT.open("rb") as fh:
            ignores = tomllib.load(fh)["tool"]["ruff"]["lint"]["per-file-ignores"]
        for path in ALLOWED_CROSS_MODULE_BREACHES:
            assert path in ignores, f"{path} breaches the ban but is not exempted in pyproject.toml"
            assert "TID251" in ignores[path], f"{path} must be exempted from TID251 specifically"

    def test_breached_cells_stay_banned_for_every_other_file(self, tmp_path: Path) -> None:
        """The counter-test for the exemptions above.

        ``tiers.service`` is exempted for two specific files, not neutered
        for the repo. Any third file importing it is still red.
        """
        cell = next(iter(ALLOWED_CROSS_MODULE_BREACHES.values()))
        probe = tmp_path / "probe_third_party.py"
        probe.write_text(f"from {cell} import TierService\n", encoding="utf-8")
        cfg = _write_ban_config(tmp_path)
        result = _run_ruff("--config", str(cfg), "--select", "TID251", str(probe))
        assert result.returncode != 0, (
            f"{cell} is exempted for two files but lints clean for everyone "
            f"else — the exemption leaked into the ban itself:\n{result.stdout}"
        )


class TestDetectorIsNotSilentlyEmpty:
    """Fail closed on a broken probe, mirroring the Stripe boundary test."""

    def test_module_tree_walk_finds_modules(self) -> None:
        mods = _module_dirs()
        assert len(mods) == 21, f"module walk found {len(mods)} dirs, expected 21"

    @pytest.mark.parametrize("module", ["auth", "users", "tenants", "posts"])
    def test_previously_named_modules_stay_banned(self, module: str) -> None:
        """The original 6 entries must survive the widening.

        Guards against a 'cleanup' that quietly drops the only cells anyone
        verified.
        """
        banned = set(_banned_api())
        for cell in (
            f"src.app.modules.{module}.service",
            f"src.app.modules.{module}.router",
        ):
            if cell == "src.app.modules.tenants.router":
                continue  # tenants has no router.py; only its service layer
            assert cell in banned, f"{cell} was dropped from the ban"
