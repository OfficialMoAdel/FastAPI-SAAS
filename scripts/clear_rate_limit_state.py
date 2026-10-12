import asyncio
import os

from dotenv import load_dotenv

load_dotenv("src/.env", override=True)

import redis.asyncio as aioredis  # noqa: E402


async def main() -> None:
    """Clear rate-limit and ban state so a fresh load run is not self-banned.

    The auth budget is keyed per client IP, and a load generator sends every
    request from one address. Ten violations trips AUTO_BAN, after which the app
    answers 403 to the very traffic meant to exercise it. That is the control
    working as designed; the run has to start from a clean slate.
    """
    password = (
        os.environ.get("REDIS_PASSWORD")
        or os.environ.get("CRUD_ADMIN_REDIS_PASSWORD")
        or ""
    )
    client = aioredis.Redis(
        host=os.environ.get("REDIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        password=password or None,
        decode_responses=True,
    )
    print("keys before      :", await client.dbsize())
    await client.flushall()
    print("keys after flush :", await client.dbsize())
    await client.aclose()


asyncio.run(main())
