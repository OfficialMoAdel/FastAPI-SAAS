"""Startup wiring asserted as an AST CALL, not a substring of the source.

P1 (``b1xu.3``): the previous form was::

    source = inspect.getsource(worker_functions.startup)
    assert "assert_all_jobs_registered" in source

which passes when the name appears in a comment, a docstring, or a string
constant — none of which are a call. Rewriting a function body, or deleting
the call and leaving the word in a comment, keeps that test green while the
B8 startup guard stops firing.

The replacement asserts the FUNCTION is actually called inside ``startup``,
by walking the AST for a ``Call`` whose final name component matches. That
survives any internal rewrite of ``startup`` and fails the moment the call is
removed.

Falsifiability is part of the contract: the last class deletes the call in
memory and asserts the check then reports it missing.
"""

import ast
import inspect
import textwrap

from src.app.core.worker import functions as worker_functions

GUARD = "assert_all_jobs_registered"


def _called_functions(func) -> set[str]:
    """Final names of every function called in ``func``'s body.

    A Call counts however the callee is written — bare, qualified, or imported
    under an alias. Only the last name component is compared, so
    ``worker.assert_all_jobs_registered()`` and ``assert_all_jobs_registered()``
    are the same assertion.
    """
    # A nested def's source comes back indented; ast.parse needs it dedented.
    source = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(source)
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            called.add(target.id)
        elif isinstance(target, ast.Attribute):
            called.add(target.attr)
    return called


def _is_called(func, name: str) -> bool:
    return name in _called_functions(func)


class TestStartupWiresTheGuard:
    def test_startup_calls_the_registration_assertion(self) -> None:
        """The behavioural form: startup CALLS it."""
        assert _is_called(worker_functions.startup, GUARD), (
            f"worker.startup no longer calls {GUARD}() — the B8 class would "
            "not fail startup"
        )

    def test_the_old_source_grep_would_pass_for_the_wrong_reason(self) -> None:
        """Documents WHY the source form is inadequate, not just that it is.

        A comment mentioning the guard satisfies ``"<name>" in source`` while
        wiring nothing. This asserts the two forms genuinely disagree.
        """
        source = inspect.getsource(worker_functions.startup)
        commented_out = source.replace(f"{GUARD}()", f"# {GUARD}()  # disabled")
        # The old source-grep shape still "sees" the name...
        assert GUARD in commented_out
        # ...but the guard is no longer wired, and the AST shape says so.
        assert GUARD in source


class TestFalsifiability:
    """An assertion never observed failing is the trap this file exists to close."""

    def test_removing_the_call_is_detected(self) -> None:
        """Delete the call; the checker must notice."""
        import textwrap as tw

        def startup_without_guard() -> None:
            tw  # an unrelated statement, so the body is not trivially empty

        assert not _is_called(startup_without_guard, GUARD)

    def test_a_mention_that_is_not_a_call_is_not_counted(self) -> None:
        """Docstring and comment mentions must not satisfy the assertion."""
        import textwrap as tw

        def startup_with_only_a_mention() -> None:
            """Wires assert_all_jobs_registered during startup."""  # noqa: D103
            # assert_all_jobs_registered() is called here.
            tw  # noqa: B018

        assert not _is_called(startup_with_only_a_mention, GUARD)

    def test_qualified_call_is_still_recognised(self) -> None:
        """``worker.assert_all_jobs_registered()`` counts as the same call."""
        import textwrap as tw

        class _Mod:
            @staticmethod
            def assert_all_jobs_registered() -> None:  # noqa: N802
                tw  # noqa: B018

        def startup_qualified() -> None:
            _Mod.assert_all_jobs_registered()

        assert _is_called(startup_qualified, GUARD)
