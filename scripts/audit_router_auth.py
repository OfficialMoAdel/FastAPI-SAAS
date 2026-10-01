"""AST audit of FastAPI route authentication across the twelve never-read routers.

Why a script and not a grep
---------------------------
Authentication in this codebase is expressed in FOUR idioms, and each can sit
in EITHER of two positions (a handler parameter, or the decorator's
``dependencies=[...]`` list):

    1. ``ctx: RequestContextDep``                        (param position)
    2. ``Depends(get_current_user)``                     (param or dependencies)
    3. ``Depends(get_current_superuser)``               (param or dependencies)
    4. ``Depends(requires_permission(Permission.X))``    (param or dependencies)

A text scan that only looks for idiom 1/3/4 at the decorator level reports
"zero auth references" for a payment surface (``billing/routers/checkouts.py``,
``billing/routers/invoices.py``) that is in fact authenticated by idiom 2.
A detector that misses one idiom therefore manufactures findings instead of
finding them. This script resolves the *expression tree* of every route, in
both positions, so each idiom is matched structurally rather than textually.

What it flags
-------------
``UNAUTHENTICATED``
    No idiom in either position. The route runs for an anonymous caller
    (unless a mount adds dependencies -- reported as ``router_dependencies``).

``PARTIAL``
    Authenticated, but the only gate is a *plain* one (``get_current_user`` /
    ``RequestContextDep``) with no permission slug (``requires_permission``) and
    no superuser gate, on a MUTATING method. This is the privilege-escalation
    shape: any authenticated member of the tenant can invoke a route that was
    meant for one role. It never shows up as "unauthenticated".

``OK``
    A permission slug, a superuser gate, or a non-mutating route.

A detector hit is a HYPOTHESIS. Every flagged route was then read by hand; see
``docs/security/router-auth-audit.md`` for the confirmed verdicts and the
explained false positives.

Usage
-----
    python scripts/audit_router_auth.py                  # human-readable
    python scripts/audit_router_auth.py --json           # machine-readable
    python scripts/audit_router_auth.py --selftest       # falsifiability proof
    python scripts/audit_router_auth.py --all-modules    # widen the scope
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = ROOT / "src" / "app" / "modules"

# The twelve routers never read before this audit. billing/ and storage/ ship
# sub-routers, so the module name maps to a list of paths.
TARGET_MODULES: dict[str, list[str]] = {
    "ai": ["ai/router.py"],
    "posts": ["posts/router.py"],
    "security": ["security/router.py"],
    "storage": ["storage/routers/files.py", "storage/routers/settings.py"],
    "notifications": ["notifications/router.py"],
    "webhooks": ["webhooks/router.py"],
    "feature_flags": ["feature_flags/router.py"],
    "billing": [
        "billing/router.py",
        "billing/routers/checkouts.py",
        "billing/routers/invoices.py",
        "billing/routers/prices.py",
        "billing/routers/products.py",
    ],
    "tiers": ["tiers/router.py"],
    "rate_limits": ["rate_limits/router.py"],
    "user_preferences": ["user_preferences/router.py"],
    "tasks": ["tasks/router.py"],
}

MUTATING_METHODS = frozenset({"post", "put", "patch", "delete"})

# Symbol names that count as the four idioms. Matched on the *last* dotted
# component so ``src.app.api.deps.auth.get_current_user`` and a local import
# alias-free ``get_current_user`` both hit.
REQUEST_CONTEXT_NAMES = frozenset({"RequestContextDep", "get_request_context"})
CURRENT_USER_NAMES = frozenset({"get_current_user", "get_auth_context"})
SUPERUSER_NAMES = frozenset({"get_current_superuser"})
PERMISSION_FACTORY_NAMES = frozenset({"requires_permission"})

_ALL_IDIOM_NAMES = (
    REQUEST_CONTEXT_NAMES | CURRENT_USER_NAMES | SUPERUSER_NAMES | PERMISSION_FACTORY_NAMES
)

# Fifth shape, discovered by reading the flagged handlers: a WRAPPER alias.
# ``RateLimitedUser = Annotated[AuthenticatedUser, Depends(check_rate_limit_dependency)]``
# and ``check_rate_limit_dependency`` itself takes ``Depends(get_current_user)``.
# The route text then mentions no idiom at all, yet the route is authenticated.
# A detector that stops at the literal name reports a false "unauthenticated"
# on two POST chat endpoints. Resolved by following the alias to its defining
# module and inspecting that dependency's own parameters (see
# ``DependencyResolver``).

# Body-level signals. These are evidence for the manual read (questions c and
# d), not verdicts on their own.
BODY_SIGNALS: dict[str, tuple[str, ...]] = {
    "ownership": ("require_ownership", "owns_resource", "require_resource_ownership", "_require_post_owner"),
    "tenant_scope": ("ctx.tenant_id", "ctx.tenant.id", "ctx.require_tenant", "tenant_id="),
    "superuser_branch": ("ctx.is_superuser", "current_user.is_superuser", "user.is_superuser"),
    "step_up": ("StepUpRequest", "step_up"),
}


def _name(node: ast.AST) -> str | None:
    """Return the trailing dotted name of an expression, or None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Subscript):
        # Annotated[X, Depends(...)] -> inspect the metadata tuple.
        return _annotated_metadata_name(node)
    return None


