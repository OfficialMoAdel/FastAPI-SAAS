import asyncio
from arq.connections import ArqRedis
from redis.asyncio import Redis


async def inspect_arq():
    # Fix initialization: ArqRedis expects arguments to create its own pool or wrapping
    # But for inspection we don't need a live connection really, but let's try to init correctly
    arq = ArqRedis(host="localhost", port=6379)

    print(f"ArqRedis methods: {dir(arq)}")

    # Check for pipeline
    if hasattr(arq, "pipeline"):
        print("ArqRedis HAS pipeline support!")
    else:
        print("ArqRedis does NOT have pipeline support.")

    # Check for enqueue_jobs
    if hasattr(arq, "enqueue_jobs"):
        print("ArqRedis HAS enqueue_jobs support!")
    else:
        print("ArqRedis does NOT have enqueue_jobs support.")

    await arq.close()


if __name__ == "__main__":
    asyncio.run(inspect_arq())
