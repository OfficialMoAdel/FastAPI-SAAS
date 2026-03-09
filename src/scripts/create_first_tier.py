import asyncio
import logging
import os

from sqlalchemy import select

from src.app.core.logger import get_logger

from ..app.core.db.database import AsyncSession, local_session
from ..app.modules.tiers.models import Tier

logging.basicConfig(level=logging.INFO)
logger = get_logger(__name__)


async def create_first_tier(session: AsyncSession) -> None:
    try:
        tier_name = os.getenv("TIER_NAME", "free")

        query = select(Tier).where(Tier.name == tier_name)
        result = await session.execute(query)
        tier = result.scalar_one_or_none()

        if tier is None:
            session.add(Tier(name=tier_name))
            await session.commit()
            logger.info("Tier '' created successfully.", tier_name=tier_name)

        else:
            logger.info("Tier '' already exists.", tier_name=tier_name)

    except Exception as e:
        logger.error("Error creating tier", error=e)


async def main():
    async with local_session() as session:
        await create_first_tier(session)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
