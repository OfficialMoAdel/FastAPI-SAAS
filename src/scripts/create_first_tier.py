import asyncio
import logging
import os

from sqlalchemy import select

from ..app.core.db.database import AsyncSession, local_session
from ..app.modules.tiers.models import Tier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def create_first_tier(session: AsyncSession) -> None:
    try:
        tier_name = os.getenv("TIER_NAME", "free")

        query = select(Tier).where(Tier.name == tier_name)
        result = await session.execute(query)
        tier = result.scalar_one_or_none()

        if tier is None:
            session.add(Tier(name=tier_name))
            await session.commit()
            logger.info(f"Tier '{tier_name}' created successfully.")

        else:
            logger.info(f"Tier '{tier_name}' already exists.")

    except Exception as e:
        logger.error(f"Error creating tier: {e}")


async def main():
    async with local_session() as session:
        await create_first_tier(session)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
