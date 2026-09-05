"""ARQ wire protocol adapter (Architecture Candidate 5).

Single source of truth for ARQ wire format between web and worker runtimes:
- Job serialization and deserialization (guaranteeing K1 pickle-vs-json mismatch cannot recur)
- Canonical queue naming for single and batch job dispatch
- Batch pipeline enqueue logic (psetex + zadd with 24h expiration)
- Standard ArqRedis connection kwargs
"""

from typing import Any

from arq.connections import ArqRedis

from src.app.core.events.priority import EventPriority
from src.app.core.json_utils import json_dumps, json_loads

# Wire TTL: 24 hours in milliseconds (matches P2-2 regression requirement)
JOB_EXPIRES_MS = 86_400_000


def serialize_job(obj: Any) -> bytes:
    """Serialize job arguments using orjson (safe, fast, consistent across web + worker)."""
    return json_dumps(obj)  # type: ignore[return-value]


def deserialize_job(data: bytes | str | memoryview) -> Any:
    """Deserialize job arguments using orjson."""
    return json_loads(data)


def get_queue_name(priority: EventPriority = EventPriority.NORMAL) -> str:
    """Return the canonical ARQ queue name for a given priority.

    Single formula for both single-job and batch-job dispatch.
    """
    import src.app.core.events as _pkg

    base_name = _pkg.default_queue_name
    if priority == EventPriority.NORMAL:
        return base_name
    return f"{base_name}_low_priority"


def get_arq_pool_kwargs() -> dict[str, Any]:
    """Return standard kwargs for ArqRedis connection pool initialization."""
    return {
        "job_serializer": serialize_job,
        "job_deserializer": deserialize_job,
    }


async def enqueue_batch_jobs(
    arq_queue: ArqRedis,
    event_name: str,
    handlers: list[Any],
    data: dict[str, Any],
    priority: EventPriority,
) -> None:
    """Batch-enqueue multiple event handlers into ARQ in a single Redis round-trip."""
    from uuid import uuid4

    import src.app.core.events as _pkg

    queue_name = get_queue_name(priority)
    enqueue_time_ms = _pkg.timestamp_ms()

    async with arq_queue.pipeline() as pipe:
        for handler in handlers:
            job_id = uuid4().hex
            job_key = _pkg.job_key_prefix + job_id
            job_data = _pkg.serialize_job(
                function_name="process_event_handler",
                args=(event_name, handler.__module__, handler.__name__, data),
                kwargs={},
                job_try=None,
                enqueue_time_ms=enqueue_time_ms,
                serializer=arq_queue.job_serializer,
            )
            pipe.psetex(job_key, JOB_EXPIRES_MS, job_data)
            pipe.zadd(queue_name, {job_id: enqueue_time_ms})
        await pipe.execute()


__all__ = [
    "JOB_EXPIRES_MS",
    "deserialize_job",
    "enqueue_batch_jobs",
    "get_arq_pool_kwargs",
    "get_queue_name",
    "serialize_job",
]
