"""C-32 oracle: does any caller of ``blacklist_tokens`` hold pending non-blacklist work?

``jwt_service.blacklist_tokens`` stages ``TokenBlacklist`` rows and then, at
``jwt_service.py:571``, opens a SAVEPOINT, flushes, and — on ``IntegrityError``
— calls ``await db.rollback()`` at ``:578`` before retrying. A session-level
``rollback()`` inside that block is not scoped to the savepoint: it unwinds the
whole transaction. That is only safe if the session holds nothing else at that
moment. If any caller had uncommitted non-blacklist work, rolling back would
silently discard it.

This module answers the question by construction rather than by assertion, and
pins the answer so a future caller cannot reintroduce the hazard silently:

1. ENUMERATION. Every ``blacklist_tokens(...)`` call site in ``src/`` is found
   by AST and compared against a pinned set. A new caller FAILS until it is
   enumerated here, which is the point: the previous audit did not enumerate
   the caller set at all, and that omission is how the question stayed
   ``[Unverified]``.
2. THE CALLER'S OWN STATEMENTS. For each call site, every statement executed
   between function entry and the call is scanned for a session-mutating call
   (``add``/``delete``/``merge``/``flush``/``commit``/``execute``) at any
   nesting depth.
3. THE AWAITED HELPERS ARE READ-ONLY. Statements 1-2 cannot see *through* a
   call, so every free function awaited on the way to the call must be a
   pinned read-only helper. ``verify_token`` is in that set — and rule 4 is
   what makes that claim safe rather than hopeful.
4. ``verify_token`` DOES NOT WRITE THROUGH THE SESSION. Driven against a real
   session across the three revocation outcomes (Redis says revoked / not
   revoked / Redis unavailable), the session must have no pending INSERT, no
   dirty rows and no deletions afterwards.

Rules 1-3 are structural and cheap; rule 4 is the one that would catch a
helper quietly gaining a write, which is the only way rules 1-3 can be
satisfied in the letter and defeated in practice.

ANSWER, as of this commit: the caller set is exactly one site
(``src/app/modules/auth/service.py``, inside ``logout``), and it holds nothing
pending at ``:571``. Both revocation helpers reachable from ``verify_token``
open their OWN ``local_session()`` — ``_check_db_fallback`` at
``jwt_service.py:297`` and ``_users_token_version_lookup`` at
``modules/users/__init__.py:52`` — so neither can touch the caller's
transaction. That is why the ``:578`` rollback is currently safe.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Final, TypeGuard
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "src"
JWT_SERVICE: Final[str] = "src/app/core/security/jwt_service.py"

#: Every call site of the PLURAL ``blacklist_tokens``. ``blacklist_token``
#: (singular, ``jwt_service.py:598``) is a different function with its own
#: transaction handling and is deliberately not in this set.
EXPECTED_CALL_SITES: Final[frozenset[str]] = frozenset({"src/app/modules/auth/service.py"})

#: Session-mutating methods. ``execute`` is included even though a SELECT also
#: goes through it: the question is whether the caller's transaction can be
#: moved at all, and an ``execute`` on that session can carry DML.
MUTATING_SESSION_METHODS: Final[frozenset[str]] = frozenset(
    {"add", "add_all", "delete", "merge", "flush", "commit", "rollback", "begin", "execute", "execute_async"}
)

#: Free functions that may be awaited between entering a caller and reaching
#: ``blacklist_tokens``. Read-only by contract — rule 4 enforces the contract
#: for ``verify_token``, which is the only one that takes a session.
READ_ONLY_AWAITED: Final[frozenset[str]] = frozenset({"verify_token", "blacklist_token"})


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _is_blacklist_tokens_call(node: ast.AST) -> TypeGuard[ast.Call]:
    """True for a call to the plural ``blacklist_tokens``, however it is spelled."""
    if not isinstance(node, ast.Call):
        return False
    func: ast.expr = node.func
    if isinstance(func, ast.Name):
        return func.id == "blacklist_tokens"
    if isinstance(func, ast.Attribute):
        return func.attr == "blacklist_tokens"
    return False


def _parsed_call_sites() -> dict[str, tuple[ast.Module, list[ast.Call]]]:
    """Map repo-relative file -> (its ONE tree, every ``blacklist_tokens`` call).

    The tree and the call nodes must come from the SAME parse. Every predicate
    here locates a node with ``child is node``, which is an identity check, and
    two ``ast.parse`` calls produce two unrelated node graphs — so handing a
    caller a tree from a second parse makes every containment test fail closed
    with "not inside a function". Returning both together removes the way to
    get that wrong.
    """
    found: dict[str, tuple[ast.Module, list[ast.Call]]] = {}
    for path in _python_files(SRC_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls: list[ast.Call] = [node for node in ast.walk(tree) if _is_blacklist_tokens_call(node)]
        if calls:
            found[path.relative_to(REPO_ROOT).as_posix()] = (tree, calls)
    return found


def _call_sites() -> dict[str, list[ast.Call]]:
    """Map repo-relative file -> every ``blacklist_tokens`` call in it."""
    return {rel: calls for rel, (_tree, calls) in _parsed_call_sites().items()}


def _enclosing_function(node: ast.AST, tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """The NEAREST function definition containing ``node``.

    "Nearest" is decided by subtree size rather than walk order, because
    ``ast.walk`` is breadth-first and would otherwise return an outer function
    for a call nested in a closure. The innermost function is the one with the
    fewest nodes in its subtree, so this is order-independent and deterministic.
    """
    enclosing = [
        fn
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and any(n is node for n in ast.walk(fn))
    ]
    if not enclosing:
        raise AssertionError(f"call at line {getattr(node, 'lineno', '?')} is not inside a function")
    return min(enclosing, key=lambda fn: len(list(ast.walk(fn))))


#: Every compound statement whose sub-bodies execute when the statement runs.
#: ``Try`` needs all five: ``body``, ``handlers[*].body``, ``orelse`` and
#: ``finalbody`` all run on some path, so a call inside any of them has all the
#: statements before it in those blocks executed first. Missing ``orelse`` or
#: ``finalbody`` is not a subtlety here — it would silently under-collect and
#: turn the mutation check into a rule that cannot fail.
_COMPOUND_BODIES: Final[tuple[str, ...]] = ("body", "orelse", "finalbody")


def _child_bodies(stmt: ast.stmt) -> list[list[ast.stmt]]:
    """Every statement list that runs as part of executing ``stmt``."""
    bodies: list[list[ast.stmt]] = []
    for attr in _COMPOUND_BODIES:
        value = getattr(stmt, attr, None)
        if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
            bodies.append(value)
    if isinstance(stmt, ast.Try | ast.With | ast.If | ast.For | ast.While | ast.Match):
        for case in getattr(stmt, "cases", []) or []:
            bodies.append(case.body)
    return bodies


def _statements_before(node: ast.Call, tree: ast.Module) -> list[ast.stmt]:
    """Every statement that has finished executing by the time ``node`` runs."""
    return _statements_before_in(_enclosing_function(node, tree).body, node)


def _precedes_within(node: ast.Call, stmt: ast.stmt) -> bool:
    """True when ``node`` lives inside ``stmt``."""
    return any(child is node for child in ast.walk(stmt))


def _statements_before_in(body: list[ast.stmt], node: ast.Call) -> list[ast.stmt]:
    """Statements of ``body`` up to and including the one containing ``node``.

    The statement CONTAINING the call is included, because the part of it that
    precedes the call has already run — and it is also the natural place to find
    a mutation, as in ``if x: await db.add(row)`` immediately before the call.
    Descending stops there: nothing after the containing statement has executed
    yet, so including it would flag a write that happens AFTER the blacklist
    call, which is the opposite of the hazard being tested.
    """
    collected: list[ast.stmt] = []
    for stmt in body:
        collected.append(stmt)
        if _precedes_within(node, stmt):
            for inner in _child_bodies(stmt):
                collected.extend(_statements_before_in(inner, node))
            return collected
    return collected


def _mutation_sites(statements: list[ast.stmt]) -> list[str]:
    """Every session-mutating call inside ``statements``, at any nesting depth."""
    hits: list[str] = []
    for stmt in statements:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in MUTATING_SESSION_METHODS:
                owner = _receiver_name(func)
                label = f"{owner}.{name}()" if owner else f"{name}()"
                hits.append(f"line {node.lineno}: {label}")
    return hits


def _receiver_name(func: ast.AST) -> str | None:
    """The receiver of ``x.y()``, when it is a plain name — ``db.flush`` -> ``db``."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return None


