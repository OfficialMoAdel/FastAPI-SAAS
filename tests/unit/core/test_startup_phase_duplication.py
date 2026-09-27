"""Startup phases must not be declared twice.

Two duplicates were removed:

  - ``set_threadpool_tokens`` ran as a bare ``await`` AND as the
    "threadpool" StartupPhase. ``compose_phases`` executes every phase, so
    the bare call was a second run. Idempotent today; a trap the moment the
    body is not.
  - ``tenant_model_gate`` was in BOTH the web ``phases`` list (inside
    ``lifespan_factory``) and ``lifespan_with_admin``'s ``admin_phases``.
    ``lifespan_with_admin`` composes ``lifespan_factory``'s lifespan, so the
    gate had already run before the admin phases began. The second copy's
    comment justified it by what "the worker" needs — a worker concern
    sitting in the web phase list.

These are asserted by COUNTING the declarations, which is legitimate here:
the duplicate is itself a source-level property, and the count is backed by
a behavioural check that the gate actually runs.
"""

import ast
import contextlib
from pathlib import Path
from unittest.mock import patch

import pytest

import src.app.composition as composition

TREE = ast.parse(Path(composition.__file__).read_text(encoding="utf-8"))


def _fn(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    # lifespan_with_admin is async; lifespan_factory is a sync factory
    # returning a context manager. Match both node kinds.
    return next(
        n
        for n in ast.walk(TREE)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
    )


def _phase_names(fn_name: str) -> list[str]:
    """Every StartupPhase name declared in one function."""
    names: list[str] = []
    for node in ast.walk(_fn(fn_name)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "StartupPhase"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.append(node.args[0].value)
    return names


class TestNoDuplicatePhases:
    def test_threadpool_is_declared_once(self) -> None:
        names = _phase_names("lifespan_factory")
        assert names.count("threadpool") == 1, f"threadpool declared {names.count('threadpool')}x"

    def test_threadpool_has_no_bare_call_left(self) -> None:
        """The bare await was the other half of the duplication.

        Walks the real AST with parent links: every reference to
        set_threadpool_tokens must be an ARGUMENT of StartupPhase. A bare
        one appears as Call(Attribute(set_threadpool_tokens)) outside that.
        """
        fn = _fn("lifespan_factory")
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(fn):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node

        bare: list[int] = []
        phase_arg: list[int] = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and node.attr == "set_threadpool_tokens":
                parent = parents.get(id(node))
                if (
                    isinstance(parent, ast.Call)
                    and isinstance(parent.func, ast.Name)
                    and parent.func.id == "StartupPhase"
                ):
                    phase_arg.append(node.lineno)
                else:
                    bare.append(node.lineno)

        assert not bare, (
            f"bare set_threadpool_tokens() call at line(s) {bare}, alongside "
            f"the StartupPhase at line(s) {phase_arg}"
        )

    def test_tenant_model_gate_is_declared_once(self) -> None:
        web = _phase_names("lifespan_factory")
        admin = _phase_names("lifespan_with_admin")
        total = web.count("tenant_model_gate") + admin.count("tenant_model_gate")
        assert total == 1, (
            f"tenant_model_gate declared {total}x (web={web.count('tenant_model_gate')}, "
            f"admin={admin.count('tenant_model_gate')}); lifespan_with_admin composes "
            "lifespan_factory, so the admin copy is a second run"
        )

    def test_the_gate_lives_in_the_web_lifespan(self) -> None:
        assert "tenant_model_gate" in _phase_names("lifespan_factory")
        assert "tenant_model_gate" not in _phase_names("lifespan_with_admin")

    def test_no_phase_name_is_duplicated_within_one_list(self) -> None:
        for fn_name in ("lifespan_factory", "lifespan_with_admin"):
            names = _phase_names(fn_name)
            dupes = {n for n in names if names.count(n) > 1}
            assert not dupes, f"{fn_name} declares {sorted(dupes)} more than once"

    def test_threadpool_is_still_the_first_phase(self) -> None:
        """Removing the bare call must not change when it runs."""
        assert _phase_names("lifespan_factory")[0] == "threadpool"


class TestTheGateStillRunsBehaviourally:
    @pytest.mark.asyncio
    async def test_lifespan_factory_still_runs_the_tenant_model_gate(self) -> None:
        """The de-duplication must not remove the gate itself.

        compose_phases is stubbed so no Redis/DB is needed; this asserts the
        phase sequence handed to it still contains the gate.
        """
        from src.app.core.config import settings

        captured: list = []

        @contextlib.asynccontextmanager
        async def _capture(phases, tail=(), teardown_on_startup_failure=False):
            captured.append([p.name for p in phases])
            yield

        with patch("src.app.composition.compose_phases", _capture):
            lifespan = composition.lifespan_factory(settings, create_tables_on_start=False)
            app = type("A", (), {"state": type("S", (), {})()})()
            async with lifespan(app):
                pass

        assert captured, "compose_phases was never called"
        assert "tenant_model_gate" in captured[0]
        assert "threadpool" in captured[0]
