"""
RBAC Seeding Script.

Synchronizes the PermissionEnum defined in code with the database 'permissions' table.
Also sets up initial system roles.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from sqlalchemy import select

from src.app.core.db.database import local_session
from src.app.core.rbac.permissions import Permission as PermissionEnum
from src.app.modules.rbac.models import Permission


async def seed_permissions() -> None:
    """Seed permissions from PermissionEnum."""
    print("Seeding permissions...")
    async with local_session() as db:
        # Get existing permissions
        result = await db.execute(select(Permission))
        existing_permissions = {p.slug for p in result.scalars().all()}

        count = 0
        for perm in PermissionEnum:
            if perm.value not in existing_permissions:
                new_perm = Permission(
                    slug=perm.value,
                    description=perm.name.replace("_", " ").title()
                )
                db.add(new_perm)
                count += 1

        if count > 0:
            await db.commit()
            print(f"Added {count} new permissions.")
        else:
            print("All permissions already up to date.")


if __name__ == "__main__":
    try:
        asyncio.run(seed_permissions())
    except Exception as e:
        print(f"Error seeding permissions: {e}")
        sys.exit(1)