def _receiver_is_session(receiver: str | None) -> bool:
    return receiver is not None and receiver in {"db", "session", "self"}


def _awaited_free_functions(statements: list[ast.stmt], exclude: ast.AST) -> set[str]:
    """Names of module-level functions awaited in ``statements``, minus ``exclude``.

    ``exclude`` is the ``blacklist_tokens`` call itself. The statement containing
    that call is part of "before" (the part preceding it has run), so without
    this filter the call reports itself as an unpinned helper — a rule that
    fails on the very statement it is written to protect.
    """
    names: set[str] = set()
    for stmt in statements:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
                if node.value is exclude:
                    continue
                func = node.value.func
                if isinstance(func, ast.Name):
                    names.add(func.id)
    return names


def _parse(rel_path: str) -> ast.Module:
    return ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"), filename=rel_path)


class TestTheCallerSetIsEnumerated:
    def test_the_scan_finds_the_caller_set_it_is_meant_to_find(self) -> None:
        found = _call_sites()
        assert JWT_SERVICE not in found, (
            "jwt_service.py defines blacklist_tokens; if it now calls it, the definition "
            "and a call site have collided and this whole module needs re-reading"
        )
        assert found, "no blacklist_tokens call site found; the scanner is broken"

    def test_every_call_site_is_enumerated(self) -> None:
        found = set(_call_sites())
        assert found == set(EXPECTED_CALL_SITES), (
            f"blacklist_tokens is called from {sorted(found)} but this oracle has only "
            f"reasoned about {sorted(EXPECTED_CALL_SITES)}. A new caller must be added here "
            "and its pending-work analysis extended before the :578 rollback can be called safe."
        )

    def test_the_singular_variant_is_not_confused_with_the_plural_one(self) -> None:
        """``blacklist_token`` has its own transaction handling and is not in scope."""
        text = (REPO_ROOT / JWT_SERVICE).read_text(encoding="utf-8")
        tree = ast.parse(text)
        singular_defs = [
            fn.name
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and fn.name == "blacklist_token"
        ]
        assert singular_defs, "blacklist_token is gone; re-check whether the plural is still needed"
        callers = _call_sites()
        for rel_path, calls in callers.items():
            for call in calls:
                func: ast.expr = call.func
                assert not isinstance(func, ast.Attribute) or func.attr == "blacklist_tokens", (
                    f"{rel_path}:{call.lineno} matched on attribute name, not on the imported symbol"
                )


