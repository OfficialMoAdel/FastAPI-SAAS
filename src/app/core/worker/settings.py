"""ARQ worker settings (composition root).

See WorkerSettings docstring below for runtime tuning; this module header
documents the two composition-root duties this file owns (A-M3):

1. **Model registration**: ``import src.app.models`` is the single point that
   registers every SQLAlchemy model (A-M6). The worker runs jobs that query
   tenant-scoped tables before any web request has touched the registry, so
   it must complete the mapper registry itself — an unresolved forward
   reference would raise ``InvalidRequestError`` on the first job query.

2. **Function aggregation**: ``_discover_module_registrations`` collects each
   module's optional ``ARQ_FUNCTIONS`` / ``ARQ_CRONJOBS`` lists. Any module
   that defines ARQ task functions or cron jobs declares them there, and the
   worker registry picks them up automatically — no manual editing of this
   file per new task (the manual-registry miss was the B8 bug class:
   enqueued-but-never-registered). The explicit core list below stays as the
   always-present fallback; a module list is merged in addition to it, and
   duplicates (a name already registered) are logged and skipped so a module
   re-exporting a core function cannot shadow it.
"""

import importlib
import pkgutil

from dotenv import load_dotenv

load_dotenv()

from arq.connections import RedisSettings
from arq.cron import cron

from src.app.core.config import settings
from src.app.core.events import process_event_handler
from src.app.core.events.arq_adapter import deserialize_job, serialize_job
from src.app.core.logger import get_logger
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
from src.app.modules.notifications.service import send_email_task
from src.app.modules.webhooks.delivery import recover_stuck_deliveries

logger = get_logger(__name__)

REDIS_QUEUE_HOST = settings.redis_queue.REDIS_QUEUE_HOST
REDIS_QUEUE_PORT = settings.redis_queue.REDIS_QUEUE_PORT


def _discover_module_registrations() -> tuple[list, list]:
    """Aggregate per-module ``ARQ_FUNCTIONS``/``ARQ_CRONJOBS`` declarations.

    Each ``src.app.modules.*`` package MAY define, at its package root
    (``<module>/__init__.py``), two optional module-level lists:

    - ``ARQ_FUNCTIONS``: callables to register as worker functions
    - ``ARQ_CRONJOBS``: ``arq.cron.cron`` instances to schedule

    A module without either list contributes nothing (import errors other
    than a missing list are surfaced, not swallowed). The return is a pair
    ``(functions, cron_jobs)`` suitable for merging into the fallback
    registry below; duplicate function names are dropped by the caller.
    """
    functions: list = []
    cron_jobs: list = []
    import src.app.modules as modules_pkg

    for mod_info in pkgutil.iter_modules(modules_pkg.__path__):
        module_name = f"src.app.modules.{mod_info.name}"
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            # Import failure in a module package is a real defect; the old
            # tenants-models loop silently swallowed these. Log and continue
            # so one broken module cannot take the worker down, but leave
            # the trail.
            logger.warning("arq_registration_import_failed", module=module_name, error=str(e))
            continue
        for attr, bucket in (("ARQ_FUNCTIONS", functions), ("ARQ_CRONJOBS", cron_jobs)):
            declared = getattr(module, attr, None)
            if not declared:
                continue
            if not isinstance(declared, list):
                logger.warning("arq_registration_ignored_not_a_list", module=module_name, attr=attr)
                continue
            bucket.extend(declared)
    return functions, cron_jobs


_MODULE_FUNCTIONS, _MODULE_CRONJOBS = _discover_module_registrations()


def _merge_module_functions(core_functions: list) -> list:
    """Append module-declared functions to the core registry, skipping duplicates.

    A module re-exporting a core function must not duplicate it in the
    registration list; the duplicate is logged and dropped.
    """
    registered = {getattr(fn, "__name__", str(fn)) for fn in core_functions}
    merged = list(core_functions)
    for fn in _MODULE_FUNCTIONS:
        name = getattr(fn, "__name__", str(fn))
        if name in registered:
            logger.warning("arq_function_duplicate_skipped", function=name)
            continue
        merged.append(fn)
        registered.add(name)
    return merged


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

    # Core worker registry (always present) + module-declared functions
    # (ARQ_FUNCTIONS in any src.app.modules.* package __init__, merged by
    # _merge_module_functions — see the module docstring for the contract).
    # The explicit core list stays as the fallback registry: it is the
    # guaranteed registration set even if a module package fails to import
    # during discovery.
    functions = _merge_module_functions(
        [
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
        ]
    )
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
        # Cleanup expired tokens from blacklist ZSET every hour
        cron(cleanup_expired_tokens_task, minute=0, timeout=30),
    ] + _MODULE_CRONJOBS

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
