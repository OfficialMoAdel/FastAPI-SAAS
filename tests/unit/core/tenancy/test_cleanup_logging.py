"""Tenancy cleanup paths must log a failure, not raise one.

`unbind_tenant` and `bound_tenant` exist so that teardown can never crash a
worker over cleanup. Both had `logger.warning(event, error=str(e))` against a
**stdlib** `logging.Logger`, whose `_log()` takes no `error` keyword. Every
time the wrapped call actually failed, the handler raised
`TypeError: Logger._log() got an unexpected keyword argument 'error'` instead
of logging — so the "never raising" guarantee was false on exactly the path
it existed to protect, and in `bound_tenant` it fires from a `finally`, where
the TypeError masks whatever exception was already propagating.

These tests pin the behaviour, not the logger type: the assertion is that the
cleanup call returns normally and the failure is reported.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.app.core.tenancy import lifecycle
from src.app.core.tenancy.lifecycle import bound_tenant, unbind_tenant


def _boom(*_args: object, **_kw: object) -> None:
    raise RuntimeError("cleanup exploded")


def test_unbind_tenant_logs_instead_of_raising() -> None:
    """A failing clear must be swallowed and reported, not re-raised."""
    with patch.object(lifecycle, "clear_tenant_context", side_effect=_boom):
        unbind_tenant()  # must not raise


def test_bound_tenant_restore_failure_does_not_mask_the_body() -> None:
    """A failing restore must not replace the exception the body raised.

    This is the sharp edge: the restore runs from a `finally`, so a secondary
    failure there replaces the primary one and the caller loses the real
    cause. Only the exit-path restore is broken here — `bind_tenant` is
    stubbed because it also calls `set_current_tenant`, and failing that would
    abort the scope before the body ever runs.
    """
    with pytest.raises(ValueError, match="original failure"):
        with (
            patch.object(lifecycle, "bind_tenant", lambda _tenant: None),
            patch.object(lifecycle, "set_current_tenant", side_effect=_boom),
        ):
            with bound_tenant(None):
                raise ValueError("original failure")


def test_bound_tenant_yields_and_restores() -> None:
    """The happy path still works after the logger change."""
    with bound_tenant(None) as tenant:
        assert tenant is None


def test_structured_logging_accepts_extra_keys() -> None:
    """The event payload still carries the error text.

    The fix moved these call sites onto the project's structlog logger, which
    is what accepts `error=` as structured context. Assert the context
    survives rather than only asserting nothing raised.
    """
    seen: list[dict] = []

    class _Capture:
        def warning(self, event: str, **kw: object) -> None:
            seen.append({"event": event, **kw})

    with patch.object(lifecycle, "clear_tenant_context", side_effect=_boom):
        with patch.object(lifecycle, "logger", _Capture()):
            unbind_tenant()

    assert len(seen) == 1, "the failure was not reported"
    assert seen[0]["event"] == "tenant_context_clear_failed"
    assert "exploded" in str(seen[0]["error"])
