from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db.tenant_crud import TenantAwareCRUD
from .models import User
from .schemas import UserCreateInternal, UserDelete, UserRead, UserUpdate, UserUpdateInternal


class CRUDUser(TenantAwareCRUD[User, UserCreateInternal, UserUpdate, UserUpdateInternal, UserDelete, UserRead]):
    async def get_user_with_tier_eager(
        self, db: AsyncSession, *, username: str | None = None, email: str | None = None
    ) -> dict[str, Any] | None:
        """Get user with tier in single query using LEFT JOIN.

        Args:
            db: Database session
            username: Username to look up (mutually exclusive with email)
            email: Email to look up (mutually exclusive with username)

        Returns:
            User dict with tier included, or None if not found

        Security: Respects tenant isolation via tenant context.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

        from ...core.tenancy.context import get_tenant_context

        if not username and not email:
            return None

        tenant = get_tenant_context()

        # Build WHERE clause based on lookup type
        if username:
            lookup_clause = User.username == username
        else:
            lookup_clause = User.email == email

        stmt = (
            select(User)
            .options(joinedload(User.tier))
            .where(
                lookup_clause,
                User.is_deleted.is_(False),
                User.tenant_id == tenant.id if tenant else True,
            )
        )
        result = await db.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            return None

        # Manually construct dict to avoid recursion issues with Pydantic models vs SQLAlchemy objects
        user_dict = {c.key: getattr(user, c.key) for c in User.__table__.columns}
        if user.tier:
            user_dict["tier"] = {c.key: getattr(user.tier, c.key) for c in user.tier.__table__.columns}
        else:
            user_dict["tier"] = None

        return user_dict


crud_users = CRUDUser(User)