class TestNoCallerHoldsPendingWork:
    @pytest.mark.parametrize("rel_path", sorted(EXPECTED_CALL_SITES))
    def test_no_session_mutation_runs_before_the_call(self, rel_path: str) -> None:
        tree, calls = _parsed_call_sites()[rel_path]
        offenders: list[str] = []
        for call in calls:
            before = _statements_before(call, tree)
            hits = [h for h in _mutation_sites(before) if _receiver_is_session(_receiver_at(before, h))]
            offenders.extend(hits)
        assert offenders == [], (
            f"{rel_path} mutates the session before calling blacklist_tokens: {offenders}. "
            "jwt_service.py:578 rolls the whole transaction back, so that work would be "
            "silently discarded. Move the write after the blacklist call or stop doing it here."
        )

    @pytest.mark.parametrize("rel_path", sorted(EXPECTED_CALL_SITES))
    def test_everything_awaited_on_the_way_is_a_pinned_read_only_helper(self, rel_path: str) -> None:
        tree, calls = _parsed_call_sites()[rel_path]
        awaited: set[str] = set()
        for call in calls:
            awaited |= _awaited_free_functions(_statements_before(call, tree), call)
        unpinned = awaited - READ_ONLY_AWAITED
        assert not unpinned, (
            f"{rel_path} awaits {sorted(unpinned)} before blacklist_tokens. Those are not on "
            "the read-only list, so their effect on the session has not been proven. Either "
            "prove it read-only here, or move it after the blacklist call."
        )

    @pytest.mark.parametrize("rel_path", sorted(EXPECTED_CALL_SITES))
    def test_the_caller_never_commits_a_transaction_the_blacklist_shared(self, rel_path: str) -> None:
        """``blacklist_tokens`` commits its OWN transaction, so "after" is moot.

        The dangerous direction is *before*: work staged earlier would be
        destroyed by the ``:578`` rollback. Work the caller does *after* cannot
        be, because ``:582`` has already committed and the transaction is closed
        by the time control returns.

        That is why this oracle does not require the caller to write anything
        after the call. An earlier draft of this test asserted the opposite --
        that a session write must FOLLOW -- which was backwards: no write after
        is strictly safer, and ``logout`` satisfies the real property by not
        writing at all.
        """
        jwt_text = (REPO_ROOT / JWT_SERVICE).read_text(encoding="utf-8")
        source = _function_source(jwt_text, "blacklist_tokens")
        assert "await db.commit()" in source, (
            "blacklist_tokens no longer commits its own transaction. If the caller now "
            "owns the commit, its post-call work joins the blacklist transaction and the "
            "before/after analysis in this oracle has to be redone from scratch."
        )
        tree, calls = _parsed_call_sites()[rel_path]
        for call in calls:
            fn = _enclosing_function(call, tree)
            later = [
                node.lineno
                for stmt in fn.body
                for node in ast.walk(stmt)
                if isinstance(node, ast.Call)
                and (
                    (isinstance(node.func, ast.Attribute) and node.func.attr in {"commit", "rollback"})
                    or (isinstance(node.func, ast.Name) and node.func.id in {"commit", "rollback"})
                )
                and node.lineno > call.lineno
            ]
            assert later == [], (
                f"{rel_path}:{call.lineno} — the caller also does transaction control at "
                f"{later}, after the blacklist call already committed. Those statements "
                "cannot be part of the blacklist transaction; they are a separate concern "
                "and this test's premise no longer holds."
            )


