from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db.tenant_crud import TenantAwareCRUD
from .models import User
from .schemas import UserCreateInternal, UserDelete, UserRead, UserUpdate, UserUpdateInternal


class CRUDUser(TenantAwareCRUD[User, UserCreateInternal, UserUpdate, UserUpdateInternal, UserDelete, UserRead]):
    async def get_user_with_tier_eager(self, db: AsyncSession, username: str) -> dict[str, Any] | None:
        """Get user with tier in single query using LEFT JOIN.
        
        Security: Respects tenant isolation via tenant context.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

        from ...core.tenancy.context import get_tenant_context

        tenant = get_tenant_context()

        stmt = (
            select(User)
            .options(joinedload(User.tier))
            .where(
                User.username == username,
                User.is_deleted == False,
                User.tenant_id == tenant.id if tenant else True,
            )
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

