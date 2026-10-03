"""Composition root for the ARQ worker's feature tasks.

Mirrors ``src.app.events``, which supplies the event bus its handler batch. The
worker needs the same thing for a different reason: ``WorkerSettings.functions``
must contain the feature-owned background tasks, and ``core.worker.settings``
may not import them (H-5).

This module is that supply point. It is imported for its side effect by
``core.worker.settings``, because the ARQ CLI loads ``WorkerSettings`` by
dotted path and offers no opportunity to call in before it is evaluated.
"""

from __future__ import annotations

# S-36: configure logging FIRST, before any handler module is imported.
#
# Every handler module binds `logger = get_logger(__name__)` at module import,
# and `setup_logging()` configures structlog with `cache_logger_on_first_use=
# True`. Configuring logging any later — including from the worker's `startup()`
# hook — leaves those already-bound loggers pointing at the DEFAULT processor
# chain, which has no `SanitizingProcessor`. The worker therefore logged
# unredacted tokens/emails/DSNs while the web process redacted them.
#
# The ARQ CLI imports this module before it loads the worker, so module import
# is the only hook early enough.
from src.app.core.logger import setup_logging

setup_logging()

from src.app.core.worker.registry import (  # noqa: E402
    FeatureCollaborators,
    FeatureTask,
    register_feature_collaborators,
    register_feature_tasks,
)
from src.app.modules.audit_logs.service import (  # noqa: E402
    AUDIT_LOG_MAX_FLUSH_PER_RUN,
    flush_audit_log_buffer,
)
from src.app.modules.auth.tasks import send_magic_link_email_task
from src.app.modules.billing.webhook.idempotency import _release_webhook_event_lock
from src.app.modules.notifications.service import (
    recover_stuck_notifications,
    send_email_task,
)
from src.app.modules.webhooks.delivery import deliver, recover_stuck_deliveries

# The feature background tasks this worker runs. Adding one means adding it
# here; the batch is registered as a unit because the decision is one — "which
# feature tasks does this worker run?" — and four separate hooks would give a
# partially-wired worker somewhere to hide.
_EVERY_FIVE_MINUTES = {"minute": set(range(0, 60, 5))}

register_feature_tasks(
    [
        FeatureTask(send_magic_link_email_task),
        FeatureTask(send_email_task),
        FeatureTask(recover_stuck_notifications, _EVERY_FIVE_MINUTES),
        FeatureTask(recover_stuck_deliveries, _EVERY_FIVE_MINUTES),
    ]
)


# The feature-owned helpers that core worker tasks call. Registered here for
# the same reason as the task batch: core.worker.functions may not name them
# (H-5), and the worker has no opportunity to call in before its module is
# imported by the ARQ CLI.
register_feature_collaborators(
    FeatureCollaborators(
        deliver=deliver,
        flush_audit_log_buffer=flush_audit_log_buffer,
        audit_log_max_flush_per_run=AUDIT_LOG_MAX_FLUSH_PER_RUN,
        release_webhook_event_lock=_release_webhook_event_lock,
    )
)
