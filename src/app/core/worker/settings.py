from arq.connections import RedisSettings

from ...core.config import settings
from ...core.json_utils import json_dumps, json_loads
from .functions import deliver_webhook_task, migrate_sessions_task, sample_background_task, shutdown, startup

REDIS_QUEUE_HOST = settings.REDIS_QUEUE_HOST
REDIS_QUEUE_PORT = settings.REDIS_QUEUE_PORT


class WorkerSettings:
    """ARQ worker settings with orjson serialization.

    Uses orjson instead of pickle for:
    - 2-5x faster serialization
    - Smaller payload sizes
    - Eliminates pickle security vulnerabilities
    """

    functions = [sample_background_task, deliver_webhook_task, migrate_sessions_task]
    redis_settings = RedisSettings(host=REDIS_QUEUE_HOST, port=REDIS_QUEUE_PORT)
    on_startup = startup
    on_shutdown = shutdown
    handle_signals = False

    # Use orjson for job serialization (faster, safer than pickle)
    job_serializer = staticmethod(lambda obj: json_dumps(obj))
    job_deserializer = staticmethod(lambda data: json_loads(data))
