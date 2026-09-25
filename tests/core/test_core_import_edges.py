"""Enforce the H-5 import contract locally, without import-linter installed.

``src/.importlinter`` is the authoritative gate, but it only runs in CI — the
package is not a local dependency, so a developer editing ``src/app/core`` had
no way to see a new ``core -> modules`` edge before pushing. The gate also
cannot run offline here at all.

This test walks the AST of every ``src/app/core`` module and reports imports
into ``src.app.modules`` / ``.api`` / ``.middleware``. Each live edge must
appear in the contract's ``ignore_imports`` list: an unlisted edge means the
contract would fail in CI, and a stale list entry means the edge was removed
without retiring the exemption. Both are failures, which is the same
``unmatched_ignore_imports_alerting = warn`` intent the config comment
describes, expressed so it can be run in a test.

Retiring entries as edges are removed is the point of the H-5 work; this test
is what makes the progress measurable.
"""

from __future__ import annotations

import ast
import configparser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
CORE_ROOT = SRC_ROOT / "app" / "core"
CONFIG_PATH = SRC_ROOT / ".importlinter"
CONTRACT_SECTION = "importlinter:contract:forbidden-core-to-modules"
FORBIDDEN_PREFIXES = ("src.app.modules", "src.app.api", "src.app.middleware")


def _dotted(path: Path) -> str:
    """src/app/core/tenancy/context.py -> src.app.core.tenancy.context"""
    return ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)


def _forbidden_edges_in(path: Path) -> set[str]:
    """Every core -> forbidden import in one file, as dotted module names.

    ``from src.app.modules.users import cache`` records the edge against
    ``...users.cache``, not ``...users``, because that is the module actually
    loaded — so a ``from pkg import submodule`` is resolved to the submodule.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()

    def resolve(package: str, name: str) -> str:
        candidate = f"{package}.{name}"
        as_path = REPO_ROOT / candidate.replace(".", "/")
        return candidate if as_path.with_suffix(".py").is_file() or as_path.is_dir() else package

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if not node.module.startswith(FORBIDDEN_PREFIXES):
                continue
            for alias in node.names:
                found.add(resolve(node.module, alias.name) if alias.name != "*" else node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(FORBIDDEN_PREFIXES):
                    found.add(alias.name)
    return found


def _live_edges() -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for path in sorted(CORE_ROOT.rglob("*.py")):
        for target in _forbidden_edges_in(path):
            edges.add((_dotted(path), target))
    return edges


def _listed_ignores() -> set[tuple[str, str]]:
    """The exemptions that cover core -> forbidden edges.

    The same ``ignore_imports`` list also carries entries for other contracts
    (``core.events.bus -> app.events``, ``core.setup -> app.models``); those
    are not part of this debt and are filtered out by the forbidden prefix.
    """
    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH, encoding="utf-8")
    listed: set[tuple[str, str]] = set()
    for raw in parser[CONTRACT_SECTION].get("ignore_imports", "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if "->" not in line:
            continue
        source, target = (part.strip() for part in line.split("->", 1))
        if target.startswith(FORBIDDEN_PREFIXES):
            listed.add((source, target))
    return listed


def test_no_core_module_imports_a_feature_module_without_an_exemption():
    """Every live core -> forbidden edge must be named in the contract."""
    live = _live_edges()
    listed = _listed_ignores()
    unlisted = sorted(f"{src} -> {dst}" for src, dst in live - listed)
    assert not unlisted, (
        "core imports a feature module with no exemption in src/.importlinter; "
        "the contract will fail in CI:\n  " + "\n  ".join(unlisted)
    )


def test_every_exemption_still_has_an_edge_behind_it():
    """No stale exemptions: each ignore_imports entry must still exist.

    The five stale entries that once aborted the whole contract silently were
    this failure mode. An entry left behind after its edge was removed means
    the exemption list is no longer a truthful account of the debt.
    """
    live = _live_edges()
    listed = _listed_ignores()
    stale = sorted(f"{src} -> {dst}" for src, dst in listed - live)
    assert not stale, (
        "src/.importlinter exempts edges that no longer exist — remove them:\n  "
        + "\n  ".join(stale)
    )


def test_the_edge_count_is_pinned_to_the_known_debt():
    """A tripwire on the H-5 backlog, so progress and regressions are visible.

    Lower this as edges are retired. The pairs are the *documented* debt: each
    is a core module reaching a feature module at runtime.
    """
    live = _live_edges()
    known = {
        ("src.app.core.cache.l1", "src.app.modules.users.cache"),
        ("src.app.core.cache.warmer", "src.app.modules.tenants.models"),
        ("src.app.core.rbac.dependencies", "src.app.modules.rbac.models"),
        ("src.app.core.security.rate_policy", "src.app.modules.rate_limits.models"),
        ("src.app.core.tenancy.resolver", "src.app.modules.tenants.models"),
        ("src.app.core.worker.functions", "src.app.modules.webhooks.delivery"),
        ("src.app.core.worker.functions", "src.app.modules.audit_logs.service"),
        ("src.app.core.worker.functions", "src.app.modules.billing.webhook.idempotency"),
        ("src.app.core.worker.rls_context", "src.app.modules.audit_logs"),
        ("src.app.core.worker.settings", "src.app.modules.auth.tasks"),
        ("src.app.core.worker.settings", "src.app.modules.notifications.service"),
        ("src.app.core.worker.settings", "src.app.modules.webhooks.delivery"),
    }
    assert live == known, (
        "the set of core -> feature edges changed; update this tripwire.\n"
        f"  new:     {sorted(f'{a} -> {b}' for a, b in live - known)}\n"
        f"  removed: {sorted(f'{a} -> {b}' for a, b in known - live)}"
    )
