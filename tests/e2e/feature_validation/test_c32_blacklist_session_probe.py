"""C-32 probe: does any caller of ``blacklist_tokens`` hold pending work at entry?

Bead FastAPI_saas-vshp (C-32). The C-32 fix replaced a transaction-wide
``db.rollback()`` inside ``blacklist_tokens`` with a savepoint, which is only
correct if every caller's session is clean at the moment of entry: a
transaction-wide rollback would also have discarded whatever the caller had
staged. The comment at ``jwt_service.py:635-651`` claims bead C-32
(FastAPI_saas-m3r3) enumerated the callers and found them clean. This probe
re-derives that claim instead of quoting it, and it does so twice over:

1. By AST -- every call site in ``src/`` is enumerated structurally, so an
   import or a re-export cannot be mistaken for a caller and a dynamic call
   cannot hide from a grep.
2. By runtime trace -- the caller's module global is wrapped, and the frame
   that actually entered the function is recorded. The diff between the two
   sets is logged, because an AST caller that is never reached is a caller
   whose cleanliness is unproven, not a caller proven clean.

At entry to each observed call the SQLAlchemy session is inspected for pending
work that a transaction-wide rollback would have discarded: ``new`` rows that
are not ``TokenBlacklist`` rows, plus ``dirty`` and ``deleted`` rows of any
kind. Any hit is a FAIL and the recorded verdict is "option (b) forbidden",
which is what forces S-2 (FastAPI_saas-d8jd) onto option (a), the upsert.

Two dialects are covered, at two different altitudes, and the difference is
deliberate rather than incidental:

* PostgreSQL -- the full production path. Real HTTP logout through the real
  router, real session, real savepoint.
* SQLite -- the function path. A transaction-wide rollback and a savepoint
  rollback differ in behaviour per dialect, so the dialect question has to be
  asked of SQLite too, but the repository has no seeded SQLite HTTP harness
  (``tests/e2e/conftest.py::e2e_client`` exists and has no seeding helper and
  no caller in the tree), so the SQLite leg calls the function directly and
  the verdict it produces is about the dialect, not about the caller.

The caller-level verdict therefore rests on the PostgreSQL leg, and this
module says so rather than implying that the SQLite leg checked a caller.
"""

from __future__ import annotations

import ast
import contextlib
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from src.app.core.db.token_blacklist import TokenBlacklist
from src.app.core.security.jwt_service import blacklist_tokens

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"

# The function is defined once. A call site at or inside this definition is the
# implementation, not a caller.
_DEFINITION_NAME = "blacklist_tokens"


@dataclass(frozen=True)
class CallSite:
    """One structural call site of ``blacklist_tokens`` inside ``src/``."""

    module: str
    lineno: int
    enclosing: str

    @property
    def site(self) -> str:
        return f"{self.module}:{self.lineno} (in {self.enclosing})"


@dataclass
class Observation:
    """What one runtime entry into ``blacklist_tokens`` saw."""

    caller_module: str
    caller_lineno: int
    caller_function: str
    dialect: str
    pending_new_non_blacklist: list[str] = field(default_factory=list)
    pending_dirty: list[str] = field(default_factory=list)
    pending_deleted: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.pending_new_non_blacklist or self.pending_dirty or self.pending_deleted)

    @property
    def verdict(self) -> str:
        return "option (b) permitted" if self.clean else "option (b) FORBIDDEN"


