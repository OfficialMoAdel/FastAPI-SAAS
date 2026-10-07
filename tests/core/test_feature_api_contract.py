"""s92p.3: the widened contract is real, and its ignore list cannot grow silently.

The contract now covers all of ``src.app.modules`` instead of two named
service modules. Two properties make that worth having:

* it CATCHES a new violation — an allowlist that hides everything would be
  worse than a red contract, so this injects a real ``service -> api`` import
  and asserts the gate goes BROKEN;
* the ignore list does not grow silently — an unenforced, undocumented edge
  is the failure this project keeps meeting, so every entry must be a router
  (the DI contract) and the router set is pinned by count and by membership.

The ignore list is 31 entries because routers legitimately obtain the request
context from the api layer. A non-router entry appearing there is the debt the
widening was meant to expose.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "src/.importlinter"
MODULES = REPO / "src/app/modules"

#: Invoke the `lint-imports` console script through the interpreter running
#: this test. Hardcoding `.venv/Scripts/lint-imports.exe` is a Windows-only
#: layout, so on Linux the path does not exist and the gate failed with
#: FileNotFoundError before it could report a single contract. Calling the
#: entry point directly is layout-independent AND guarantees the gate runs
#: against the same import-linter install the rest of the suite is using.
LINT_IMPORTS_ARGV = [sys.executable, "-c", "from importlinter.cli import lint_imports_command as c; c()"]

#: Every ignore entry in the feature->api contract, parsed from the config.
_CONTRACT_START = "[importlinter:contract:forbidden-feature-implementation-to-api]"


def _contract_block() -> str:
    text = CONFIG.read_text(encoding="utf-8")
    start = text.index(_CONTRACT_START)
    end = text.index("[importlinter:contract:", start + len(_CONTRACT_START))
    return text[start:end]


def _ignore_entries() -> list[str]:
    block = _contract_block()
    out, in_list = [], False
    for line in block.splitlines():
        if line.strip().startswith("ignore_imports"):
            in_list = True
            continue
        if not in_list:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "->" in stripped:
            out.append(stripped)
    return out


def _is_router(module: str) -> bool:
    """A module is a router when it is named router or lives in routers/."""
    return module.endswith(".router") or ".routers." in module


def _lint_imports() -> tuple[int, str]:
    result = subprocess.run(
        LINT_IMPORTS_ARGV,
        cwd=REPO / "src",
        capture_output=True,
        text=True,
        timeout=600,
    )
    return result.returncode, result.stdout + result.stderr


class TestContractCoversAllModules:
    def test_source_scope_is_the_whole_package(self) -> None:
        block = _contract_block()
        assert "src.app.modules\n" in block or "    src.app.modules" in block, (
            "the contract must be scoped to src.app.modules, not a named subset"
        )

    def test_old_narrow_scope_is_gone(self) -> None:
        block = _contract_block()
        assert "src.app.modules.posts.service" not in block
        assert "src.app.modules.billing.subscription_service" not in block


class TestContractIsGreen:
    def test_contracts_pass(self) -> None:
        code, out = _lint_imports()
        assert "0 broken" in out, f"contracts failing:\n{out}"
        assert code == 0, f"lint-imports exited {code}:\n{out}"


class TestFalsifiability:
    """The gate must catch a NEW violation, or the allowlist is theatre."""

    def test_a_new_service_to_api_import_would_be_caught(self) -> None:
        """Inject a real violation into a service and confirm the gate goes red."""
        target = MODULES / "feature_flags" / "service.py"
        original = target.read_bytes()
        try:
            target.write_text(
                original.decode("utf-8")
                + "\nfrom src.app.api.deps import get_current_user  # noqa: F401\n",
                encoding="utf-8",
            )
            code, out = _lint_imports()
            assert "BROKEN" in out, f"a new service->api import was NOT caught:\n{out}"
            assert code != 0
        finally:
            target.write_bytes(original)

    def test_contracts_are_green_again_after_revert(self) -> None:
        """The injection test must not leave the tree dirty."""
        code, out = _lint_imports()
        assert "0 broken" in out, f"contracts still failing after revert:\n{out}"


class TestIgnoreListIsDisciplined:
    """Every entry must be a router, and the set is pinned."""

    def test_every_ignore_entry_is_a_router(self) -> None:
        """A non-router entry is real debt, not the DI contract."""
        offenders = [e for e in _ignore_entries() if not _is_router(e.split(" -> ")[0])]
        assert not offenders, (
            "non-router modules in the feature->api ignore list - these are the "
            f"debt the widening was meant to surface: {offenders}"
        )

    def test_ignore_count_is_pinned(self) -> None:
        """A silently grown list is the failure this file exists to prevent."""
        entries = _ignore_entries()
        assert len(entries) == 31, (
            f"ignore list has {len(entries)} entries, expected 31. If a router "
            "grew, confirm it is still a router and update this pin deliberately."
        )

    def test_the_list_is_justified_as_a_group(self) -> None:
        """Undocumented and unenforced are the same thing.

        The entries are ONE class of edge (routers taking their DI from the
        api layer), so the justification is a block comment above the group.
        31 identical comments would be noise, not documentation.
        """
        block = _contract_block()
        entries = _ignore_entries()
        first = block.index(entries[0])
        preamble = block[block.index("ignore_imports") : first]
        assert "# ROUTERS ARE THE DI CONTRACT" in preamble, (
            "the ignore list must be preceded by the justification for what the "
            "entries are"
        )
        assert preamble.count("#") >= 3, (
            "the justification must explain WHY routers are exempt, not just name them"
        )


class TestDiStillWorks:
    """COUNTER-TEST: the routers the contract ignores must still import."""

    @pytest.mark.parametrize(
        "module",
        [
            "src.app.modules.users.router",
            "src.app.modules.auth.router",
            "src.app.modules.billing.router",
            "src.app.modules.tenants.router",
        ],
    )
    def test_router_imports_remain_legal(self, module: str) -> None:
        """A router's api-layer import is expected, and the contract says so."""
        assert any(
            _is_router(e.split(" -> ")[0]) and module in e for e in _ignore_entries()
        ), f"{module} is not recorded in the ignore list but imports the api layer"