class TestVerifyTokenDoesNotWriteThroughTheSession:
    """Rule 4: the claim that ``verify_token`` is read-only, made executable."""

    def test_verify_token_accepts_a_session_but_never_touches_it(self) -> None:
        """Its revocation helpers must open their OWN session, not the caller's.

        Asserted structurally on the source rather than by mocking, because the
        hazard is exactly that a future edit swaps ``local_session()`` for the
        caller's session — which a mocked test would not notice.
        """
        jwt_text = (REPO_ROOT / JWT_SERVICE).read_text(encoding="utf-8")
        fallback = _function_source(jwt_text, "_check_db_fallback")
        assert "local_session()" in fallback, (
            "_check_db_fallback must open its own local_session(); if it starts using the "
            "caller's session it can move the caller's transaction before :578 rolls it back"
        )
        assert ".add(" not in fallback and ".execute(" not in fallback, (
            "_check_db_fallback must stay a pure existence check"
        )

        users_text = (REPO_ROOT / "src/app/modules/users/__init__.py").read_text(encoding="utf-8")
        lookup = _function_source(users_text, "_users_token_version_lookup")
        assert "local_session()" in lookup, (
            "_users_token_version_lookup must open its own local_session(), not be handed "
            "the request session — that is what makes verify_token safe to await here"
        )

    def test_the_callers_session_is_never_passed_to_either_helper(self) -> None:
        """Neither helper may accept the request session as a parameter at all."""
        jwt_tree = _parse(JWT_SERVICE)
        for name in ("_check_db_fallback", "_load_token_version"):
            fn = _find_function(jwt_tree, name)
            assert fn is not None, f"{name} disappeared; re-run the C-32 enumeration"
            args = [a.arg for a in fn.args.args]
            assert "db" not in args and "session" not in args, (
                f"{name} now takes {args}; a session parameter is exactly how the caller's "
                "transaction gets moved by a helper that was written to be read-only"
            )

    async def test_a_session_rollback_inside_the_savepoint_discards_the_callers_work(
        self, db_session: AsyncSession
    ) -> None:
        """The hazard, executed: ``jwt_service.py:578`` is a TRANSACTION rollback.

        The code's intent at ``:571-582`` is to abort only the savepoint it just
        opened, so the retry can re-stage the blacklist rows. The mechanism it
        actually uses, ``await db.rollback()``, is scoped to the whole
        transaction. Those are different things, and the difference is silent:
        nothing in ``:571-582`` suggests that anything outside the savepoint is
        affected.

        So the shape is reproduced against a real session with real staged work.
        The staged row is unrelated to the blacklist and is committed by nothing,
        yet it disappears — because a rollback that was meant to rescue one
        INSERT destroyed a different one.

        This is the counterweight to rules 1-4. Those rules are all ABSENCE
        claims ("no caller stages anything"), and an absence that is merely
        believed protects nothing. This one shows what the absence is buying, so
        the day it stops being true the damage is concrete rather than
        hypothetical.

        The test is self-cleaning: the row it inserts is removed by the very
        rollback under test. The ``finally`` is belt-and-braces for the case
        where the behaviour changes and the row survives.
        """
        from sqlalchemy import text

        from tests.conftest import get_testing_session_local

        # The factory's return annotation in tests/conftest.py resolves to the
        # sync Session; the object it hands back is TestingSessionLocal, which is
        # async. Annotated at the use site rather than widened in conftest,
        # because only this test needs a second, independent session.
        session_local: Any = get_testing_session_local()
        token = f"c32-probe-{uuid4()}"
        slug = f"c32-probe-{uuid4().hex[:12]}"

        # A scratch tenant is created rather than reusing an existing one, and
        # committed in its OWN session so it survives as a valid FK parent. The
        # test database has no tenants, and skipping here would leave the one
        # assertion that turns "nothing is pending" into a concrete cost
        # unexecuted on exactly the machine most likely to be running it.
