"""
Tier Factory

Creates Tier model instances for testing.
"""

from tests.factories.base import AsyncFactory, random_string
from src.app.modules.tiers.models import Tier


class TierFactory(AsyncFactory):
    """Factory for creating Tier test instances.

    Usage:
        tier = await TierFactory.create_async(db=session)
        tier = await TierFactory.create_async(db=session, name="Enterprise")
    """

    model = Tier
    defaults = {
        "name": lambda: f"Tier_{random_string(length=6)}",
    }

    @classmethod
    async def create_default_tiers(cls, db) -> dict[str, Tier]:
        """Create the standard tier set (Free, Pro, Enterprise).

        Returns:
            Dict with tier names as keys and Tier instances as values
        """
        tiers = {}
        for name in ["Free", "Pro", "Enterprise"]:
            tier = await cls.create_async(db, name=name)
            tiers[name] = tier
        return tiers
