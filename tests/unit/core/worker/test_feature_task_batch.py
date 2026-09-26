"""The worker feature-task batch: the registry owns what runs, and when.

``WorkerSettings.functions`` must carry the feature-owned background tasks, and
``core.worker.settings`` may not import them (H-5), so they arrive as a
registered batch from ``src.app.worker``.

Two things are pinned here. The batch is the *only* place a feature task is
named — it supplies both ``functions`` and ``cron_jobs``, because naming a task
once for the function list and again for the schedule is how the two lists
drift. And an empty batch fails at worker startup, because a worker that
starts without them is silent: no magic links, no email, no stuck-job
recovery, and nothing in the logs to say why.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.app.core.worker import registry as worker_registry


@pytest.fixture
def unregistered():
    saved = worker_registry._feature_tasks
    worker_registry.reset_feature_tasks()
    yield
    worker_registry._feature_tasks = saved


def test_require_style_access_is_empty_not_a_raise(unregistered):
    """Reading the batch at import time must not explode.

    arq may import ``WorkerSettings`` without the application seam loaded, so
    the accessor is a plain read. The failure belongs at startup, not import.
    """
    assert worker_registry.registered_feature_tasks() == ()


def test_boot_gate_raises_on_an_empty_batch(unregistered):
    """A silently under-wired worker is worse than one that refuses to start."""
    with pytest.raises(RuntimeError, match="no feature background tasks registered"):
        worker_registry.ensure_feature_tasks_registered()


def test_application_root_registers_the_batch():
    import src.app.worker  # noqa: F401 — the import is the registration

    batch = worker_registry.registered_feature_tasks()
    assert len(batch) == 4
    worker_registry.ensure_feature_tasks_registered()


def test_batch_supplies_both_the_functions_and_the_schedules():
    """One batch, two lists: no feature task is named twice."""
    from src.app.core.worker.settings import WorkerSettings

    batch = worker_registry.registered_feature_tasks()
    registered = {getattr(t.fn, "__name__", str(t.fn)) for t in batch}
    on_worker = {getattr(f, "__name__", str(f)) for f in WorkerSettings.functions}
    assert registered <= on_worker, "a registered feature task is not on the worker"

    scheduled = {getattr(t.fn, "__name__", str(t.fn)) for t in batch if t.schedule}
    crons = {getattr(getattr(c, "coroutine", None), "__name__", "?") for c in WorkerSettings.cron_jobs}
    assert scheduled == crons & scheduled, "a scheduled feature task is missing from cron_jobs"


def test_the_worker_registry_is_unchanged_in_size_and_names():
    """Guards against a batch edit silently dropping or duplicating a task."""
    from src.app.core.worker.settings import WorkerSettings

    names = sorted(getattr(f, "__name__", str(f)) for f in WorkerSettings.functions)
    assert names == sorted(
        [
            "cleanup_expired_tokens_task",
            "cleanup_leaked_webhook_lock",
            "deliver_webhook_task",
            "flush_audit_logs_task",
            "migrate_sessions_task",
            "process_event_handler",
            "recover_stuck_deliveries",
            "recover_stuck_notifications",
            "sample_background_task",
            "send_email_task",
            "send_magic_link_email_task",
        ]
    )
    assert len(WorkerSettings.cron_jobs) == 4


def test_reset_is_blocked_in_production(monkeypatch):
    from src.app.core.config import EnvironmentOption

    monkeypatch.setattr(worker_registry.settings.env, "ENVIRONMENT", EnvironmentOption.PRODUCTION)
    with pytest.raises(RuntimeError, match="test-only and blocked in production"):
        worker_registry.reset_feature_tasks()


def test_core_worker_settings_does_not_import_feature_tasks():
    """The three edges this batch replaces must not creep back."""
    path = Path("src/app/core/worker/settings.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(
                (
                    "src.app.modules.auth.tasks",
                    "src.app.modules.notifications.service",
                    "src.app.modules.webhooks.delivery",
                )
            ), f"settings.py imports {node.module} directly; the batch comes from src.app.worker"
