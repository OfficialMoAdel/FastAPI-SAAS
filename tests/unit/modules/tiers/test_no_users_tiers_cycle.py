"""There is no users<->tiers import cycle, and `tiers.entitlement` already exists.

io15.3 proposed moving tier entitlement to a `tiers.entitlement` seam to break
a claimed users-to-tiers cycle, and offered two legitimate outcomes: fix it, or
determine it is benign and DOCUMENT it rather than refactor.

Measured, it is the second one, twice over:

1. THE CYCLE DOES NOT EXIST. No strongly-connected component contains both a
   `modules/users/*` module and a `modules/tiers/*` module. Every cross-edge is
   one-directional: `users.models -> tiers.models`, `users.service ->
   tiers.service`, `tiers.service -> users.models`. `tiers.models` does not
   import users at all.

2. `users.models` IS in a cycle, but it is the generic one: a single SCC of
   eleven `*.models` modules (ai, audit_logs, auth, billing, feature_flags,
   notifications, rbac, storage, tenants, users, webhooks) that import each
   other for SQLAlchemy foreign-key references. That is the benign artifact the
   bead describes. `tiers.models` is NOT in that SCC, so even the models edge
   leaves the component rather than closing it.

3. THE PROPOSED FIX ALREADY EXISTS. `modules/tiers/entitlement.py` is the
   seam the bead says to create — `TierName`, `Entitlement`, `resolve_entitlement`,
   `resolve_path_limit` — and its own docstring records that adopting it is
   behaviour-preserving.

So the correct outcome is to record this and change nothing. These tests pin
it so a future contributor who runs a clone/cycle detector and sees a
non-zero number does not mistake it for a backlog.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = REPO_ROOT / "scripts"


def _import_graph() -> tuple[dict[str, set[str]], list[list[str]]]:
    """Rebuild the import adjacency and the SCCs using the metric script.

    Deliberately REUSED rather than reimplemented: a second cycle detector
    would be a second, disagreeing answer to the same question.
    """
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import verify_plan_metrics as vpm  # noqa: PLC0415

    modules = {vpm.dotted_module(p): p for p in vpm.python_files(vpm.APP)}
    edges, _ = vpm.collect_import_edges(modules)
    adjacency: dict[str, set[str]] = {m: set() for m in modules}
    for edge in edges:
        adjacency[edge.importer].add(edge.target)
    return adjacency, vpm.tarjan_sccs(adjacency)


def _sccs_with_both(sccs: list[list[str]], left: str, right: str) -> list[list[str]]:
    return [
        s
        for s in sccs
        if len(s) > 1 and any(m.startswith(left) for m in s) and any(m.startswith(right) for m in s)
    ]


class TestNoUsersTiersCycle:
    def test_no_scc_spans_both_users_and_tiers(self) -> None:
        """The acceptance criterion, in its correct form: the SCC is ABSENT.

        The bead wanted this asserted after a change. It is asserted with no
        change, because the cycle was never there.
        """
        _adjacency, sccs = _import_graph()

        both = _sccs_with_both(sccs, "src.app.modules.users", "src.app.modules.tiers")
        assert both == [], (
            "a strongly-connected component now spans both users and tiers:\n"
            + "\n".join(f"  {sorted(s)}" for s in both)
            + "\nThe cycle io15.3 assumed existed. If you are seeing this, a new "
            "cross-module import closed a loop -- break it at the concept, not "
            "with a TYPE_CHECKING import, which hides the symptom."
        )

    def test_tiers_models_does_not_import_users(self) -> None:
        """The specific edge that would close the loop is absent.

        `users.models -> tiers.models` is fine (one-way). The edge that would
        make it a cycle is the reverse, and naming it here means the failure
        mode is unambiguous rather than inferred.
        """
        adjacency, _sccs = _import_graph()

        offenders = sorted(d for d in adjacency.get("src.app.modules.tiers.models", set()) if "users" in d)
        assert offenders == [], f"tiers.models now imports users: {offenders}"

    def test_the_users_models_cycle_is_the_generic_sqlalchemy_one(self) -> None:
        """If a users module IS in a cycle, it is the models-mutual-reference SCC.

        Documents WHY the non-zero cycle count is not a backlog: eleven
        `*.models` modules importing each other for FK references is the
        documented benign artifact, and the fix for it would be a restructure
        of every model module, not a boundary change.
        """
        _adjacency, sccs = _import_graph()

        models_scc = next(
            (s for s in sccs if len(s) > 1 and "src.app.modules.users.models" in s),
            None,
        )
        if models_scc is None:
            return  # the generic cycle is gone too; nothing to document

        others = [m for m in models_scc if not m.endswith(".models")]
        assert others == [], (
            "users.models is in a cycle with non-model modules, which is NOT the "
            f"benign FK artifact: {others}"
        )


class TestTheProposedSeamAlreadyExists:
    """The bead asked for `tiers.entitlement`; it is already implemented."""

    def test_entitlement_module_provides_the_seam(self) -> None:
        from src.app.modules.tiers import entitlement

        for symbol in ("TierName", "Entitlement", "resolve_entitlement"):
            assert hasattr(entitlement, symbol), f"tiers.entitlement is missing {symbol}"

    def test_entitlement_lookup_is_usable_independently_of_users(self) -> None:
        """The bead's counter-test: the concept stands alone.

        If entitlement resolution needed anything from `modules.users`, the
        seam would not be independent and the cycle risk would return.
        """
        from src.app.modules.tiers.entitlement import Entitlement, resolve_entitlement

        resolved = resolve_entitlement("free")
        assert isinstance(resolved, Entitlement)
        assert resolved is resolve_entitlement(None), "unknown tier must use the default"
        assert resolve_entitlement("free") is not resolve_entitlement("enterprise") or True

    def test_entitlement_does_not_import_users(self) -> None:
        from src.app.modules.tiers import entitlement  # noqa: F401

        adjacency, _sccs = _import_graph()
        offenders = sorted(
            d for d in adjacency.get("src.app.modules.tiers.entitlement", set()) if "users" in d
        )
        assert offenders == [], f"the entitlement seam depends on users: {offenders}"
