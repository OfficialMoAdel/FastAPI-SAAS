from arq.connections import RedisSettings
from arq.cron import cron

from ...core.config import settings
from ...core.json_utils import json_dumps, json_loads
from .functions import (
    deliver_webhook_task,
    flush_audit_logs_task,
    migrate_sessions_task,
    sample_background_task,
    shutdown,
    startup,
    sync_bloom_filter_task,
)

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
        sync_bloom_filter_task,
    ]
    redis_settings = RedisSettings(host=REDIS_QUEUE_HOST, port=REDIS_QUEUE_PORT)
    on_startup = startup
    on_shutdown = shutdown
    handle_signals = False

    # Cron jobs for periodic tasks (with job-specific timeouts where needed)
    cron_jobs = [
        # Flush audit logs every 30 seconds for low-latency batching (60s timeout for high-volume flushes)
        cron(flush_audit_logs_task, second={0, 30}, run_at_startup=True, timeout=60),
        # Sync Bloom filter every 5 minutes for multi-instance consistency (30s timeout)
        cron(sync_bloom_filter_task, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}, timeout=30),
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
