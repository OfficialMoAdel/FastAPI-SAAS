from arq.connections import RedisSettings
from arq.cron import cron

from src.app.core.config import settings
from src.app.core.events import process_event_handler
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.worker.functions import (
    deliver_webhook_task,
    flush_audit_logs_task,
    migrate_sessions_task,
    sample_background_task,
    shutdown,
    startup,
)
from src.app.modules.notifications.service import send_email_task
from src.app.modules.webhooks.recovery import recover_stuck_deliveries

REDIS_QUEUE_HOST = settings.REDIS_QUEUE_HOST
REDIS_QUEUE_PORT = settings.REDIS_QUEUE_PORT


class WorkerSettings:
    """ARQ worker settings with orjson serialization.

    Uses orjson instead of pickle for:
    - 2-5x faster serialization
    - Smaller payload sizes
    - Eliminates pickle security vulnerabilities

    Scaling recommendations:
    - Low load: 1 worker, max_jobs=10
    - Medium load: 2-3 workers, max_jobs=10 each
    - High load: 4-5 workers, max_jobs=10 each
    """

    functions = [
        sample_background_task,
        deliver_webhook_task,
        migrate_sessions_task,
        flush_audit_logs_task,
        process_event_handler,  # FIXED: Was missing - handles async event bus events
        send_email_task,  # FIXED: Was missing - handles email notifications
    ]
    redis_settings = RedisSettings(
        host=settings.REDIS_QUEUE_HOST,
        port=settings.REDIS_QUEUE_PORT,
        password=settings.REDIS_QUEUE_PASSWORD.get_secret_value() if settings.REDIS_QUEUE_PASSWORD else None,
    )
    on_startup = startup
    on_shutdown = shutdown
    handle_signals = False

    # Cron jobs for periodic tasks (with job-specific timeouts where needed)
    cron_jobs = [
        # Flush audit logs every 30 seconds for low-latency batching (60s timeout for high-volume flushes)
        cron(flush_audit_logs_task, second={0, 30}, run_at_startup=True, timeout=60),
        # Recover stuck webhook deliveries every 5 minutes
        cron(recover_stuck_deliveries, minute=set(range(0, 60, 5))),
    ]

    # Concurrency and timeout settings (configurable via environment)
    # max_jobs: DB_POOL_SIZE is workers × 10, leave 50% for web requests
    # job_timeout: 5 minutes default, override per-job as needed
    # keep_result: 1 hour retention prevents memory leak
    # max_tries: Retry failed jobs 3 times (exponential backoff: 10s, 60s, 360s)
    # queue_max_len: Backpressure to prevent memory issues
    max_jobs = settings.ARQ_MAX_JOBS
    job_timeout = settings.ARQ_JOB_TIMEOUT
    keep_result = settings.ARQ_KEEP_RESULT
    max_tries = settings.ARQ_MAX_TRIES
    queue_max_len = settings.ARQ_QUEUE_MAX_SIZE

    # Use orjson for job serialization (faster, safer than pickle)
    job_serializer = staticmethod(lambda obj: json_dumps(obj))
    job_deserializer = staticmethod(lambda data: json_loads(data))
