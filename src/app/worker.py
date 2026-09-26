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

from src.app.core.worker.registry import FeatureTask, register_feature_tasks
from src.app.modules.auth.tasks import send_magic_link_email_task
from src.app.modules.notifications.service import (
    recover_stuck_notifications,
    send_email_task,
)
from src.app.modules.webhooks.delivery import recover_stuck_deliveries

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
