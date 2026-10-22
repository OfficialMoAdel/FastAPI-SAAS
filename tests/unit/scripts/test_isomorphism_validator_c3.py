"""C-3's two closed worlds, pinned so the gate cannot drift silently.

WHAT THIS FILE IS FOR
---------------------
`scripts/perf-validation/isomorphism_validator.py` runs in CI
(`.github/workflows/tests.yml`, the "Run isomorphism tests" step) and its
`C3_enumeration` gate was RED against HEAD: the pinned caller set named
four functions that no longer hold a session.

THE DEFECT, AS MEASURED
-----------------------
Commit `ec632f29` (2026-10-17) refactored the auth path: the session is
no longer injected into `get_current_user` as a
`Depends(async_get_db)` parameter, it is opened inside the shared
resolver with `async with get_db_context() as _db:`. So:

  * the four former entries became PURE DELEGATORS -- their signatures
    carry no session parameter, and they are not in the holder scan any
    more; and
  * the whole `get_db_context()` class -- eight functions, including the
    auth path's own `resolve_principal` -- was never enumerated and
    never checked at all.

The gate was not wrong about the four; it was wrong about the WORLD it
was describing. A gate that models one way of obtaining a session and
claims to model all of them is a gate that silently stops covering the
other way.

THE TWO CLOSED WORLDS
---------------------
  HOLDERS: `db: Annotated[AsyncSession, Depends(async_get_db)]` -- the
           session is created by FastAPI before the dependency body runs.
  OPENERS: `async with get_db_context() as X:` -- the session is opened
           by the function itself.

Both are enumerated exhaustively and both are pinned, so a new member of
either class fails a gate until it is classified -- the C-1 discipline
("no unclassified path reader").

WHAT IS NOT ASSERTED HERE
-------------------------
No latency, no throughput, no timing. This file asserts STRUCTURE: which
functions are in each closed world, that the two enumerations are
self-consistent with the code, that no except handler READS a session,
and -- the load-bearing one -- that removing the four delegators from the
holder set did not lose the auth path's coverage.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR_PATH = REPO_ROOT / "scripts" / "perf-validation" / "isomorphism_validator.py"

# The four that were in the pinned holder set and no longer hold a
# session. They are listed here EXPLICITLY so that a future refactor
# that re-introduces a session parameter into one of them turns a test
# red instead of quietly widening the gate.
FORMER_HOLDERS: tuple[tuple[str, str], ...] = (
    ("src/app/api/deps/auth.py", "get_current_user"),
    ("src/app/api/deps/auth.py", "get_optional_auth_context"),
    ("src/app/api/deps/auth.py", "get_optional_user"),
    ("src/app/core/principal.py", "get_auth_context"),
)

# The FIFTH former holder (Cluster C / C1, 2026-10-22).
# `core.rbac.dependencies._check_permission` held a session ONLY to hand it
# to the permission lookup. The C1 fix gave the RBAC bitmask singleflight its
# own short-lived session -- it must not run on a request's session, because
# the task is awaited by OTHER requests -- so the dependency no longer needs
# one and no longer takes one. It holds no session to classify, so it was
# removed from the pinned holder set.
FORMER_HOLDERS_C1: tuple[tuple[str, str], ...] = (
    ("src/app/core/rbac/dependencies.py", "_check_permission"),
)

# The chain from those four to a session, edge by edge. Each edge is a
# source-level fact; together they are what makes the removal of the
# four from the holder set a correction rather than a loss of coverage.
#   get_current_user      --Depends(get_auth_context)-->  get_auth_context
#   get_optional_user    --Depends(get_optional_auth_context)--> ...
#   get_optional_auth_context --calls--> resolve_principal
#   get_auth_context     --reads--> _principal_provider
#   _principal_provider  --calls--> resolve_principal
#   resolve_principal    --opens--> get_db_context
SESSION_CHAIN: tuple[tuple[str, str, str], ...] = (
    ("src/app/api/deps/auth.py", "get_current_user", "get_auth_context"),
    ("src/app/api/deps/auth.py", "get_optional_user", "get_optional_auth_context"),
    ("src/app/api/deps/auth.py", "get_optional_auth_context", "resolve_principal"),
    ("src/app/core/principal.py", "get_auth_context", "_principal_provider"),
    ("src/app/api/deps/auth.py", "_principal_provider", "resolve_principal"),
    ("src/app/api/deps/auth.py", "resolve_principal", "get_db_context"),
)


@pytest.fixture(scope="module")
def validator() -> Any:
    spec = importlib.util.spec_from_file_location("c3_isomorphism_validator", VALIDATOR_PATH)
    assert spec is not None, f"cannot load {VALIDATOR_PATH}"
    assert spec.loader is not None, f"no loader for {VALIDATOR_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text(encoding="utf-8")


def _function_node(relpath: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(_source(relpath))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{relpath} has no function {name}")


def _mentions(node: ast.AST, name: str) -> bool:
    """True if `name` appears as a loaded Name anywhere in `node`."""
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


class TestTheGateItselfIsGreen:
    """The whole validator, run as a unit test.

    This is the RED-first assertion: before the fix the validator
    reported 12 of 13 gates passing and printed "Isomorphism Validation:
    FAILED", and the C3_enumeration gate was the failure.
    """

    @pytest.mark.asyncio
    async def test_every_gate_in_the_validator_passes(self, validator: Any) -> None:
        outcome = await validator.run_isomorphism_validation()
        assert outcome["all_passed"] is True, outcome["failures"]
        assert outcome["failures"] == []

    @pytest.mark.asyncio
    async def test_the_c3_gates_are_registered_and_named(self, validator: Any) -> None:
        names = [test.name for test in validator.ISOMORPHISM_TESTS]
        assert "C3_enumeration" in names
        assert "C3_opener_enumeration" in names
        assert "C3_no_handler_reads" in names

    @pytest.mark.asyncio
    async def test_the_c2_gates_still_pass(self, validator: Any) -> None:
        """The other half of the same file, and the gate C-2 depends on.

        `FastAPI_saas-niix` (C-2) requires `LC-C2-OR` to be green before
        its diff. A fix for C-3 that broke C-2 would move the failure
        rather than settle it.
        """
        outcome = await validator.run_isomorphism_validation()
        passed = {result["name"]: result["passed"] for result in outcome["results"]}
        assert passed["C2_semicolon_gate"] is True
        assert passed["C2_pixel_ua"] is True


class TestTheTwoClosedWorldsArePinned:
    def test_the_holder_set_is_a_frozenset(self, validator: Any) -> None:
        """`tests/e2e/feature_validation/test_dxra_three_401_shapes.py`
        asserts this, so the type is load-bearing."""
        assert isinstance(validator.C3_DB_CALLERS, frozenset)

    def test_the_holder_set_is_the_pinned_fifty_nine(self, validator: Any) -> None:
        """59 since the Cluster C / C1 fix removed the fifth former holder.

        The count is a ratchet: it moves DOWN when a holder stops holding a
        session, and a new holder fails the enumeration gate until it is
        classified. It never moves up without a new classification.
        """
        assert len(validator.C3_DB_CALLERS) == 59

    def test_the_opener_set_is_a_frozenset_of_eight(self, validator: Any) -> None:
        assert isinstance(validator.C3_SESSION_OPENERS, frozenset)
        assert len(validator.C3_SESSION_OPENERS) == 8

    def test_the_four_former_holders_are_not_in_the_holder_set(self, validator: Any) -> None:
        for key in FORMER_HOLDERS:
            assert key not in validator.C3_DB_CALLERS, f"{key} was re-added to the holder set"

    def test_the_c1_former_holder_is_not_in_the_holder_set(self, validator: Any) -> None:
        """Cluster C / C1: the RBAC dependency stopped holding a session."""
        for key in FORMER_HOLDERS_C1:
            assert key not in validator.C3_DB_CALLERS, f"{key} was re-added to the holder set"

    def test_the_holder_enumeration_matches_the_code(self, validator: Any) -> None:
        found = validator._c3_db_caller_functions()
        assert set(found) == set(validator.C3_DB_CALLERS)

    def test_the_opener_enumeration_matches_the_code(self, validator: Any) -> None:
        found = validator._c3_session_opener_functions()
        assert set(found) == set(validator.C3_SESSION_OPENERS)

    def test_the_holder_and_opener_sets_do_not_overlap(self, validator: Any) -> None:
        """A function can be in both classes, but then it is in BOTH
        enumerations -- so a function in one and not the other means one
        of the two scans is wrong."""
        overlap = set(validator.C3_DB_CALLERS) & set(validator.C3_SESSION_OPENERS)
        assert overlap == set()


class TestTheC1FormerHolderHoldsNoSession:
    """The code fact that justifies removing the RBAC dependency from the
    holder set (Cluster C / C1)."""

    @pytest.mark.parametrize("relpath,name", FORMER_HOLDERS_C1)
    def test_the_signature_carries_no_session_parameter(self, relpath: str, name: str) -> None:
        node = _function_node(relpath, name)
        args = node.args
        params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        for param in params:
            annotation = ast.unparse(param.annotation) if param.annotation is not None else ""
            default = getattr(param, "default", None)
            default_text = ast.unparse(default) if default is not None else ""
            assert "async_get_db" not in annotation
            assert "async_get_db" not in default_text

    def test_the_permission_lookup_opens_its_own_session(self) -> None:
        """The session it used to hold now lives in the task's own session.

        That is the OPENERS world (a different acquisition mechanism), and
        it is why the dependency no longer needs one.
        """
        node = _function_node("src/app/core/rbac/dependencies.py", "_compute_and_cache_bitmask")
        assert "local_session()" in ast.unparse(node)


class TestTheFourDelegatorsHoldNoSession:
    """The code fact that justifies removing them from the holder set."""

    @pytest.mark.parametrize("relpath,name", FORMER_HOLDERS)
    def test_the_signature_carries_no_session_parameter(self, relpath: str, name: str) -> None:
        node = _function_node(relpath, name)
        args = node.args
        params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        for param in params:
            annotation = ast.unparse(param.annotation) if param.annotation is not None else ""
            default = getattr(param, "default", None)
            default_text = ast.unparse(default) if default is not None else ""
            assert "async_get_db" not in annotation
            assert "async_get_db" not in default_text

    @pytest.mark.parametrize("relpath,name", FORMER_HOLDERS)
    def test_the_body_does_not_open_a_session_itself(self, relpath: str, name: str) -> None:
        node = _function_node(relpath, name)
        assert "get_db_context" not in ast.unparse(node)

    def test_the_auth_path_opens_its_session_inside_the_shared_resolver(self) -> None:
        """`resolve_principal` is the function that opens the session for
        the whole auth path now."""
        node = _function_node("src/app/api/deps/auth.py", "resolve_principal")
        assert "get_db_context" in ast.unparse(node)


class TestRemovingTheFourLosesNoCoverage:
    """The load-bearing assertion.

    The four delegators left the holder set. That is only a correction,
    not a loss, if they still reach a session -- and they must reach it
    through a class the gate DOES enumerate. If they reached a session
    through some third mechanism the gate does not model, the removal
    would have dropped the auth path off the map entirely.
    """

    @pytest.mark.parametrize("relpath,name,target", SESSION_CHAIN)
    def test_each_edge_of_the_chain_holds(self, relpath: str, name: str, target: str) -> None:
        node = _function_node(relpath, name)
        assert _mentions(node, target), f"{relpath}::{name} no longer mentions {target}"

    def test_every_former_holder_reaches_a_pinned_opener(self, validator: Any) -> None:
        """Transitively.

        `get_current_user` reaches an opener through `get_auth_context`
        and the registered principal provider, not by mentioning an
        opener itself -- so the assertion has to follow the chain. The
        chain's edges are pinned individually above; this checks that
        they connect each of the four to an opener.
        """
        opener_names = {name for _, name in validator.C3_SESSION_OPENERS}
        # REVERSE the chain: "who points at this name" -- then walking
        # forward from the openers yields every name that reaches one.
        incoming: dict[str, set[str]] = {}
        for _relpath, name, target in SESSION_CHAIN:
            incoming.setdefault(target, set()).add(name)
        reaching = set()
        frontier = set(opener_names)
        while frontier:
            reaching |= frontier
            nxt: set[str] = set()
            for name in frontier:
                nxt |= incoming.get(name, set())
            frontier = nxt - reaching
        for _relpath, name in FORMER_HOLDERS:
            assert name in reaching, f"{name} reaches no pinned opener"

    def test_the_opener_the_chain_ends_at_is_pinned(self, validator: Any) -> None:
        assert ("src/app/api/deps/auth.py", "resolve_principal") in validator.C3_SESSION_OPENERS

    def test_the_principal_provider_is_registered_from_the_api_layer(self) -> None:
        """`get_auth_context` lives in `core/principal.py` and reads a
        module-global provider, because core must not import the api
        layer. The registration is what makes the chain complete."""
        source = _source("src/app/api/deps/auth.py")
        assert "register_principal_provider(_principal_provider)" in source

    def test_the_core_module_does_not_import_the_api_layer(self) -> None:
        """`core/principal.py` names the api layer in its DOCSTRING (it
        explains which import sites keep working), so this checks the
        imports themselves, not the text."""
        tree = ast.parse(_source("src/app/core/principal.py"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("src.app.api"), node.module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("src.app.api"), alias.name


class TestNoHandlerReadsASession:
    """C-3's 401-path property, asserted over BOTH closed worlds.

    The plan's own wording is "no caller READS from the session on a 401
    path". A rollback is not a read, and the measurement behind
    `C3_ALLOWED_HANDLER_SESSION_CALLS` shows it cannot put a request on
    the database: a rollback on a session that never connected performs
    zero pool checkouts and zero statements, where a query on the same
    kind of session performs one of each.
    """

    def test_a_rollback_is_the_only_permitted_use(self, validator: Any) -> None:
        assert validator.C3_ALLOWED_HANDLER_SESSION_CALLS == frozenset({"rollback"})

    def test_no_holder_or_opener_reads_a_session_in_a_handler(self, validator: Any) -> None:
        names = validator._c3_session_names()
        assert names, "the session name map is empty -- the gate would be vacuous"
        violations: dict[str, list[str]] = {}
        for (rel, fname), session_names in names.items():
            node = _function_node(rel, fname)
            for session_name in session_names:
                found, _permitted = validator._c3_handler_session_uses(node, session_name)
                for line, what in found:
                    violations.setdefault(f"{rel}:{node.lineno} {fname}", []).append(f"{line} {what}")
        assert violations == {}

    def test_the_permitted_uses_are_the_two_known_rollbacks(self, validator: Any) -> None:
        """They are LOGGED, not hidden, so a permitted use that is really
        a read would show up here. This pins the count and the sites."""
        names = validator._c3_session_names()
        permitted: dict[str, list[str]] = {}
        for (rel, fname), session_names in names.items():
            node = _function_node(rel, fname)
            for session_name in session_names:
                _found, ok = validator._c3_handler_session_uses(node, session_name)
                for line, what in ok:
                    permitted.setdefault(f"{rel}:{fname}", []).append(f"{line} {what}")
        assert permitted == {
            "src/app/modules/audit_logs/service.py:flush_audit_log_buffer": ["670 .rollback"],
            "src/app/modules/webhooks/delivery.py:recover_stuck_deliveries": ["661 .rollback"],
        }

    def test_a_rollback_is_not_mistaken_for_a_read(self, validator: Any) -> None:
        """The distinction is the whole point of the rule."""
        node = _function_node("src/app/modules/webhooks/delivery.py", "recover_stuck_deliveries")
        found, permitted = validator._c3_handler_session_uses(node, "db")
        assert found == []
        assert permitted, "the rollback was not recognised as a permitted use"

    def test_the_detector_actually_detects_a_read(self, validator: Any) -> None:
        """NON-VACUITY.

        Every other test in this class asserts that no violation exists.
        If the detector stopped detecting anything, all of them would
        pass while the gate checked nothing. This builds the two
        synthetic functions the check has to tell apart and asserts the
        difference, so the detector is exercised on a case where the
        answer is known.
        """
        read = ast.parse(
            "async def probe(session):\n"
            "    try:\n"
            "        pass\n"
            "    except Exception:\n"
            "        await session.execute(stmt)\n"
        ).body[0]
        rollback = ast.parse(
            "async def probe(session):\n"
            "    try:\n"
            "        pass\n"
            "    except Exception:\n"
            "        await session.rollback()\n"
        ).body[0]
        read_violations, read_permitted = validator._c3_handler_session_uses(read, "session")
        assert read_violations and read_violations[0][1] == ".execute", read_violations
        assert not read_permitted
        rb_violations, rb_permitted = validator._c3_handler_session_uses(rollback, "session")
        assert rb_violations == []
        assert rb_permitted and rb_permitted[0][1] == ".rollback", rb_permitted

    def test_a_bare_reference_to_the_session_is_a_violation(self, validator: Any) -> None:
        """Passing the session to something else is a use, and it is not
        a rollback."""
        bare = ast.parse(
            "async def probe(session):\n"
            "    try:\n"
            "        pass\n"
            "    except Exception:\n"
            "        helper(session)\n"
        ).body[0]
        violations, permitted = validator._c3_handler_session_uses(bare, "session")
        assert violations and violations[0][1] == "bare reference", violations
        assert not permitted
