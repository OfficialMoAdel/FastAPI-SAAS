from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.core.tenancy.context import get_tenant_context
from src.app.modules.users.models import User
from src.app.modules.users.schemas import UserCreateInternal, UserDelete, UserRead, UserUpdate, UserUpdateInternal


class CRUDUser(TenantAwareCRUD[User, UserCreateInternal, UserUpdate, UserUpdateInternal, UserDelete, UserRead]):
    async def get_user_with_tier_eager(
        self, db: AsyncSession, *, id: int | None = None, username: str | None = None, email: str | None = None
    ) -> dict[str, Any] | None:
        """Get user with tier in single query using LEFT JOIN.

        P1: Direct dict construction for ~30% faster serialization.

        Args:
            db: Database session
            id: User ID to look up
            username: Username to look up (mutually exclusive with email and id)
            email: Email to look up (mutually exclusive with username and id)

        Returns:
            User dict with tier included, or None if not found

        Security: Respects tenant isolation via tenant context.
        """
        if not id and not username and not email:
            return None

        tenant = get_tenant_context()

        # Build WHERE clause based on lookup type - case-insensitive
        if id is not None:
            lookup_clause = User.id == id
        elif username:
            lookup_clause = func.lower(User.username) == func.lower(username)
        else:
            lookup_clause = func.lower(User.email) == func.lower(email)

        # Build base statement
        stmt = select(User).options(joinedload(User.tier)).where(lookup_clause, User.is_deleted.is_(False))

        # Add tenant filter only if tenant context exists
        if tenant is not None:
            # SECURITY FIX: Ensure exact tenant mapping even through joins/eager loading.
            stmt = stmt.where(User.tenant_id == tenant.id)
        else:
            # SECURITY FIX: Do not allow global lookup without a tenant context
            # since usernames/emails are only unique per-tenant.
            raise RuntimeError("Tenant context is required for user lookup to prevent cross-tenant data exposure")

        result = await db.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            return None

        # Build auth payload directly from ORM object.
        # NOTE: Do not validate through API/user-input schema here because
        # auth payload must accept existing DB usernames/emails even when they
        # don't satisfy current request-time regex constraints.
        user_dict: dict[str, Any] = {
            "id": user.id,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "name": user.name,
            "username": user.username,
            "email": user.email,
            "profile_image_url": user.profile_image_url,
            "tenant_id": user.tenant_id,
            "tier_id": user.tier_id,
            "hashed_password": user.hashed_password,
            "is_superuser": user.is_superuser,
            "is_deleted": user.is_deleted,
            "is_active": user.is_active,
            "is_email_verified": user.is_email_verified,
            "token_version": user.token_version,
        }

        if user.tier:
            user_dict["tier"] = {
                "id": user.tier.id,
                "created_at": user.tier.created_at,
                "updated_at": user.tier.updated_at,
                "name": user.tier.name,
            }
        else:
            user_dict["tier"] = None

        return user_dict


crud_users = CRUDUser(User)
