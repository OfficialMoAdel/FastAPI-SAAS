from arq.connections import ArqRedis

pool: ArqRedis | None = None


async def get_queue_size() -> int:
    """Get current ARQ queue size.

    Returns:
        Number of jobs in the queue
    """
    if pool is None:
        return 0
    # M1: Use a pipeline to get sizes for both queues in one roundtrip
    async with pool.pipeline() as pipe:
        pipe.zcard("arq:queue")
        pipe.zcard("arq:queue_low_priority")
        results = await pipe.execute()
    return int(results[0] or 0) + int(results[1] or 0)
