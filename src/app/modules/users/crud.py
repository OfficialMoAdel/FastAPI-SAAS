from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db.tenant_crud import TenantAwareCRUD
from .models import User
from .schemas import UserCreateInternal, UserDelete, UserRead, UserUpdate, UserUpdateInternal

class CRUDUser(TenantAwareCRUD[User, UserCreateInternal, UserUpdate, UserUpdateInternal, UserDelete, UserRead]):
    async def get_user_with_tier_eager(self, db: AsyncSession, username: str) -> dict[str, Any] | None:
        """Get user with tier in single query using LEFT JOIN."""
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload
        from ..tiers.models import Tier

        stmt = (
            select(User)
            .options(joinedload(User.tier))
            .where(User.username == username, User.is_deleted == False)
        )
        result = await db.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            return None

        # Manually construct dict to avoid recursion issues with Pydantic models vs SQLAlchemy objects
        user_dict = {c.key: getattr(user, c.key) for c in User.__table__.columns}
        user_dict["tier"] = user.tier.__dict__ if user.tier else None

        return user_dict

crud_users = CRUDUser(User)
