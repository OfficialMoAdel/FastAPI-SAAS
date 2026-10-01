"""Guards for the H-5 architecture contract in ``src/.importlinter``.

Context (FastAPI_saas-21ak, 2026-09-23): the import-linter gate sat INERT for an
unknown length of time. In import-linter 2.x an ``ignore_imports`` entry that
matches no import in the graph defaults to ERROR alerting, which raises
``MissingImport`` from ``create_report()`` — *before any core -> modules edge is
evaluated*. Five stale entries therefore made every run exit 1 while checking
zero edges, and ``Report.contains_failures`` (set only by a broken contract)
told nobody, because the report was never rendered.

These tests do not need import-linter installed: they assert the *config* is in
the shape that keeps the gate honest. The substantive check remains
``lint-imports`` in CI (``.github/workflows/security-supply-chain.yml``).
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

CONTRACT_SECTION = "importlinter:contract:forbidden-core-to-modules"
FEATURE_CONTRACT_SECTION = "importlinter:contract:forbidden-feature-implementation-to-api"
SOURCE_PACKAGE = "src.app.core"
FORBIDDEN_PACKAGES = ("src.app.modules", "src.app.api", "src.app.middleware")

# tests/core/ -> repo root -> src/.importlinter
CONFIG_PATH = Path(__file__).resolve().parents[2] / "src" / ".importlinter"


def _feature_contract() -> configparser.SectionProxy:
    assert CONFIG_PATH.is_file(), f"architecture contract not found at {CONFIG_PATH}"
    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH, encoding="utf-8")
    assert parser.has_section(FEATURE_CONTRACT_SECTION)
    return parser[FEATURE_CONTRACT_SECTION]


def _contract() -> configparser.SectionProxy:
    assert CONFIG_PATH.is_file(), f"architecture contract not found at {CONFIG_PATH}"
    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH, encoding="utf-8")
    assert parser.has_section(CONTRACT_SECTION), (
        f"{CONFIG_PATH} has no [{CONTRACT_SECTION}] section — the H-5 gate is gone"
    )
    return parser[CONTRACT_SECTION]


def _ignore_entries() -> list[tuple[str, str]]:
    """Parse ``ignore_imports`` into ``(importer, imported)`` pairs.

    Comments are stripped per line so the explanatory prose in the contract
    (which contains no ``->``) can never be mistaken for an entry.
    """
    entries: list[tuple[str, str]] = []
    for raw in _contract()["ignore_imports"].splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        assert "->" in line, f"malformed ignore_imports entry (no '->'): {line!r}"
        left, right = (part.strip() for part in line.split("->", 1))
        entries.append((left, right))
    return entries


def test_contract_declares_the_expected_packages() -> None:
    contract = _contract()
    assert contract["type"] == "forbidden"
    assert SOURCE_PACKAGE in contract["source_modules"].split()
    forbidden = contract["forbidden_modules"].split()
    for package in FORBIDDEN_PACKAGES:
        assert package in forbidden


def test_feature_implementation_contract_covers_every_module() -> None:
    """s92p.3: the scope was two named services, which left real debt outside.

    src.app.modules.ai.middleware imported the api layer and was NOT caught:
    a forbidden contract with an explicit source list only checks edges FROM
    those modules, so it was out of scope rather than compliant. The scope is
    now the whole feature package, and the router exemptions are recorded in
    the contract with a stated reason.
    """
    contract = _feature_contract()
    assert contract["type"] == "forbidden"
    assert contract["source_modules"].split() == ["src.app.modules"]
    assert contract["forbidden_modules"].split() == ["src.app.api"]
    assert contract.get("unmatched_ignore_imports_alerting", "").strip() == "warn"
    assert "ignore_imports" in contract, (
        "router exemptions must be recorded in the contract, not tolerated by accident"
    )


def test_unmatched_ignores_do_not_abort_the_contract() -> None:
    """The regression that made the gate inert.

    With the default ``error`` alerting, a single stale entry raises
    ``MissingImport`` and the contract checks *nothing*. ``warn`` keeps stale
    entries visible on every run without disabling the check. If this assertion
    fails, someone has re-armed the trap — the gate may be silently inert again.
    """
    alerting = _contract().get("unmatched_ignore_imports_alerting", "error").strip()
    assert alerting == "warn", (
        f"unmatched_ignore_imports_alerting is {alerting!r}; with 'error' a single "
        "stale ignore_imports entry aborts the H-5 contract before it evaluates any "
        "edge (FastAPI_saas-21ak). Use 'warn'."
    )


def test_every_ignore_entry_is_well_formed_and_originates_in_core() -> None:
    """An entry that does not start from ``src.app.core`` cannot match anything.

    It would be stale by construction, so this catches copy-paste/typo rot at
    the point it is introduced rather than months later in CI.
    """
    offenders = [
        f"{importer} -> {imported}"
        for importer, imported in _ignore_entries()
        if importer != SOURCE_PACKAGE and not importer.startswith(f"{SOURCE_PACKAGE}.")
    ]
    assert not offenders, (
        "ignore_imports entries whose importer is not under src.app.core (they can "
        f"never match this contract): {offenders}"
    )


def test_ignore_entries_are_unique() -> None:
    """A duplicate entry is dead config and a future stale-entry warning."""
    entries = _ignore_entries()
    duplicates = sorted({entry for entry in entries if entries.count(entry) > 1})
    assert not duplicates, f"duplicate ignore_imports entries: {duplicates}"


@pytest.mark.parametrize("package", FORBIDDEN_PACKAGES)
def test_ignore_list_does_not_neuter_a_whole_forbidden_package(package: str) -> None:
    """Nobody may 'fix' a violation by ignoring the entire forbidden package.

    Ignoring ``src.app.core -> src.app.modules`` would suppress every violation
    in one line while looking like a normal entry. Individual edges (including
    the documented aggregator seams) are allowed; the bare package is not.
    """
    blanket = [entry for entry in _ignore_entries() if entry == (SOURCE_PACKAGE, package)]
    assert not blanket, f"ignore_imports ignores the whole {package} package: {blanket}"
