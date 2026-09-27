"""An unwired RLS bypass recorder must not abort the worker job.

``_record_bypass_audit`` promises two things in its own docstring: the audit
trail never blocks a system job, and a lost trail is never silent (it is
surfaced as the loud ``audit_bypass_unaudited`` event).

``require_bypass_recorder()`` raises ``RuntimeError`` when the seam is
unwired. Resolved OUTSIDE the try, that raise escaped
``_record_bypass_audit``, propagated out of ``job_bypass_context``, and
aborted the job — inverting the contract in exactly the case the gate was
written to cover. Resolved inside the try, it becomes the counted event.

The worker-side boot gate is the second half: the worker is the only
process that runs ``job_bypass_context``, and without the gate it starts
clean and only discovers the missing recorder mid-job.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.worker import audit as audit_module
from src.app.core.worker import rls_context


@pytest.fixture(autouse=True)
def _clean_recorder():
    """Never leave the process-global recorder set by a test."""
    original = audit_module._bypass_recorder
    yield
    audit_module._bypass_recorder = original


class TestUnwiredRecorderNeverAbortsTheJob:
    @pytest.mark.asyncio
    async def test_unresolved_recorder_is_logged_not_raised(self) -> None:
        """Pre-fix this raised RuntimeError out of _record_bypass_audit."""
        audit_module._bypass_recorder = None

        with patch.object(rls_context.logger, "error") as mock_error:
            # Must NOT raise.
            await rls_context._record_bypass_audit("job reason")

        assert mock_error.called, "an unaudited bypass was not surfaced"
        assert mock_error.call_args.args[0] == "audit_bypass_unaudited"
        assert mock_error.call_args.kwargs["error_type"] == "RuntimeError"

    @pytest.mark.asyncio
    async def test_delivery_failure_is_logged_not_raised(self) -> None:
        """The recorder being present but failing behaves the same way."""
        recorder = AsyncMock(side_effect=ValueError("bad payload"))
        audit_module._bypass_recorder = recorder

        with patch.object(rls_context.logger, "error") as mock_error:
            await rls_context._record_bypass_audit("job reason")

        recorder.assert_awaited_once_with("job reason")
        assert mock_error.call_args.kwargs["error_type"] == "ValueError"

    @pytest.mark.asyncio
    async def test_working_recorder_still_audits(self) -> None:
        """The happy path must not log the unaudited event."""
        recorder = AsyncMock()
        audit_module._bypass_recorder = recorder

        with patch.object(rls_context.logger, "error") as mock_error:
            await rls_context._record_bypass_audit("job reason")

        recorder.assert_awaited_once_with("job reason")
        assert not mock_error.called

    @pytest.mark.asyncio
    async def test_job_bypass_context_survives_an_unwired_recorder(self) -> None:
        """The end-to-end contract: the job still yields a usable session."""
        audit_module._bypass_recorder = None

        entered = False
        with patch("src.app.core.db.rls.bypass_rls", _noop_bypass_rls()):
            async with rls_context.job_bypass_context("recovery job"):
                entered = True

        assert entered, "job_bypass_context aborted before yielding — the job would have failed"


class TestWorkerBootGate:
    @pytest.mark.asyncio
    async def test_worker_startup_fails_at_boot_when_recorder_unwired(self) -> None:
        """The worker must gate at BOOT, not discover it mid-job.

        The worker is the only process that runs ``job_bypass_context``.
        Without the gate it starts clean and only raises when a job
        actually takes an RLS bypass.

        The gate runs before any Redis work, so this needs no live backend:
        everything above it is stubbed and the RuntimeError is raised
        before the first pool is created.
        """
        from src.app.core.worker import functions

        audit_module._bypass_recorder = None

        with (
            patch.object(functions, "assert_all_jobs_registered", return_value=[]),
            patch("src.app.core.worker.registry.ensure_feature_tasks_registered"),
            patch("src.app.core.worker.registry.ensure_feature_collaborators_registered"),
        ):
            with pytest.raises(RuntimeError, match="RLS bypass recorder is not registered"):
                await functions.startup(_FakeWorker())

    @pytest.mark.asyncio
    async def test_worker_startup_calls_the_gate(self) -> None:
        """A renamed or dropped gate must be caught, not silently absent.

        Behavioural (not a source grep): the real gate is called, and the
        unwired case above proves it raises.
        """
        from src.app.core.worker import functions

        audit_module._bypass_recorder = None
        called: list[str] = []

        def _gate() -> None:
            called.append("gate")
            raise RuntimeError("RLS bypass recorder is not registered")

        with (
            patch.object(functions, "assert_all_jobs_registered", return_value=[]),
            patch("src.app.core.worker.registry.ensure_feature_tasks_registered"),
            patch("src.app.core.worker.registry.ensure_feature_collaborators_registered"),
            patch("src.app.core.worker.audit.ensure_bypass_recorder_registered", _gate),
        ):
            with pytest.raises(RuntimeError, match="RLS bypass recorder is not registered"):
                await functions.startup(_FakeWorker())

        assert called == ["gate"]

    def test_gate_raises_when_unwired(self) -> None:
        """The gate itself must fail loud in any environment."""
        audit_module._bypass_recorder = None
        with pytest.raises(RuntimeError, match="RLS bypass recorder is not registered"):
            audit_module.ensure_bypass_recorder_registered()

    def test_gate_passes_when_wired(self) -> None:
        audit_module._bypass_recorder = AsyncMock()
        audit_module.ensure_bypass_recorder_registered()  # must not raise


class _FakeWorker:
    """ARQ hands startup a Worker; the gate raises before it is touched."""

    def __init__(self) -> None:
        self.redis = None
        self.ctx: dict = {}


def _noop_bypass_rls():
    import contextlib

    @contextlib.asynccontextmanager
    async def _stub(session, *args, **kwargs):
        yield session

    return _stub