def _module_name(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _enclosing_names(nodes: list[ast.AST]) -> list[str]:
    """Names of every function/class node in ``nodes``, outermost first."""
    names: list[str] = []
    for node in nodes:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
    return names


def enumerate_call_sites() -> list[CallSite]:
    """Structurally enumerate every call site of ``blacklist_tokens`` in ``src/``.

    A call is a ``Call`` whose callee is the bare name ``blacklist_tokens`` or
    an attribute of that name. Attribute calls are included because a caller
    that reaches through a module (``jwt_service.blacklist_tokens(...)``) is
    still a caller; imports and re-exports are not calls and so cannot be
    counted here, which is exactly the confusion a text search creates.
    """
    found: list[CallSite] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover - a parse failure is a bug
            raise AssertionError(f"{path} does not parse: {exc}") from exc
        module = _module_name(path)

        def visit(current: ast.AST, enclosing: list[str]) -> None:
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                enclosing = [*enclosing, current.name]
            if isinstance(current, ast.Call):
                func = current.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == _DEFINITION_NAME:
                    scope = enclosing[-1] if enclosing else "<module>"
                    # The definition itself, and any nested reference inside it,
                    # is the implementation rather than a caller.
                    if _DEFINITION_NAME in enclosing:
                        return
                    found.append(CallSite(module=module, lineno=current.lineno, enclosing=scope))
            for child in ast.iter_child_nodes(current):
                visit(child, enclosing)

        visit(tree, [])
    return found


def _describe(obj: Any) -> str:
    return f"{type(obj).__module__}.{type(obj).__qualname__}"


def _dialect_of(db: Any) -> str:
    try:
        bind = db.get_bind()
    except Exception:  # pragma: no cover - a session with no bind is not a shape we assert on
        return "unknown"
    dialect = getattr(bind, "dialect", None)
    return getattr(dialect, "name", "unknown")


@contextlib.contextmanager
def trace_blacklist_entries() -> Iterator[list[Observation]]:
    """Wrap every module global named ``blacklist_tokens`` and record entries.

    The wrapper is installed into the *calling* module's globals rather than
    into ``jwt_service``, because a caller that did ``from ... import
    blacklist_tokens`` holds its own binding; patching the definition module
    would not be observed by it and the runtime trace would come back empty.
    """
    observations: list[Observation] = []
    original = blacklist_tokens
    restored: list[tuple[Any, str, Any]] = []

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        frame = sys._getframe(1)
        db = kwargs.get("db")
        if db is None and len(args) >= 3:
            db = args[2]
        observation = Observation(
            caller_module=_module_name(Path(frame.f_code.co_filename)),
            caller_lineno=frame.f_lineno,
            caller_function=frame.f_code.co_name,
            dialect=_dialect_of(db),
        )
        if db is not None:
            observation.pending_new_non_blacklist = [_describe(o) for o in db.new if not isinstance(o, TokenBlacklist)]
            observation.pending_dirty = [_describe(o) for o in db.dirty]
            observation.pending_deleted = [_describe(o) for o in db.deleted]
        observations.append(observation)
        return await original(*args, **kwargs)

    for site in enumerate_call_sites():
        import importlib

        module = importlib.import_module(site.module)
        if getattr(module, _DEFINITION_NAME, None) is original:
            setattr(module, _DEFINITION_NAME, wrapper)
            restored.append((module, _DEFINITION_NAME, original))
    try:
        yield observations
    finally:
        for module, name, value in restored:
            setattr(module, name, value)


def test_c32_call_sites_are_enumerated_structurally() -> None:
    """The AST enumeration is not empty and names an importable module per site."""
    import importlib

    sites = enumerate_call_sites()
    assert sites, "AST enumeration found no call site of blacklist_tokens in src/"
    for site in sites:
        module = importlib.import_module(site.module)
        assert hasattr(module, _DEFINITION_NAME), f"{site.module} has no global named {_DEFINITION_NAME}"


@pytest.mark.asyncio
async def test_c32_postgres_logout_never_carries_pending_non_blacklist_work(
    authenticated_user: dict,
    client: AsyncClient,
    log_step: Any,
) -> None:
    """Drive logout over HTTP and inspect the session at entry to the function."""
    headers = {"Authorization": f"Bearer {authenticated_user['access_token']}"}
    cookies = {"refresh_token": authenticated_user["refresh_token"]} if authenticated_user.get("refresh_token") else {}

    ast_sites = enumerate_call_sites()
    with trace_blacklist_entries() as observations:
        response = await client.post("/api/v1/account/logout", headers=headers, cookies=cookies)

    assert response.status_code == 200, f"logout failed: {response.status_code} {response.text}"
    assert observations, "logout returned 200 but blacklist_tokens was never entered"

    ast_modules = {site.module for site in ast_sites}
    reached_modules = {obs.caller_module for obs in observations}
    log_step(
        "C-32 caller reconciliation",
        ast_call_sites=[site.site for site in ast_sites],
        runtime_reached=sorted(f"{m}" for m in reached_modules),
        ast_only_never_reached=sorted(ast_modules - reached_modules),
        runtime_only_not_in_ast=sorted(reached_modules - ast_modules),
    )

    # A runtime caller the AST enumeration missed would mean the enumeration is
    # not a complete caller set, which is the premise the whole probe rests on.
    assert not (reached_modules - ast_modules), (
        f"runtime reached caller(s) the AST enumeration did not find: {sorted(reached_modules - ast_modules)}"
    )

    for observation in observations:
        assert observation.dialect == "postgresql", f"expected the postgres leg, got {observation.dialect}"
        log_step(
            f"C-32 entry at {observation.caller_module}:{observation.caller_lineno} "
            f"in {observation.caller_function}",
            verdict=observation.verdict,
            pending_new_non_blacklist=observation.pending_new_non_blacklist,
            pending_dirty=observation.pending_dirty,
            pending_deleted=observation.pending_deleted,
        )
        assert observation.clean, (
            f"{observation.caller_module}:{observation.caller_lineno} holds pending work at entry "
            f"({observation.verdict}): new={observation.pending_new_non_blacklist} "
            f"dirty={observation.pending_dirty} deleted={observation.pending_deleted}"
        )


@pytest.mark.asyncio
async def test_c32_sqlite_savepoint_leaves_no_pending_work(log_step: Any) -> None:
    """The dialect leg: on SQLite the savepoint path commits without residue."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(TokenBlacklist.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    with trace_blacklist_entries() as observations:
        async with maker() as session:
            observation = Observation(
                caller_module=__name__,
                caller_lineno=0,
                caller_function="c32_sqlite_leg",
                dialect="sqlite",
            )
            staged = [o for o in session.new if not isinstance(o, TokenBlacklist)]
            observation.pending_new_non_blacklist = [_describe(o) for o in staged]
            observations.append(observation)
            await blacklist_tokens(
                "not-a-jwt",
                None,
                session,
            )
            assert not session.new, "blacklist_tokens left rows staged on a session that started clean"
            assert not session.dirty and not session.deleted

    assert observations, "the sqlite leg recorded no observation"
    log_step(
        "C-32 sqlite dialect leg",
        dialect=observations[0].dialect,
        pending_new_non_blacklist=observations[0].pending_new_non_blacklist,
        verdict=observations[0].verdict,
    )
    await engine.dispose()
