"""The RLS-bypass recorder seam: core audits a bypass the app records.

``core.worker.rls_context`` records an event for every RLS bypass. That write
reached the audit-logs feature module, which the H-5 contract forbids from
core, so a bound recorder is registered from outside.

The gate here matters more than the others in this family. A missing recorder
would not fail loudly at the bypass: ``_record_bypass_audit`` swallows
exceptions by design so a job is never blocked by audit infra. Without the
boot gate, a job would proceed with a silently unaudited RLS bypass. These
tests pin that the gate exists and fires.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.app.core.worker import audit as audit_seam


@pytest.fixture
def unregistered():
    """Unwire the seam, then restore whatever was there."""
    saved = audit_seam._bypass_recorder
    audit_seam.reset_bypass_recorder()
    yield
    audit_seam._bypass_recorder = saved


def test_require_raises_when_unwired(unregistered):
    with pytest.raises(RuntimeError, match="RLS bypass recorder is not registered"):
        audit_seam.require_bypass_recorder()


def test_boot_gate_raises_when_unwired(unregistered):
    """A silently unaudited RLS bypass is a security failure, not a nuisance."""
    with pytest.raises(RuntimeError, match="RLS bypass recorder is not registered"):
        audit_seam.ensure_bypass_recorder_registered()


def test_application_seam_registers_the_recorder():
    import src.app.modules.audit_logs  # noqa: F401 — the import is the registration

    assert callable(audit_seam.require_bypass_recorder())
    audit_seam.ensure_bypass_recorder_registered()


async def test_the_registered_recorder_writes_the_bypass_event(monkeypatch):
    """It records system.rls_bypass with the reason, at BEST_EFFORT."""
    import src.app.modules.audit_logs as pkg

    seen: dict = {}

    async def _fake_audit(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(pkg, "audit", _fake_audit)

    await pkg._record_rls_bypass("nightly reconciliation")

    assert seen["action"] == "system.rls_bypass"
    assert seen["entity_type"] == "system"
    assert seen["entity_id"] == "rls_context"
    assert seen["details"] == {"reason": "nightly reconciliation"}
    assert seen["guarantee"] is pkg.AuditGuarantee.BEST_EFFORT


def test_a_failing_recorder_does_not_block_the_job():
    """Record-then-proceed: the bypass is granted even when the audit fails."""
    import asyncio

    from src.app.core.worker import rls_context

    async def _boom(reason: str) -> None:
        raise RuntimeError("audit down")

    saved = audit_seam._bypass_recorder
    audit_seam.register_bypass_recorder(_boom)
    try:
        # Must not raise, or every job would fail when audit infra is down.
        asyncio.run(rls_context._record_bypass_audit("test reason"))
    finally:
        audit_seam._bypass_recorder = saved


def test_reset_is_blocked_in_production(monkeypatch):
    from src.app.core.config import EnvironmentOption

    monkeypatch.setattr(audit_seam.settings.env, "ENVIRONMENT", EnvironmentOption.PRODUCTION)
    with pytest.raises(RuntimeError, match="test-only and blocked in production"):
        audit_seam.reset_bypass_recorder()


def test_rls_context_does_not_import_the_feature_module():
    """The edge this seam exists to remove must not creep back.

    Scoped to rls_context rather than the whole worker package on purpose:
    ``core/worker/functions.py`` still imports audit_logs.service for the
    flush task. That is a separate, known edge — the worker-registry group —
    and asserting it away here would hide it rather than fix it.
    """
    for path in [Path("src/app/core/worker/rls_context.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("src.app.modules.audit_logs"), (
                    f"{path} imports {node.module}; the record goes through a "
                    "registered recorder instead"
                )
