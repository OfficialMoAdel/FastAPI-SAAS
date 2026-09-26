"""The worker's feature task batch, supplied by the application seam.

``WorkerSettings.functions`` must contain the feature-owned background tasks —
the magic-link sender, the email sender, the two stuck-job recoverers. Naming
them from ``core.worker.settings`` would be a ``core -> feature`` edge, which
the H-5 contract forbids, so they arrive as a registered batch instead.

The batch is one hook rather than four because the decision is one: *which
feature tasks does this worker run?* Splitting it into per-task registrations
would spread one list across four hooks and give a partial worker somewhere to
hide.

This mirrors ``src.app.events``, which supplies the event bus's handler batch
the same way. The difference is import order: the bus is wired by the
composition root, but the ARQ CLI imports ``WorkerSettings`` by dotted path
with no chance to call in first. So ``core.worker.settings`` imports
``src.app.worker`` for the side effect, exactly as it already does for
``src.app.models`` — a core -> app edge, which the contract permits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from src.app.core.config import EnvironmentOption, settings
from src.app.core.logger import get_logger

logger = get_logger(__name__)

@dataclass(frozen=True, slots=True)
class FeatureTask:
    """One feature background task: what to run, and on what schedule.

    Both halves live here because naming a feature task twice — once for
    ``functions`` and again for ``cron_jobs`` — is how the two lists drift.
    A task with no ``schedule`` runs on demand only.
    """

    fn: Any
    schedule: dict[str, Any] = field(default_factory=dict)


_feature_tasks: tuple[FeatureTask, ...] = ()


def register_feature_tasks(tasks: Sequence[FeatureTask]) -> None:
    """Register the feature background tasks the worker should run.

    Called once, by ``src.app.worker``, at import time.
    """
    global _feature_tasks
    _feature_tasks = tuple(tasks)


def reset_feature_tasks() -> None:
    """Reset the registered batch (test isolation).

    TEST-ONLY, blocked in production: an empty batch would start a worker that
    silently never runs the feature jobs.
    """
    if settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION:
        raise RuntimeError("reset_feature_tasks is test-only and blocked in production")
    global _feature_tasks
    _feature_tasks = ()


def registered_feature_tasks() -> tuple[FeatureTask, ...]:
    """The registered feature tasks. Empty when nothing has registered.

    Deliberately not a raise: ``core.worker.settings`` reads this at import
    time, and arq may import it in a context where the application seam was
    never loaded. A missing batch is caught loudly at boot by
    :func:`ensure_feature_tasks_registered` instead of at import.
    """
    return _feature_tasks


def ensure_feature_tasks_registered() -> None:
    """Boot gate: raise when the batch is empty. Call once at worker startup.

    An empty batch is not a harmless no-op — the worker would start and never
    run magic-link delivery, email sending, or the stuck-job recoverers, with
    nothing in the logs to say why. The gate turns that into a startup failure.
    """
    if not _feature_tasks:
        logger.error("feature_tasks_unwired")
        raise RuntimeError(
            "no feature background tasks registered. The worker would start "
            "without magic-link delivery, email sending, or the stuck-job "
            "recoverers. Import src.app.worker before starting the worker."
        )


@dataclass(frozen=True, slots=True)
class FeatureCollaborators:
    """The feature-owned helpers that core worker tasks call.

    Distinct from :class:`FeatureTask`, which is *what the worker runs*.
    These are the three unrelated feature operations three unrelated tasks
    happen to need, so they travel as one record rather than three separate
    hooks — for the same reason the task batch is one: a partially-wired
    worker should not be a representable state.
    """

    deliver: Any
    flush_audit_log_buffer: Any
    audit_log_max_flush_per_run: int
    release_webhook_event_lock: Any


_feature_collaborators: FeatureCollaborators | None = None


def register_feature_collaborators(collaborators: FeatureCollaborators) -> None:
    """Register the feature helpers core worker tasks call."""
    global _feature_collaborators
    _feature_collaborators = collaborators


def require_feature_collaborators() -> FeatureCollaborators:
    """The registered collaborators, or a loud failure when unwired."""
    if _feature_collaborators is None:
        raise RuntimeError(
            "worker feature collaborators are not registered. Import "
            "src.app.worker before running worker tasks."
        )
    return _feature_collaborators


def ensure_feature_collaborators_registered() -> None:
    """Boot gate: raise when unwired. Call once at worker startup."""
    if _feature_collaborators is None:
        logger.error("feature_collaborators_unwired")
    require_feature_collaborators()


__all__ = [
    "FeatureCollaborators",
    "FeatureTask",
    "ensure_feature_tasks_registered",
    "register_feature_tasks",
    "registered_feature_tasks",
    "reset_feature_tasks",
]
