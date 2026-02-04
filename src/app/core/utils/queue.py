from arq.connections import ArqRedis

pool: ArqRedis | None = None


async def get_queue_size() -> int:
    """Get current ARQ queue size.

    Returns:
        Number of jobs in the queue
    """
    if pool is None:
        return 0
    return await pool.zcard("arq:queue")
