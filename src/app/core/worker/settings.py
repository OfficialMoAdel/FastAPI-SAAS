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

    # Cron jobs for periodic tasks
    cron_jobs = [
        # Flush audit logs every 30 seconds for low-latency batching
        cron(flush_audit_logs_task, second={0, 30}),
        # Sync Bloom filter every 5 minutes for multi-instance consistency
        cron(sync_bloom_filter_task, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
    ]

    # Concurrency and timeout settings (P1-3 optimization)
    # max_jobs=10: DB_POOL_SIZE is 20, leave 10 for web requests
    # job_timeout=300: 5 minutes is generous for most jobs
    # keep_result=3600: 1 hour retention prevents memory leak
    max_jobs = 10
    job_timeout = 300
    keep_result = 3600

    # Use orjson for job serialization (faster, safer than pickle)
    job_serializer = staticmethod(lambda obj: json_dumps(obj))
    job_deserializer = staticmethod(lambda data: json_loads(data))