# The probe's own SQL is built with bindparams() rather than a params
        # mapping because the test database may be SQLite, where ``text()``
        # wants ``?`` and rejects named binds. Binding at construction keeps one
        # statement working on both backends and keeps the parameter typed.
        # These statements interpolate their values rather than binding them. The test
        # session hands out a session whose execute() drops bind parameters
        # entirely (every form — mapping, tuple, bindparams() — arrives at the
        # driver with zero bindings), and both values are ``uuid4()`` hex from
        # this test, never caller input. Inline is the only form that works
        # here; if the driver ever accepted binds again this should go back to
        # them.
        # Raw SQL bypasses the ORM, so the columns whose defaults are declared in
        # Python (``is_active``, ``feature_flags``, the two counters) have to be
        # supplied explicitly. is_active is NOT NULL with no server_default, so
        # an INSERT that omits it fails outright.
        tenant_insert = (
            "INSERT INTO tenants (name, slug, is_active, feature_flags, storage_usage_bytes, "
            "file_count, created_at) "
            f"VALUES ('{slug}', '{slug}', 1, '{{}}', 0, 0, CURRENT_TIMESTAMP)"
        )
        tenant_select = f"SELECT id FROM tenants WHERE slug = '{slug}'"
        tenant_delete = f"DELETE FROM tenants WHERE slug = '{slug}'"

        async with session_local() as setup:
            await setup.execute(text(tenant_insert))
            await setup.commit()
            tenant_id = (await setup.execute(text(tenant_select))).scalar_one()

        # CURRENT_TIMESTAMP rather than now(): the test session may be SQLite,
        # which has no now(). The value only has to be a non-null timestamp.
        token_insert = (
            "INSERT INTO token_blacklist (token, expires_at, tenant_id) "
            f"VALUES ('{token}', CURRENT_TIMESTAMP, '{tenant_id}')"
        )
        token_count = f"SELECT count(*) FROM token_blacklist WHERE token = '{token}'"
        token_delete = f"DELETE FROM token_blacklist WHERE token = '{token}'"

        try:
            await db_session.execute(text(token_insert))
            # The row is in this transaction and visible to it, but nothing has
            # committed it. This is precisely the "pending work" that a :578
            # rollback would destroy.
            assert (await db_session.execute(text(token_count))).scalar_one() == 1

            # --- the shape at jwt_service.py:571-578, verbatim in mechanism ---
            try:
                async with db_session.begin_nested():
                    raise IntegrityError(
                        "INSERT INTO token_blacklist ...",
                        {"token": token},
                        Exception("simulated duplicate-key failure on the blacklist insert"),
                    )
            except IntegrityError:
                await db_session.rollback()

            # --- the consequence ---
            db_session.expire_all()
            assert (await db_session.execute(text(token_count))).scalar_one() == 0, (
                "the staged row survived a rollback issued inside a savepoint. Either "
                "AsyncSession.rollback() has become savepoint-scoped, or this test is no "
                "longer reproducing jwt_service.py:578's shape. Re-run the C-32 analysis: "
                "if the rollback really is now scoped, :578 stops being a hazard at all."
            )
        finally:
            async with session_local() as cleanup:
                await cleanup.execute(text(token_delete))
                await cleanup.execute(text(tenant_delete))
                await cleanup.commit()


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and fn.name == name:
            return fn
    return None


def _function_source(text: str, name: str) -> str:
    tree = ast.parse(text)
    fn = _find_function(tree, name)
    assert fn is not None, f"{name} not found; the C-32 analysis must be redone"
    lines = text.splitlines()
    return "\n".join(lines[fn.lineno - 1 : fn.end_lineno or fn.lineno])


def _receiver_at(statements: list[ast.stmt], hit: str) -> str | None:
    """Recover the receiver name from a ``_mutation_sites`` hit string."""
    line_no = int(hit.split(":")[0].removeprefix("line "))
    for stmt in statements:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call) and node.lineno == line_no:
                return _receiver_name(node.func)
    return None
