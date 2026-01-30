import asyncio
import os
import sys

# Add src to path
sys.path.append(os.getcwd())

from src.app.core.db.database import async_session_maker
from src.app.modules.tiers.crud import crud_tiers
from src.app.modules.tiers.schemas import TierCreateInternal
from src.app.modules.users.crud import crud_users


async def upgrade_perf_user():
    async with async_session_maker() as db:
        user = await crud_users.get_by_email(db, email="perftest@example.com")
        if not user:
            print("User perftest@example.com not found!")
            return

        print(f"User found: {user.id}")

        if user.tier_id:
            tier = await crud_tiers.get(db, id=user.tier_id)
            print(f"Current Tier: {tier.name if tier else 'None'}")
        else:
            print("Current Tier: None")

        # Get or create 'enterprise' tier (10000 req/hr)
        tiers = await crud_tiers.get_multi(db)
        enterprise_tier = next((t for t in tiers["data"] if t["name"] == "enterprise"), None)

        if not enterprise_tier:
            print("Creating Enterprise tier...")
            enterprise_tier = await crud_tiers.create(
                db,
                object=TierCreateInternal(
                    name="enterprise", slug="enterprise", description="Unlimited Power", is_public=True
                ),
            )

        print(f"Setting user to Enterprise tier (ID: {enterprise_tier['id']})...")
        await crud_users.update(db, object={"tier_id": enterprise_tier["id"]}, id=user.id)
        await db.commit()
        print("Done!")


if __name__ == "__main__":
    asyncio.run(upgrade_perf_user())