def _annotated_metadata_name(node: ast.Subscript) -> str | None:
    """For ``Annotated[T, Dep]``, return Dep's callee name (recursively)."""
    slice_node = node.slice
    if isinstance(slice_node, ast.Tuple):
        for element in slice_node.elts[1:]:
            found = _dep_call_name(element)
            if found:
                return found
    return _dep_call_name(slice_node)


def _annotated_metadata(node: ast.AST | None) -> str | None:
    """Callee name inside ``Annotated[T, Dep]``, guarding non-Subscript nodes."""
    if not isinstance(node, ast.Subscript):
        return None
    return _annotated_metadata_name(node)


def _dep_call_name(node: ast.AST) -> str | None:
    """Return the callee of ``Depends(X)`` / ``Depends(X(...))``, else None."""
    if not isinstance(node, ast.Call):
        return None
    callee = _name(node.func)
    if callee != "Depends":
        return None
    if not node.args:
        return None
    return _name(node.args[0])


@dataclass
class AuthSignal:
    """One authentication expression found on a route."""

    idiom: str  # request_context | current_user | superuser | permission
    position: str  # param | dependencies | router
    detail: str  # e.g. "Permission.WEBHOOKS_CREATE" or "RequestContextDep"
    line: int


@dataclass
class Route:
    """One FastAPI route with its resolved authentication surface."""

    module: str
    path: str  # repo-relative file path
    router_var: str
    method: str
    url: str
    handler: str
    line: int
    mutating: bool
    signals: list[AuthSignal] = field(default_factory=list)
    body_signals: list[str] = field(default_factory=list)
    verdict: str = "OK"
    reasons: list[str] = field(default_factory=list)

    @property
    def idioms(self) -> set[str]:
        return {signal.idiom for signal in self.signals}

    @property
    def label(self) -> str:
        return f"{self.path}:{self.line} {self.method.upper()} {self.url} -> {self.handler}"


