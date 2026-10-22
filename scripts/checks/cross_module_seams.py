"""Enforce the cross-module seam rule, and report the event-bus surface.

Two contracts, one walk. Both are governance ratchets over the same
``src/app/modules`` tree, so a module that splits a service is measured once
and cannot satisfy one contract while breaking the other.

CONTRACT 1 — the cross-module seam list
---------------------------------------
A module's behaviour layer (``service``/``services``/``router``/``routers``)
may reach another module's behaviour layer only through a seam named in
``SANCTIONED_SEAMS``. Data cells (``models``/``schemas``/``crud``) are
excluded: a read of another module's row type is persistence coupling, not
the behaviour seam this rule governs.

A seam is a *direction*, not a file. Adding a method call through an
existing sanctioned seam is not a violation; adding a new *target* is. The
list is therefore the set of ``(importer, target cell)`` pairs that exist
today, each with the reason it is legitimate.

Why the rule is about seams and not about the event bus: the evidence in
``ARCHITECTURE.md``'s cross-module section. Request-scoped cross-module
calls need their return value, and an event has none, so the bus is the
wrong tool for them. See ``ARCHITECTURE.md`` for the recorded decision and
``docs/testing/cross-module-import-metric.md`` for why the published counts
disagree.

CONTRACT 2 — the event-bus surface ratchet
-----------------------------------------
``src/app/events.py`` registers 14 event names; ``src/app`` publishes 3.
The bus's failure mode is silence in both directions: an event published to
no handler is dropped, and a handler registered for an event nobody
publishes is dead code that reads as live wiring. A *new* dead
registration is a new silent hole, so the count is a ratchet: it may fall
(a real migration publishes one) and may not rise.

This is a ratchet, not a gate, on purpose. Making it a gate would require
deleting 11 handler registrations — a behavioural cutover in the webhook
and auth paths, which is a migration (recorded as the candidate below),
not a lint fix.

Usage:
    uv run python scripts/checks/cross_module_seams.py
    uv run python scripts/checks/cross_module_seams.py --verbose

Exit codes:
    0 - no unsanctioned seam, no new dead bus registration
    1 - a violation was found
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

# Repo root = two levels up (scripts/checks/<gate>.py)
ROOT = Path(__file__).resolve().parents[2]
MODULES_DIR = ROOT / "src" / "app" / "modules"

#: A module's own behaviour layer. ``service.py`` and the ``service``/
#: ``services`` package forms are the same layer, as are the router forms;
#: they are listed separately because the filesystem spells them separately.
BEHAVIOUR_FILES = frozenset({"service.py", "router.py"})
BEHAVIOUR_DIRS = frozenset({"service", "services", "router", "routers"})

#: Cells that are persistence, not behaviour. A module reading another
#: module's row type or DTO is not the coupling this contract governs, and
#: 465 measured imports sit in these two cells — banning them would demand
#: rewrites of legal reads, not of real coupling.
DATA_CELLS = frozenset({"models", "schemas", "crud"})

#: Documented shared scaffolding, not a feature module (see the
#: module-boundary ban, which excludes it for the same reason).
SHARED_MODULE = "_base"

#: Every ``(importer, target cell)`` behaviour seam that exists today, with
#: the reason it is legitimate. Grouped by the kind of coupling so a future
#: migration retires a whole group and the number visibly moves.
#:
#: These are the seams a future refactor would target, and each is a place
#: the event bus is the WRONG tool: the caller needs the return value
#: (an auth payload, a tenant, a rate limit) in the same request. A bus
#: event has no return path, so routing one of these through the bus would
#: not decouple them — it would make them asynchronous and wrong.
SANCTIONED_SEAMS: dict[tuple[str, str], str] = {
    # --- Identity read seam: auth must resolve who the caller is, NOW.
    # `UserDirectory` is the narrow read interface (users/directory.py);
    # `UserService` here is the write/privileged path. Both are
    # request-scoped: the token payload and the session record are needed
    # before the request can proceed, so neither can be an event.
    ("auth/router.py", "src.app.modules.users"): (
        "DI: the router resolves the authenticated user through the facade; the result is the request's identity"
    ),
    ("auth/service.py", "src.app.modules.users"): (
        "identity resolution on the auth door; the caller needs the payload synchronously"
    ),
    ("auth/services/email_verification.py", "src.app.modules.users"): (
        "verification mutates the user row and must observe the write's outcome to answer the request"
    ),
    ("auth/services/passwords.py", "src.app.modules.users"): (
        "a password change is a user write whose result the endpoint returns"
    ),
    ("auth/services/sso.py", "src.app.modules.users"): (
        "SSO login provisions and returns the local user; the session cannot be built without it"
    ),
    # --- Session invalidation vocabulary: a shared ENUM plus one function.
    # `InvalidationReason` is data (an enum, not a service); the function
    # is a targeted cross-module side effect that must complete before the
    # credential change is acknowledged, or a revoked credential stays live.
    ("auth/services/email_verification.py", "src.app.modules.users.session_invalidation"): (
        "shared invalidation enum; the verification must name why it invalidates"
    ),
    ("auth/services/passwords.py", "src.app.modules.users.session_invalidation"): (
        "password change MUST invalidate existing sessions before the response returns"
    ),
    ("rbac/service.py", "src.app.modules.users.session_invalidation"): (
        "a credential/role change must revoke sessions inline; deferring it leaves a live credential"
    ),
    # --- Tenant lookup on the SSO provisioning path.
    ("auth/services/sso.py", "src.app.modules.tenants"): (
        "SSO login resolves the target tenant to provision into; the redirect target is the return value"
    ),
    # --- Read-only tier/entitlement POLICY values, not service calls.
    # A limit or default is a constant consulted in-line; there is nothing
    # to await and no state to change, so neither the bus nor a deep
    # service import is the right shape.
    ("notifications/service.py", "src.app.modules.tiers.entitlement"): (
        "reads DEFAULT_ENTITLEMENT, a policy constant; no call and no state change"
    ),
    ("webhooks/service.py", "src.app.modules.tiers.entitlement"): (
        "resolve_path_limit is a request-scoped policy read bounding this delivery's rate budget"
    ),
    # --- Read-only dashboard aggregation over another module's query seam.
    ("dashboard/service.py", "src.app.modules.audit_logs.trail"): (
        "login_activity_subquery is a no-I/O SQL subquery; dashboard owns no copy so the action names cannot drift"
    ),
    # --- The documented tiers/rate_limits import-cycle guard.
    # `tiers.service` transitively imports `rate_limits`, so these two
    # import it directly and eagerly to keep the cycle out of the
    # tiers-first import order (modules/tiers/__init__.py). They are the
    # same two edges the TID251 per-file-ignores name; the ban governs the
    # CELL, this list governs the DIRECTION.
    ("rate_limits/service.py", "src.app.modules.tiers.service"): (
        "import-cycle guard documented in modules/tiers/__init__.py; the deep form is "
        "sanctioned by TID251's named per-file exemption"
    ),
    ("users/service.py", "src.app.modules.tiers.service"): (
        "import-cycle guard documented in modules/tiers/__init__.py; tier lookup is request-scoped"
    ),
    # --- Router-to-service edges that are DI wiring, not module coupling.
    # A module's OWN router importing a service it does not own is
    # surprising, but both of these are dependency-injection wiring: the
    # router needs a collaborator and the facade is the sanctioned
    # accessor. Naming the router explicitly keeps the edge visible.
    #
    # The security module's passkey edge moved from the router to the
    # service (F1): the router is now a thin adapter and the business
    # logic — including the passkey collaborator — lives in
    # `security/service.py`. The seam is the same edge in a different
    # layer, so the sanctioned entry follows the importer.
    ("security/service.py", "src.app.modules.auth"): (
        "DI wiring: security's service builds a passkey collaborator via auth's own factory"
    ),
    ("users/router.py", "src.app.modules.auth.services.email_verification"): (
        "DI wiring: users' router needs the email verifier auth owns to complete a registration"
    ),
}


@dataclass(frozen=True, order=True)
class Violation:
    """One contract breach."""

    kind: str  # "unsanctioned-seam" | "graveyard-seam" | "dead-subscription"
    subject: str
    detail: str

    def format(self) -> str:
        return f"{self.kind}: {self.subject}: {self.detail}"


def _owner_of(rel: Path) -> str | None:
    """The feature module a path belongs to, or None outside ``modules/``."""
    return rel.parts[0] if len(rel.parts) > 1 else None


def _in_behaviour_layer(rel: Path) -> bool:
    """True when the file sits in its own module's behaviour layer.

    Both spellings count: ``users/service.py`` (parts[1] == ``service.py``)
    and ``auth/services/sso.py`` (parts[1] == ``services``).
    """
    if len(rel.parts) < 2:
        return False
    return rel.parts[1] in BEHAVIOUR_FILES or rel.parts[1] in BEHAVIOUR_DIRS


def _defined_names(module_dir: Path) -> set[str]:
    """Top-level names a module's behaviour layer defines.

    Used to decide whether a package-root import is a *behaviour* seam: the
    lazy facades re-export services and routers alongside models and
    enums, so "imports the package root" alone is not a behaviour edge.
    """
    names: set[str] = set()
    for path in sorted(module_dir.rglob("*.py")):
        rel = path.relative_to(MODULES_DIR)
        if not (rel.name in BEHAVIOUR_FILES or rel.parts[1:-1] and rel.parts[1] in BEHAVIOUR_DIRS):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name.startswith("_"):
                    continue
                names.add(node.name)
    return names


def scan_seams() -> tuple[dict[tuple[str, str], set[str]], set[str]]:
    """Return the live ``(importer, target) -> symbols`` seam map and the
    per-module behaviour-name index."""
    behaviour_names: dict[str, set[str]] = {}
    for mod in sorted(p for p in MODULES_DIR.iterdir() if p.is_dir()):
        if mod.name.startswith("_"):
            continue
        behaviour_names[mod.name] = _defined_names(mod)

    seams: dict[tuple[str, str], set[str]] = OrderedDict()
    for path in sorted(MODULES_DIR.rglob("*.py")):
        rel = path.relative_to(MODULES_DIR)
        if not _in_behaviour_layer(rel):
            continue
        owner = _owner_of(rel)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module):
                continue
            parts = node.module.split(".")
            if len(parts) < 4 or parts[:3] != ["src", "app", "modules"]:
                continue
            target = parts[3]
            if target in (owner, SHARED_MODULE):
                continue
            imported = {alias.name for alias in node.names}
            if len(parts) == 4:
                # Package root: a behaviour seam only if it binds a name the
                # target's behaviour layer defines.
                imported &= behaviour_names.get(target, set())
            elif parts[4] in DATA_CELLS:
                continue
            if not imported:
                continue
            seams.setdefault((rel.as_posix(), node.module), set()).update(imported)
    return seams, dict(behaviour_names)


def scan_bus(app_root: Path | None = None) -> tuple[set[str], set[str]]:
    """Return ``(registered, published)`` event names.

    Registration is read from the handler registry (``src/app/events.py``),
    which is the single source of truth: ``EventBus.register_all_handlers``
    delegates to ``register_application_event_handlers`` and there is no
    second registry. Publication is every ``*.publish("<literal>")`` call in
    ``src/app`` whose receiver is the bus, matched by AST so a call in a
    docstring or a comment cannot be counted.

    ``app_root`` is a parameter so the orphan-publish check can be run
    against a tree with a producer added, rather than being asserted
    against the real one where the assertion would hold vacuously. A check
    that can only ever be run on a clean tree proves nothing.
    """
    app_root = app_root or (ROOT / "src" / "app")
    registry = (app_root / "events.py").read_text(encoding="utf-8")
    registered: set[str] = set()
    tree = ast.parse(registry)
    for node in ast.walk(tree):
        # `handlers: list[tuple[str, EventHandler]] = [("audit.log", handle), ...]`
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "handlers":
            registered |= _string_leads(node.value)
        # `business_events = ("user.created", ...)` — the names the webhook
        # adapter is fanned out over, so registering it covers each of them.
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "business_events" for t in node.targets
        ):
            registered |= _string_leads(node.value)
        # `handlers.extend(...)` / `handlers.append(...)` — any future registry
        # shape that adds to the list must be read too, or it reads as unregistered.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "extend", "add"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "handlers"
        ):
            registered |= _string_leads(node.args[0]) if node.args else set()

    published: set[str] = set()
    for path in sorted(app_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "publish"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and "bus" in ast.unparse(node.func.value).lower()
            ):
                published.add(node.args[0].value)
    return registered, published


def _string_leads(node: ast.expr | None) -> set[str]:
    """Collect event names from the registry's two literal shapes.

    ``business_events`` is a tuple of BARE names, while ``handlers`` is a
    list of ``(name, handler)`` PAIRS. Both are tuples at the AST level, so
    they are told apart by whether the first element is a string, not by
    node type. Two earlier revisions got this wrong in each direction and
    the gate reported 4 registered / 2 dead, then 5 / 2 — in one case
    claiming ``audit.log`` was never registered when it is, in the other
    missing ``audit.log`` and ``webhook.trigger`` entirely and calling them
    phantom violations. A detector that miscounts the thing it ratchets
    measures nothing, so this is shape-driven rather than name-driven.
    """
    found: set[str] = set()
    if isinstance(node, ast.Tuple):
        if not node.elts:
            return found
        first = node.elts[0]
        # A (name, handler) PAIR: a string head followed by a non-string
        # second element. A tuple of BARE NAMES is all-strings, so it is
        # recursed instead. `business_events` is the latter and has 10
        # names; reading it as a pair collapsed it to `user.created` and
        # the gate then called 9 registered events dead.
        if (
            len(node.elts) == 2
            and isinstance(first, ast.Constant)
            and isinstance(first.value, str)
            and not (isinstance(node.elts[1], ast.Constant) and isinstance(node.elts[1].value, str))
        ):
            found.add(first.value)
        else:
            for element in node.elts:
                found |= _string_leads(element)
    elif isinstance(node, ast.List | ast.Set):
        for element in node.elts:
            found |= _string_leads(element)
    elif isinstance(node, ast.GeneratorExp):
        found |= _string_leads(node.elt)
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        found.add(node.value)
    return found


#: Dead registrations today. A ratchet, not a target: publishing one of
#: these (the recorded migration candidate) lowers the count, and a new
#: registration nobody publishes raises it.
DEAD_SUBSCRIPTION_CEILING = 11


def check() -> list[Violation]:
    violations: list[Violation] = []

    seams, _ = scan_seams()
    for (importer, target), symbols in sorted(seams.items()):
        if (importer, target) not in SANCTIONED_SEAMS:
            violations.append(
                Violation(
                    "unsanctioned-seam",
                    f"{importer} -> {target}",
                    f"binds {sorted(symbols)}; add a reason to SANCTIONED_SEAMS or route it through the bus",
                )
            )
    for importer, target in sorted(SANCTIONED_SEAMS):
        if (importer, target) not in seams:
            violations.append(
                Violation(
                    "graveyard-seam",
                    f"{importer} -> {target}",
                    "no longer imports this cell; delete the entry so the list cannot become a graveyard",
                )
            )

    registered, published = scan_bus()
    if not published:
        violations.append(
            Violation(
                "dead-subscription",
                "event bus",
                "publication scan found zero call sites — the detector is broken",
            )
        )
    orphan_publish = sorted(published - registered)
    if orphan_publish:
        violations.append(
            Violation(
                "dead-subscription",
                "event bus",
                f"published but registered nowhere, so these are dropped silently: {orphan_publish}",
            )
        )
    dead = len(registered - published)
    if dead > DEAD_SUBSCRIPTION_CEILING:
        violations.append(
            Violation(
                "dead-subscription",
                "event bus",
                f"{dead} registered events are never published, ceiling is {DEAD_SUBSCRIPTION_CEILING}",
            )
        )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", action="store_true", help="print the measured seam and bus surfaces")
    args = parser.parse_args(argv)

    seams, _ = scan_seams()
    violations = check()

    if args.verbose:
        registered, published = scan_bus()
        print(f"sanctioned behaviour seams: {len(SANCTIONED_SEAMS)} declared, {len(seams)} live")
        for (importer, target), symbols in sorted(seams.items()):
            reason = SANCTIONED_SEAMS.get((importer, target), "UNSANCTIONED")
            print(f"  {importer} -> {target} {sorted(symbols)}")
            print(f"      {reason}")
        print(f"bus: {len(registered)} registered, {len(published)} published, {len(registered - published)} dead")
        for name in sorted(registered - published):
            print(f"  dead registration: {name}")

    for violation in violations:
        print(violation.format(), file=sys.stderr)
    if violations:
        print(f"\n{len(violations)} violation(s)", file=sys.stderr)
        return 1
    if args.verbose:
        print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
