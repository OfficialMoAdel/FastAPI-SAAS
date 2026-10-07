"""s92p.2: the H-5 contract passes, and the registration still happens.

The H-5 contract was broken by a transitive edge:
``core.worker.settings -> src.app.worker -> modules.*``. Core cannot name a
feature module, but it MUST import the composition root, because the ARQ CLI
loads ``WorkerSettings`` by dotted path and offers no call-in point before
evaluating it.

The composition seam is now allowlisted (the same shape as the pre-existing
``core.setup -> src.app.models`` entry), and these tests pin both halves of
that decision:

* the contract actually passes, and still catches a NEW core->feature edge;
* the collaborator seam is really wired, so the audit flush in the worker
  still runs — a silent audit-flush failure is the regression that a
  contract-only fix would introduce.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from src.app.core.worker import registry

# This file is tests/core/<file>.py, so the repo root is parents[2].
REPO = Path(__file__).resolve().parents[2]

#: See the same constant in test_feature_api_contract.py: the hardcoded
#: `.venv/Scripts/lint-imports.exe` is a Windows-only layout and does not exist
#: on the Linux runner, so the contract could not report anything there.
LINT_IMPORTS_ARGV = [sys.executable, "-c", "from importlinter.cli import lint_imports_command as c; c()"]


def _lint_imports() -> tuple[int, str]:
    """Run the contracts; return (exit_code, output)."""
    result = subprocess.run(
        LINT_IMPORTS_ARGV,
        cwd=REPO / "src",
        capture_output=True,
        text=True,
        timeout=600,
    )
    return result.returncode, result.stdout + result.stderr


class TestContractIsGreen:
    def test_contracts_pass(self) -> None:
        code, out = _lint_imports()
        assert "0 broken" in out, f"import contracts failing:\n{out}"
        assert code == 0, f"lint-imports exited {code}:\n{out}"

    def test_composition_seam_is_allowlisted(self) -> None:
        """The exemption is explicit and explained, not implicit."""
        config = (REPO / "src/.importlinter").read_text(encoding="utf-8")
        assert "src.app.core.worker.settings -> src.app.worker" in config, (
            "the composition seam must be recorded, or the contract fails on the "
            "edge the ARQ CLI makes structurally unavoidable"
        )
        assert "A-M7" in config, "the entry must name its rationale"

    def test_stale_claim_was_corrected(self) -> None:
        """The false 'last live edge' sentence must not come back."""
        config = (REPO / "src/.importlinter").read_text(encoding="utf-8")
        # The old sentence is quoted only inside a CORRECTION block that says
        # it was false. If it is ever stated as fact again, drop the marker.
        if "That was the last live edge" in config:
            assert "CORRECTION" in config, (
                "the false 'last live edge' claim is stated as fact again"
            )


class TestGateStillBites:
    """An allowlist that hides everything is worse than a red contract."""

    def test_a_new_core_to_feature_edge_would_be_caught(self) -> None:
        """Falsifiability, proven by injecting a real violation and reverting.

        The gate is only worth its allowlist if it still fails on a NEW edge.
        This adds a live ``core -> feature`` import, confirms the contract
        breaks, then restores the file.
        """
        target = REPO / "src/app/core/logger.py"
        original = target.read_bytes()
        try:
            target.write_text(
                original.decode("utf-8")
                + "\nfrom src.app.modules.audit_logs.service import flush_audit_log_buffer\n",
                encoding="utf-8",
            )
            code, out = _lint_imports()
            assert "BROKEN" in out, f"a new core->feature import was NOT caught:\n{out}"
            assert code != 0
        finally:
            target.write_bytes(original)


class TestCollaboratorSeamIsReallyWired:
    """The contract is green; the audit flush must still actually happen."""

    def test_flush_helper_comes_from_the_registered_seam(self) -> None:
        """core/worker/functions.py must read it from the collaborators record."""
        import inspect

        from src.app.core.worker import functions as worker_functions

        source = inspect.getsource(worker_functions)
        assert "require_feature_collaborators" in source, (
            "the worker no longer resolves the collaborator seam"
        )
        # It reads the FIELD, rather than importing the feature function.
        assert "collaborators.flush_audit_log_buffer" in source

    @pytest.mark.asyncio
    async def test_registered_collaborator_is_callable(self) -> None:
        """register -> require round-trips a working flush callable."""
        from src.app.core.worker.registry import (
            FeatureCollaborators,
            register_feature_collaborators,
            require_feature_collaborators,
        )

        called: list[int] = []

        async def _flush(*, max_flush_per_run: int, db=None) -> int:
            called.append(max_flush_per_run)
            return 0

        saved = registry._feature_collaborators
        try:
            register_feature_collaborators(
                FeatureCollaborators(
                    deliver=lambda *a, **k: None,
                    flush_audit_log_buffer=_flush,
                    audit_log_max_flush_per_run=500,
                    release_webhook_event_lock=lambda *a, **k: None,
                )
            )
            collaborators = require_feature_collaborators()
            assert await collaborators.flush_audit_log_buffer(max_flush_per_run=10) == 0
            assert called == [10], "the registered flush must be the one invoked"
        finally:
            # The registry global is process-wide. Leaving a stub registered
            # makes every later test that asserts the collaborators are the
            # REAL feature callables compare against these lambdas — an
            # order-dependent failure in a file that contains no bug.
            registry._feature_collaborators = saved


class TestTypeCheckingIsNotARuntimeEdge:
    """The reported users/router -> api.deps edge is TYPE_CHECKING-only."""

    def test_importing_users_does_not_load_the_router(self) -> None:
        """Executable form of the false-positive claim.

        modules/users/__init__.py wraps the router and api.deps re-exports in
        a TYPE_CHECKING block with an explicit "never executed at runtime"
        comment. A fresh interpreter proves it.
        """
        code = (
            "import sys\n"
            "import src.app.modules.users\n"
            "bad = [m for m in ('src.app.api.deps', 'src.app.modules.users.router')\n"
            "       if m in sys.modules]\n"
            "print('LOADED:' + ','.join(bad) if bad else 'CLEAN')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=300,
        )
        out = result.stdout + result.stderr
        assert "CLEAN" in out, f"a TYPE_CHECKING-only import was executed at runtime:\n{out}"