def _param_signals(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[AuthSignal]:
    """Idiom signals from the handler signature.

    Two shapes must both be caught, because both occur in this codebase:
      * bare annotation   -- ``ctx: RequestContextDep``
      * Annotated form    -- ``u: Annotated[AuthenticatedUser, Depends(get_current_user)]``
      * plain default     -- ``current_user: dict = Depends(get_current_user)``

    A bare annotation is NOT a ``Depends(...)`` node, so it is matched by name
    directly; an Annotated/default node is unwrapped to the ``Depends`` callee.
    """
    signals: list[AuthSignal] = []
    args = fn.args


    def handle(arg: ast.arg, candidates: list[ast.AST | None]) -> None:
        bare_name = _name(arg.annotation) if arg.annotation is not None else None
        for candidate in candidates:
            if candidate is None:
                continue
            dep = _dep_call_name(candidate)
            callee = dep if dep is not None else _name(candidate)
            if callee is None:
                continue
            # ``Annotated[T, Depends(x)]`` carries x in the metadata, not the name.
            if callee not in _ALL_IDIOM_NAMES:
                callee = _annotated_metadata(arg.annotation) or callee
            signal = _classify_name(callee, candidate, arg.lineno, "param")
            if signal is not None:
                signals.append(signal)
        if not any(_dep_call_name(candidate) is not None for candidate in candidates if candidate is not None):
            if bare_name in _ALL_IDIOM_NAMES:
                signal = _classify_name(bare_name, None, arg.lineno, "param")
                if signal is not None:
                    signals.append(signal)


    positional_only = [*getattr(args, "posonlyargs", []), *args.args]
    defaults_offset = len(positional_only) - len(args.defaults)
    for index, arg in enumerate(positional_only):
        default = args.defaults[index - defaults_offset] if index >= defaults_offset else None
        handle(arg, [arg.annotation, default])
    for arg in args.kwonlyargs:
        handle(arg, [arg.annotation, _kw_default(args, arg)])


    return signals



def _kw_default(args: ast.arguments, arg: ast.arg) -> ast.AST | None:
    """Return the kwonly default matching ``arg``, or None."""
    for name, default in zip(args.kwonlyargs, args.kw_defaults):
        if name is arg:
            return default
    return None


def _iter_dep_refs(node: ast.AST) -> list[ast.AST]:
    """Yield every ``Depends(...)``-shaped sub-expression inside ``node``."""
    found: list[ast.AST] = []
    callee = _name(node)
    if callee == "Depends":
        found.append(node)
    for child in ast.walk(node):
        if child is node:
            continue
        if _dep_call_name(child) is not None:
            found.append(child)
    return found


def _classify(dep: ast.AST, has_annotation: bool, line: int) -> AuthSignal | None:
    """Map one ``Depends(...)`` expression onto one of the four idioms."""
    callee = _dep_call_name(dep)
    if callee is None and has_annotation:
        # Bare annotation: ``ctx: RequestContextDep``.
        callee = _name(dep)
    if callee is None:
        return None
    if callee in REQUEST_CONTEXT_NAMES:
        return AuthSignal("request_context", "param", callee, line)
    if callee in SUPERUSER_NAMES:
        return AuthSignal("superuser", "param", callee, line)
    if callee in CURRENT_USER_NAMES:
        return AuthSignal("current_user", "param", callee, line)
    if callee in PERMISSION_FACTORY_NAMES:
        detail = callee
        if isinstance(dep, ast.Call) and dep.args:
            detail = f"{callee}({_render(dep.args[0])})"
        return AuthSignal("permission", "param", detail, line)
    return None


def _classify_name(callee: str | None, node: ast.AST | None, line: int, position: str) -> AuthSignal | None:
    """Map a resolved callee NAME onto one of the four idioms."""
    if callee is None:
        return None
    if callee in REQUEST_CONTEXT_NAMES:
        return AuthSignal("request_context", position, callee, line)
    if callee in SUPERUSER_NAMES:
        return AuthSignal("superuser", position, callee, line)
    if callee in CURRENT_USER_NAMES:
        return AuthSignal("current_user", position, callee, line)
    if callee in PERMISSION_FACTORY_NAMES:
        detail = callee
        if isinstance(node, ast.Call) and node.args:
            detail = f"{callee}({_render(node.args[0])})"
        return AuthSignal("permission", position, detail, line)
    return None


def _alias_targets(tree: ast.Module) -> dict[str, str]:
    """Map a locally-assigned alias name to the symbol it ultimately names.

    ``X = Annotated[T, Depends(y)]`` -> y;  ``X = Y`` -> Y. Following these is
    what turns an opaque alias like ``RateLimitedUser`` back into a real
    dependency callable.
    """
    targets: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        if isinstance(node, ast.AnnAssign):
            names = [node.target.id] if isinstance(node.target, ast.Name) else []
        else:
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        value = node.value
        if value is None:
            continue
        for name in names:
            if isinstance(value, ast.Subscript):
                dep = _annotated_metadata(value)
                if dep:
                    targets[name] = dep
                elif isinstance(value.value, ast.Name):
                    targets[name] = value.value.id
            elif isinstance(value, ast.Name) and value.id != name:
                targets[name] = value.id
    return targets


class DependencyResolver:
    """Resolves authentication idioms across module boundaries.

    The chain that matters: ``RateLimitedUser`` (alias in
    ``api/deps/ai_rate_limit.py``) -> ``check_rate_limit_dependency`` (a
    function whose own parameter takes ``Depends(get_current_user)``). A route
    naming only the alias inherits ``current_user`` authentication two hops
    away. Memoised per ``(module, symbol)`` so the whole audit parses each
    dependency module once.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._trees: dict[Path, ast.Module] = {}
        self._cache: dict[tuple[str, str], set[str]] = {}
        self._in_progress: set[tuple[str, str]] = set()

    def _tree(self, dotted: str) -> tuple[ast.Module, str] | None:
        """Locate a dotted module path under the repo root and parse it once."""
        parts = dotted.split(".")
        candidate = self.root.joinpath(*parts).with_suffix(".py")
        package = self.root.joinpath(*parts) / "__init__.py"
        if candidate.is_file():
            path = candidate
        elif package.is_file():
            path = package
        else:
            return None
        if path not in self._trees:
            self._trees[path] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        return self._trees[path], dotted

    def idioms_in_module(self, symbol: str, dotted_module: str) -> set[str]:
        """All idioms a symbol pulls in, following aliases and imports."""
        key = (dotted_module, symbol)
        if key in self._cache or key in self._in_progress:
            return self._cache.get(key, set())
        self._in_progress.add(key)
        located = self._tree(dotted_module)
        if located is None:
            self._in_progress.discard(key)
            return set()
        tree, dotted = located
        found: set[str] = set()

        # Case 1: the symbol is a function -- inspect its own parameters.
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == symbol:
                for signal in _param_signals(node):
                    found.add(signal.idiom)

        # Case 2: the symbol is an alias in this module -- follow one hop.
        target = _alias_targets(tree).get(symbol)
        if target is not None and target != symbol:
            found |= self.idioms_in_module(target, dotted)

        # Case 3: the symbol was imported from elsewhere -- follow the import.
        found |= self._follow_import(tree, symbol)

        self._in_progress.discard(key)
        self._cache[key] = found
        return found

    def idioms_in_tree(self, tree: ast.Module, dotted_module: str, symbol: str) -> set[str]:
        """Resolve a symbol using an ALREADY-PARSED tree as its home module.

        The router's own module is parsed by ``audit_file``; an alias the router
        merely *imports* (``from ... import RateLimitedUser``) must be resolved
        against the tree the router actually imports from, not against the
        router's own file -- the name is not defined there.
        """
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == symbol:
                for signal in _param_signals(node):
                    found.add(signal.idiom)
        target = _alias_targets(tree).get(symbol)
        if target is not None and target != symbol:
            found |= self.idioms_in_module(target, dotted_module)
        found |= self._follow_import(tree, symbol)
        return found

    def _follow_import(self, tree: ast.Module, symbol: str) -> set[str]:
        """If ``symbol`` is imported from another module, resolve idioms there."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = {alias.asname or alias.name: alias.name for alias in node.names}
                if symbol in names and node.module:
                    return self.idioms_in_module(names[symbol], node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    if local == symbol and "." in alias.name:
                        return self.idioms_in_module(alias.name.rsplit(".", 1)[-1], alias.name.rsplit(".", 1)[0])
        return set()


def _render(node: ast.AST) -> str:
    """Cheap source rendering for a permission slug."""
    if isinstance(node, ast.Attribute):
        return f"{_render(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Call):
        return f"{_render(node.func)}(...)"
    return type(node).__name__


def _decorator_signals(decorator: ast.Call, router_name: str) -> tuple[str, list[AuthSignal], ast.AST | None]:
    """Return (url, decorator dependency signals, dependencies kwarg node)."""
    signals: list[AuthSignal] = []
    url = ""
    if decorator.args and isinstance(decorator.args[0], ast.Constant):
        url = str(decorator.args[0].value)
    dep_kwarg = None
    for keyword in decorator.keywords:
        if keyword.arg != "dependencies" or keyword.value is None:
            continue
        dep_kwarg = keyword.value
        elements = keyword.value.elts if isinstance(keyword.value, ast.List) else [keyword.value]
        for element in elements:
            for dep in _iter_dep_refs(element):
                signal = _classify(dep, False, element.lineno)
                if signal is not None:
                    signal.position = "dependencies"
                    signals.append(signal)
    return url, signals, dep_kwarg


def _router_level_signals(tree: ast.Module, path: str) -> list[AuthSignal]:
    """Idiom signals inherited by every route from ``APIRouter(dependencies=...)``."""
    signals: list[AuthSignal] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _name(node.func) != "APIRouter":
            continue
        for keyword in node.keywords:
            if keyword.arg != "dependencies" or keyword.value is None:
                continue
            elements = keyword.value.elts if isinstance(keyword.value, ast.List) else [keyword.value]
            for element in elements:
                for dep in _iter_dep_refs(element):
                    signal = _classify(dep, False, element.lineno)
                    if signal is not None:
                        signal.position = "router"
                        signals.append(signal)
    return signals


def _body_signals(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Body-level evidence for the manual tenant/axis questions."""
    source = ast.unparse(fn)
    return sorted(name for name, needles in BODY_SIGNALS.items() if any(needle in source for needle in needles))


def classify(route: Route, router_inherited: list[AuthSignal]) -> Route:
    """Attach inherited signals and decide UNAUTHENTICATED / PARTIAL / OK."""
    for signal in router_inherited:
        route.signals.append(AuthSignal(signal.idiom, "router", signal.detail, signal.line))
    idioms = route.idioms
    if not idioms:
        route.verdict = "UNAUTHENTICATED"
        route.reasons.append("no authentication idiom in parameter or dependencies position")
        return route

    plain_only = idioms <= {"request_context", "current_user"}
    if route.mutating and plain_only:
        route.verdict = "PARTIAL"
        gate = "+".join(sorted(idioms))
        route.reasons.append(
            f"mutating route gated only by {gate}; no requires_permission slug and no superuser gate"
        )
        return route

    if "permission" in idioms:
        route.verdict = "OK"
        route.reasons.append(
            "permission slug present: " + ",".join(s.detail for s in route.signals if s.idiom == "permission")
        )
    elif "superuser" in idioms:
        route.verdict = "OK"
        route.reasons.append("superuser gate present")
    else:
        route.verdict = "OK"
        route.reasons.append("non-mutating route with authentication present")
    return route


def _unresolved_param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Annotation/default names on the handler that matched no idiom directly.

    These are the wrapper-alias candidates (``current_user: RateLimitedUser``)
    that only a cross-module hop can resolve.
    """
    args = fn.args
    positional = [*getattr(args, "posonlyargs", []), *args.args, *args.kwonlyargs]
    known: set[str] = set()
    candidates: list[str] = []
    for arg in positional:
        candidates.append(_name(arg.annotation) if arg.annotation is not None else "")
    defaults_offset = len(positional) - len(args.defaults)
    for index, arg in enumerate(positional):
        default = args.defaults[index - defaults_offset] if index >= defaults_offset else None
        candidates.append(_name(default) if default is not None else "")
    for name, default in zip(args.kwonlyargs, args.kw_defaults):
        candidates.append(_name(default) if default is not None else "")
    resolved = {signal.detail.split("(")[0] for signal in _param_signals(fn)}
    for candidate in candidates:
        if candidate and candidate not in _ALL_IDIOM_NAMES and candidate not in resolved:
            known.add(candidate)
    return known


def audit_file(module: str, path: Path, resolver: DependencyResolver | None = None) -> list[Route]:
    """Return every classified route declared in one router file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    inherited = _router_level_signals(tree, str(path))
    routes: list[Route] = []
    # The selftest parses a file in a temp dir, so fall back to the bare name.
    rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
    dotted = rel.removesuffix(".py").replace("/", ".")
    aliases = _alias_targets(tree)

    def _idioms_for(symbol: str) -> set[str]:
        """Idioms behind an unresolved handler name, local alias or import."""
        if symbol in aliases:
            return resolver.idioms_in_tree(tree, dotted, aliases[symbol])
        return resolver.idioms_in_tree(tree, dotted, symbol)


    for node in tree.body:
        functions = []
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions = [node]
        elif isinstance(node, ast.ClassDef):
            functions = [
                child for child in node.body if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
        for fn in functions:
            for decorator in fn.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not isinstance(func, ast.Attribute) or func.attr.lower() not in {
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "head",
                    "options",
                    "api_route",
                }:
                    continue
                if not isinstance(func.value, ast.Name):
                    continue
                method = func.attr.lower()
                url, dep_signals, _ = _decorator_signals(decorator, func.value.id)
                signals = [*_param_signals(fn), *dep_signals]
                # Resolve wrapper aliases the direct pass could not classify
                # (e.g. ``current_user: RateLimitedUser``).
                if resolver is not None:
                    for alias in _unresolved_param_names(fn):
                        idioms = _idioms_for(alias)
                        for idiom in idioms:
                            signals.append(AuthSignal(idiom, "indirect", alias, fn.lineno))
                route = Route(
                    module=module,
                    path=rel,
                    router_var=func.value.id,
                    method=method,
                    url=url,
                    handler=fn.name,
                    line=fn.lineno,
                    mutating=method in MUTATING_METHODS,
                    signals=signals,
                    body_signals=_body_signals(fn),
                )
                routes.append(classify(route, inherited))
    return routes


def collect_routes(
    modules: dict[str, list[str]],
    resolver: DependencyResolver | None = None,
) -> tuple[list[Route], list[str]]:
    """Audit every target file. Returns (routes, missing_files)."""
    routes: list[Route] = []
    missing: list[str] = []
    for module, relatives in modules.items():
        for relative in relatives:
            path = MODULES_DIR / relative
            if not path.is_file():
                missing.append(relative)
                continue
            routes.extend(audit_file(module, path, resolver))
    return routes, missing


# --------------------------------------------------------------------------
# Falsifiability: a detector nobody has watched catch anything proves nothing.
# --------------------------------------------------------------------------
SELFTEST_SOURCE = '''
from fastapi import APIRouter
from typing import Annotated
from src.app.api.deps import RequestContextDep, get_current_user, get_current_superuser
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission

router = APIRouter()


@router.post("/ctx-only")
async def ctx_only(body: dict, ctx: RequestContextDep) -> dict:
    return {"tenant_id": ctx.tenant_id}


@router.post("/user-only")
async def user_only(body: dict, current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
    return {"id": current_user["id"]}


@router.post("/user-bare-default")
async def user_bare(body: dict, current_user: dict = Depends(get_current_user)) -> dict:
    return {"id": current_user["id"]}


@router.post("/dep-superuser", dependencies=[Depends(get_current_superuser)])
async def dep_superuser(body: dict) -> dict:
    return {}


@router.post("/dep-permission", dependencies=[Depends(requires_permission(Permission.POSTS_CREATE))])
async def dep_permission(body: dict) -> dict:
    return {}


@router.get("/read-only-user")
async def read_only(current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
    return {}


@router.delete("/synthetic-unauthenticated")
async def synthetic_unauthenticated(file_id: int, db=Depends(get_db)) -> dict:
    """Deliberately missing every idiom: the detector MUST flag this."""
    return {"file_id": file_id}
'''


# A wrapper alias whose own dependency lives in ANOTHER module -- the exact
# shape of ``ai/router.py``'s ``current_user: RateLimitedUser``. Without this
# indirect hop the detector calls both AI chat endpoints UNAUTHENTICATED.
INDIRECT_SELFTEST_SOURCE = '''
from fastapi import APIRouter
from src.app.api.deps.ai_rate_limit import RateLimitedUser

router = APIRouter()


@router.post("/indirect-chat")
async def indirect_chat(session_id: int, current_user: RateLimitedUser) -> dict:
    return {"id": current_user.id}
'''


def run_selftest() -> int:
    """Parse a synthetic router and assert the classifier fires on the planted bug."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "synthetic_router.py"
        path.write_text(SELFTEST_SOURCE, encoding="utf-8")
        routes = audit_file("synthetic", path)


    expected = {
        # handler: (verdict, idiom that must be detected)
        "ctx_only": ("PARTIAL", "request_context"),
        "user_only": ("PARTIAL", "current_user"),
        "user_bare": ("PARTIAL", "current_user"),
        "dep_superuser": ("OK", "superuser"),
        "dep_permission": ("OK", "permission"),
        "read_only": ("OK", "current_user"),
        "synthetic_unauthenticated": ("UNAUTHENTICATED", None),
    }

    failures: list[str] = []
    resolver = DependencyResolver(ROOT)

    # The wrapper-alias hop, against the REAL dependency module, not a mock.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "indirect_router.py"
        path.write_text(INDIRECT_SELFTEST_SOURCE, encoding="utf-8")
        indirect = audit_file("synthetic", path, resolver)
    indirect_idioms = indirect[0].idioms if indirect else set()
    indirect_ok = bool(indirect) and indirect[0].verdict == "PARTIAL" and "current_user" in indirect_idioms
    print(
        f"  [{'ok' if indirect_ok else 'FAIL':>4}] indirect_chat (RateLimitedUser)   "
        f"{indirect[0].verdict if indirect else 'MISSING':<16} idioms={','.join(sorted(indirect_idioms)) or '-'}"
    )
    if not indirect_ok:
        failures.append(
            "indirect_chat: wrapper alias RateLimitedUser did not resolve to current_user "
            f"(verdict={indirect[0].verdict if indirect else 'MISSING'}, idioms={sorted(indirect_idioms)})"
        )
    print()
    print("selftest: synthetic router with 1 planted unauthenticated route\n")
    for route in sorted(routes, key=lambda r: r.line):
        idioms = ",".join(sorted(route.idioms)) or "-"
        want_verdict, want_idiom = expected[route.handler]
        mark = "ok"
        if route.verdict != want_verdict:
            mark = "FAIL"
            failures.append(f"{route.handler}: verdict {route.verdict} != {want_verdict}")
        if want_idiom and want_idiom not in route.idioms:
            mark = "FAIL"
            failures.append(f"{route.handler}: idiom {want_idiom} not detected (saw {idioms})")
        print(f"  [{mark:>4}] {route.handler:<26} {route.verdict:<16} idioms={idioms}")

    unauth = [route.handler for route in routes if route.verdict == "UNAUTHENTICATED"]
    if unauth != ["synthetic_unauthenticated"]:
        failures.append(f"planted bug not isolated: unauthenticated set = {unauth}")

    print()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        print("selftest FAILED -- the detector does not catch the planted bug")
        return 1
    print("selftest PASSED -- all four idioms detected in both positions; the planted")
    print("unauthenticated DELETE was flagged UNAUTHENTICATED and nothing else was.")
    return 0


def render(routes: list[Route], missing: list[str]) -> str:
    """Human-readable report grouped per module."""
    lines: list[str] = []
    modules = sorted({route.module for route in routes})
    totals = {"UNAUTHENTICATED": 0, "PARTIAL": 0, "OK": 0}
    for route in routes:
        totals[route.verdict] = totals.get(route.verdict, 0) + 1

    lines.append("Router auth audit -- AST, all four idioms, both positions")
    lines.append("=" * 72)
    lines.append(
        f"{'module':<18}{'routes':>7}{'unauth':>8}{'partial':>9}{'ok':>6}"
    )
    for module in modules:
        subset = [route for route in routes if route.module == module]
        counts = {key: sum(1 for route in subset if route.verdict == key) for key in totals}
        lines.append(
            f"{module:<18}{len(subset):>7}{counts['UNAUTHENTICATED']:>8}"
            f"{counts['PARTIAL']:>9}{counts['OK']:>6}"
        )
    lines.append(
        f"{'TOTAL':<18}{len(routes):>7}{totals['UNAUTHENTICATED']:>8}{totals['PARTIAL']:>9}{totals['OK']:>6}"
    )

    for verdict in ("UNAUTHENTICATED", "PARTIAL"):
        flagged = [route for route in routes if route.verdict == verdict]
        lines.append("")
        lines.append(f"--- {verdict} ({len(flagged)}) ---")
        if not flagged:
            lines.append("  (none)")
        for route in flagged:
            lines.append(f"  {route.label}")
            lines.append(f"      idioms: {','.join(sorted(route.idioms)) or 'none'}")
            lines.append(f"      body:   {','.join(route.body_signals) or 'none'}")
            for reason in route.reasons:
                lines.append(f"      why:    {reason}")

    if missing:
        lines.append("")
        lines.append("--- missing files ---")
        for item in missing:
            lines.append(f"  {item}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--selftest", action="store_true", help="prove the detector fires on a planted bug")
    parser.add_argument("--all-modules", action="store_true", help="audit every module router, not the twelve")
    parser.add_argument("--verdict", action="append", default=None, help="only routes with this verdict")
    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest()

    if args.all_modules:
        modules = {
            directory.name: ["router.py"]
            for directory in sorted(MODULES_DIR.iterdir())
            if directory.is_dir() and (directory / "router.py").is_file()
        }
    else:
        modules = TARGET_MODULES

    routes, missing = collect_routes(modules, DependencyResolver(ROOT))
    if args.verdict:
        routes = [route for route in routes if route.verdict in set(args.verdict)]

    if args.json:
        payload = {
            "idioms": [
                "RequestContextDep",
                "Depends(get_current_user)",
                "Depends(get_current_superuser)",
                "Depends(requires_permission(...))",
            ],
            "positions": ["param", "dependencies", "router", "indirect"],
            "modules": sorted(modules),
            "missing_files": missing,
            "summary": {
                verdict: sum(1 for route in routes if route.verdict == verdict)
                for verdict in ("UNAUTHENTICATED", "PARTIAL", "OK")
            },
            "routes": [
                {**asdict(route), "idioms": sorted(route.idioms), "label": route.label}
                for route in sorted(routes, key=lambda r: (r.module, r.path, r.line))
            ],
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0

    print(render(routes, missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
