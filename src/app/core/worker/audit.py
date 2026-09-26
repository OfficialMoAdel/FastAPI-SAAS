"""Access to the audit trail, supplied by the application seam.

``core.worker.rls_context`` records an audit event for every RLS bypass. That
write reaches the audit-logs feature module, which the H-5 contract forbids
from core, so the record is registered from outside.

What is registered is a *bound recorder* for this one event, not the general
``audit`` entry point with its twelve parameters. The worker owns what it
means to bypass RLS and insists the record must never block a job, while the
audit module owns the event schema and the delivery guarantee. Registering the
narrow operation splits them at that line: core keeps the
record-then-proceed policy and the loud ``audit_bypass_unaudited`` failure,
the feature keeps how the record is written.

Same shape as the Principal provider, tenant-model, permission-graph and
rate-policy seams.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.app.core.config import EnvironmentOption, settings
from src.app.core.logger import get_logger

logger = get_logger(__name__)

# (reason) -> record an ``system.rls_bypass`` event. May raise; the caller
# decides what a failure means.
BypassRecorder = Callable[[str], Awaitable[None]]

_bypass_recorder: BypassRecorder | None = None


def register_bypass_recorder(recorder: BypassRecorder) -> None:
    """Register the RLS-bypass recorder. Called by the application seam."""
    global _bypass_recorder
    _bypass_recorder = recorder


def reset_bypass_recorder() -> None:
    """Reset the registered recorder (test isolation).

    TEST-ONLY, blocked in production: a reset there would stop audit records
    for RLS bypasses at request time, which is a security regression rather
    than a boot failure.
    """
    if settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION:
        raise RuntimeError("reset_bypass_recorder is test-only and blocked in production")
    global _bypass_recorder
    _bypass_recorder = None


def require_bypass_recorder() -> BypassRecorder:
    """The registered recorder, or a loud failure when the seam is unwired."""
    if _bypass_recorder is None:
        raise RuntimeError(
            "RLS bypass recorder is not registered. Import src.app.modules."
            "audit_logs (or src.app.models) before bypassing RLS."
        )
    return _bypass_recorder


def ensure_bypass_recorder_registered() -> None:
    """Boot gate: raise when the seam is unwired. Call once at startup.

    This one matters more than the others. A missing recorder would not fail
    loudly at the bypass — ``_record_bypass_audit`` swallows exceptions by
    design, so a job would proceed with a silently unaudited bypass. The gate
    turns that into a startup failure.
    """
    if _bypass_recorder is None:
        logger.error("bypass_recorder_unwired")
    require_bypass_recorder()


__all__ = [
    "BypassRecorder",
    "ensure_bypass_recorder_registered",
    "register_bypass_recorder",
    "require_bypass_recorder",
    "reset_bypass_recorder",
]
