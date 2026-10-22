"""The module-structure gate must BIND, and its allow-list must stay live.

``scripts/checks/module_structure.py`` enforces the modular-monolith
contract: every module under ``src/app/modules`` must carry
``router.py`` + ``service.py`` + (``models.py`` or ``schemas.py``), with
deviations spelled out in an explicit allow-list.

Two properties are pinned here, and both are properties of the GATE rather
than of any one module:

1. **The allow-list is live, not decorative.** Every ``ALLOWLIST`` key must
   resolve to a real module directory. A stale entry exempts a module that
   no longer exists, so the day that name comes back it is silently exempt
   again -- and the gate that was supposed to catch it says nothing. The
   entry used to be a WARNING, printed on every run, which is the failure
   mode the check exists to prevent: a permanent line of output that nobody
   reads. It is a FAILURE now, and this test says so.

2. **The gate binds.** A module missing ``service.py`` must fail the check
   with that module named, and the same tree with the file present must
   pass. The "broken" case is built in a temporary tree by pointing the
   checker at it, so no tracked file is edited and nothing is left behind.

The temp tree is the point of the exercise. Falsifying this gate by
editing a real module and editing it back has bitten this repo before.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[3]
CHECKER = REPO / "scripts" / "checks" / "module_structure.py"


def _load_checker() -> ModuleType:
    """Import the checker as a module (it is not on sys.path)."""
    spec = importlib.util.spec_from_file_location("_module_structure_gate", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_module_tree(root: Path, name: str, *, with_service: bool) -> Path:
    """Create one module directory under ``root``."""
    module_dir = root / "modules" / name
    module_dir.mkdir(parents=True)
    (module_dir / "router.py").write_text("", encoding="utf-8")
    (module_dir / "schemas.py").write_text("", encoding="utf-8")
    if with_service:
        (module_dir / "service.py").write_text("", encoding="utf-8")
    return module_dir


class TestAllowListIsLive:
    """Every allow-list entry must resolve to a real module."""

    def test_every_allowlist_entry_matches_a_real_module(self) -> None:
        checker = _load_checker()
        modules_dir = checker.MODULES_DIR
        existing = {d.name for d in modules_dir.iterdir() if d.is_dir() and d.name != "__pycache__"}
        stale = sorted(name for name in checker.ALLOWLIST if name not in existing)
        assert not stale, (
            f"stale allow-list entr(ies) in {CHECKER.name}: {stale} — "
            "an entry for a module that does not exist exempts nothing today "
            "and silently exempts the module the day that name returns"
        )

    def test_stale_entry_fails_the_gate(self) -> None:
        """A stale entry must exit non-zero, not print a warning."""
        checker = _load_checker()
        original_allowlist = dict(checker.ALLOWLIST)
        original_modules_dir = checker.MODULES_DIR
        try:
            checker.ALLOWLIST = {**original_allowlist, "cache_warming": "no such module"}
            checker.MODULES_DIR = original_modules_dir
            exit_code = checker.main()
        finally:
            checker.ALLOWLIST = original_allowlist
            checker.MODULES_DIR = original_modules_dir

        assert exit_code == 1, "a stale allow-list entry must fail the gate, not warn"

    def test_stale_entry_is_named_in_the_output(self, capsys: object) -> None:
        checker = _load_checker()
        original_allowlist = dict(checker.ALLOWLIST)
        original_modules_dir = checker.MODULES_DIR
        try:
            checker.ALLOWLIST = {**original_allowlist, "cache_warming": "no such module"}
            checker.MODULES_DIR = original_modules_dir
            checker.main()
        finally:
            checker.ALLOWLIST = original_allowlist
            checker.MODULES_DIR = original_modules_dir

        out = capsys.readouterr().out  # type: ignore[attr-defined]
        assert "cache_warming" in out, f"the failing entry must be named:\n{out}"


class TestGateBinds:
    """A module missing service.py must fail; the same tree must pass."""

    def test_missing_service_fails_and_names_the_module(self, tmp_path: Path, capsys: object) -> None:
        checker = _load_checker()
        original_modules_dir = checker.MODULES_DIR
        original_allowlist = dict(checker.ALLOWLIST)
        try:
            _make_module_tree(tmp_path, "widgets", with_service=False)
            checker.MODULES_DIR = tmp_path / "modules"
            checker.ALLOWLIST = {}
            exit_code = checker.main()
        finally:
            checker.MODULES_DIR = original_modules_dir
            checker.ALLOWLIST = original_allowlist

        out = capsys.readouterr().out  # type: ignore[attr-defined]
        assert exit_code == 1, f"a module missing service.py must fail:\n{out}"
        assert "widgets" in out, f"the failing module must be named:\n{out}"
        assert "service.py" in out, f"the missing file must be named:\n{out}"

    def test_conforming_tree_passes(self, tmp_path: Path) -> None:
        checker = _load_checker()
        original_modules_dir = checker.MODULES_DIR
        original_allowlist = dict(checker.ALLOWLIST)
        try:
            _make_module_tree(tmp_path, "widgets", with_service=True)
            checker.MODULES_DIR = tmp_path / "modules"
            checker.ALLOWLIST = {}
            exit_code = checker.main()
        finally:
            checker.MODULES_DIR = original_modules_dir
            checker.ALLOWLIST = original_allowlist

        assert exit_code == 0, "a conforming module tree must pass the gate"

    def test_the_real_tree_passes(self) -> None:
        """The repo's own module tree conforms (F1 fixed the last gap)."""
        checker = _load_checker()
        assert checker.main() == 0, "the real module tree violates the structure contract"
