"""The feature-collaborators record: what core worker tasks call, from outside.

Three core worker tasks each need one feature operation: the webhook task
needs ``deliver``, the audit-flush task needs ``flush_audit_log_buffer`` and its
per-run cap, and the leaked-lock task needs the release helper. None may be
imported from ``core.worker.functions`` (H-5), so they arrive as one record
from ``src.app.worker``.

One record rather than three hooks, for the same reason the task batch is one:
a partially-wired worker should not be a representable state.

This file also records *why* the imports looked inconsistent when the work
started. ``deliver`` was imported at module level while the other two were
imported inside their task bodies, which reads like one of them was dodging an
import cycle. It was not — ``webhooks.delivery`` reaches no worker code. The
difference was import cost: arq imports ``core.worker.functions`` at worker
boot, so the function-local ones deferred their feature imports out of the
boot path. All three are now uniform.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.app.core.worker import registry as worker_registry


@pytest.fixture
def unregistered():
    saved = worker_registry._feature_collaborators
    worker_registry._feature_collaborators = None
    yield
    worker_registry._feature_collaborators = saved


def test_require_raises_when_unwired(unregistered):
    with pytest.raises(RuntimeError, match="feature collaborators are not registered"):
        worker_registry.require_feature_collaborators()


def test_boot_gate_raises_when_unwired(unregistered):
    """A task reaching for an unregistered helper would fail mid-job instead."""
    with pytest.raises(RuntimeError, match="feature collaborators are not registered"):
        worker_registry.ensure_feature_collaborators_registered()


def test_application_root_registers_all_four_members():
    import src.app.worker  # noqa: F401 — the import is the registration

    collaborators = worker_registry.require_feature_collaborators()
    assert callable(collaborators.deliver)
    assert callable(collaborators.flush_audit_log_buffer)
    assert collaborators.audit_log_max_flush_per_run > 0
    assert callable(collaborators.release_webhook_event_lock)
    worker_registry.ensure_feature_collaborators_registered()


def test_collaborators_are_the_real_feature_callables():
    from src.app.modules.audit_logs.service import (
        AUDIT_LOG_MAX_FLUSH_PER_RUN,
        flush_audit_log_buffer,
    )
    from src.app.modules.billing.webhook.idempotency import _release_webhook_event_lock
    from src.app.modules.webhooks.delivery import deliver

    collaborators = worker_registry.require_feature_collaborators()
    assert collaborators.deliver is deliver
    assert collaborators.flush_audit_log_buffer is flush_audit_log_buffer
    assert collaborators.audit_log_max_flush_per_run == AUDIT_LOG_MAX_FLUSH_PER_RUN
    assert collaborators.release_webhook_event_lock is _release_webhook_event_lock


def test_core_worker_functions_names_no_feature_module():
    """All three edges this record replaces must not creep back.

    The assertion is on the module as a whole, not per-task: the original
    inconsistency — one module-level import and two function-local ones — is
    exactly what would let one of them return.
    """
    path = Path("src/app/core/worker/functions.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("src.app.modules"), (
                f"functions.py imports {node.module}; feature helpers come from "
                "the registered collaborators instead"
            )


def test_no_duplicate_registration_sites():
    """One application module supplies everything the worker needs.

    Two would reintroduce the problem this session fixed: the bypass recorder
    lives in the audit module's own __init__ (it must self-register, because
    job_bypass_context also runs in the web process on the Stripe webhook
    path), while tasks and collaborators are supplied by src.app.worker.
    Anything else registering into core.worker is a new site to justify.
    """
    roots = []
    for path in Path("src/app").rglob("*.py"):
        if "core/worker" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8")
        if "register_feature_tasks" in text or "register_feature_collaborators" in text:
            roots.append(path.as_posix())
    assert roots == ["src/app/worker.py"], f"unexpected registration sites: {sorted(roots)}"
