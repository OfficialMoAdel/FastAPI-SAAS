"""ARQ worker settings (composition root).

See WorkerSettings docstring below for runtime tuning; this module header
documents the two composition-root duties this file owns (A-M3):

1. **Model registration**: ``import src.app.models`` is the single point that
   registers every SQLAlchemy model (A-M6). The worker runs jobs that query
   tenant-scoped tables before any web request has touched the registry, so
   it must complete the mapper registry itself — an unresolved forward
   reference would raise ``InvalidRequestError`` on the first job query.

2. **Function registration**: ``WorkerSettings.functions`` below is the single
   explicit registry, and it is the *whole* registry — there is no discovery
   path. A previous per-module ``ARQ_FUNCTIONS``/``ARQ_CRONJOBS`` scan walked
   every ``src.app.modules.*`` package for optional declaration lists; no
   module ever declared one, so the machinery had no adapter and the only
   test of it had to inject a fake to have anything to observe. Adding a task
   means adding it here, and ``functions.assert_all_jobs_registered`` fails
   worker startup when an enqueued name is missing from this list (the B8 bug
   class: enqueued-but-never-registered).
"""

from dotenv import load_dotenv

load_dotenv()

from arq.connections import RedisSettings
from arq.cron import cron

from src.app.core.config import settings
from src.app.core.events import process_event_handler
from src.app.core.events.arq_adapter import deserialize_job, serialize_job
from src.app.core.worker.functions import (
    cleanup_expired_tokens_task,
    cleanup_leaked_webhook_lock,
    deliver_webhook_task,
    flush_audit_logs_task,
    migrate_sessions_task,
    sample_background_task,
    shutdown,
    startup,
)
from src.app.models import *  # noqa: F401,F403 -- single model-registration point (A-M6)
from src.app.modules.auth.tasks import send_magic_link_email_task
from src.app.modules.notifications.service import recover_stuck_notifications, send_email_task
from src.app.modules.webhooks.delivery import recover_stuck_deliveries

REDIS_QUEUE_HOST = settings.redis_queue.REDIS_QUEUE_HOST
REDIS_QUEUE_PORT = settings.redis_queue.REDIS_QUEUE_PORT


class WorkerSettings:
    """ARQ worker settings with orjson serialization.

    Uses orjson instead of pickle for:
    - 2-5x faster serialization
    - Smaller payload sizes
    - Eliminating pickle security vulnerabilities

    Scaling recommendations:
    - Low load: 1 worker, max_jobs=10
    - Medium load: 2-3 workers, max_jobs=10 each
    - High load: 4-5 workers, max_jobs=10 each
    """

    # The one explicit registry. Adding a task means adding it here;
    # assert_all_jobs_registered (core/worker/functions.py) fails worker
    # startup if an enqueued name is missing from this list.
    functions = [
        sample_background_task,
        deliver_webhook_task,
        migrate_sessions_task,
        flush_audit_logs_task,
        process_event_handler,  # FIXED: Was missing - handles async event bus events
        send_email_task,  # FIXED: Was missing - handles email notifications
        send_magic_link_email_task,
        cleanup_leaked_webhook_lock,  # B8: was enqueued but never defined/registered
        cleanup_expired_tokens_task,
        recover_stuck_deliveries,
        recover_stuck_notifications,
    ]
    # NOTE: arq 0.26.x has no multi-queue support — a worker polls exactly one
    # zset (queue_name). EventPriority.LOW events are enqueued to
    # f"{default_queue_name}_low_priority" (see events/bus.py) and require a
    # second worker process with that queue_name to be consumed. Unknown
    # settings-class attributes (e.g. a `queues` list) are silently dropped by
    # arq's get_kwargs, so declaring them here has no effect.
    redis_settings = RedisSettings(
        host=settings.redis_queue.REDIS_QUEUE_HOST,
        port=settings.redis_queue.REDIS_QUEUE_PORT,
        password=(
            settings.redis_queue.REDIS_QUEUE_PASSWORD.get_secret_value()
            if settings.redis_queue.REDIS_QUEUE_PASSWORD
            else None
        ),
    )
    on_startup = startup
    on_shutdown = shutdown
    # K6 FIX: False disabled graceful shutdown - SIGTERM killed workers mid-job,
    # losing popped jobs until key expiry. arq's signal handling defers in-flight
    # jobs and exits cleanly.
    handle_signals = True

    # Cron jobs for periodic tasks (with job-specific timeouts where needed)
    cron_jobs = [
        # Flush audit logs every 30 seconds for low-latency batching (60s timeout for high-volume flushes)
        cron(flush_audit_logs_task, second={0, 30}, run_at_startup=True, timeout=60),
        # Recover stuck webhook deliveries every 5 minutes
        cron(recover_stuck_deliveries, minute=set(range(0, 60, 5))),
        # Sweep stuck PENDING notifications to terminal FAILED every 5 minutes (P2-6)
        cron(recover_stuck_notifications, minute=set(range(0, 60, 5))),
        # Cleanup expired tokens from blacklist ZSET every hour
        cron(cleanup_expired_tokens_task, minute=0, timeout=30),
    ]

    # Concurrency and timeout settings (configurable via environment)
    # max_jobs: DB_POOL_SIZE is workers × 10, leave 50% for web requests
    # job_timeout: 5 minutes default, override per-job as needed
    # keep_result: 1 hour retention prevents memory leak
    # max_tries: Retry failed jobs 3 times (exponential backoff: 10s, 60s, 360s)
    max_jobs = settings.arq.ARQ_MAX_JOBS
    job_timeout = settings.arq.ARQ_JOB_TIMEOUT
    keep_result = settings.arq.ARQ_KEEP_RESULT
    max_tries = settings.arq.ARQ_MAX_TRIES

    job_serializer = staticmethod(serialize_job)
    job_deserializer = staticmethod(deserialize_job)
