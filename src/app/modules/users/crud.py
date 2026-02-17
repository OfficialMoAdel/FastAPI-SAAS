from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ...core.db.tenant_crud import TenantAwareCRUD
from ...core.tenancy.context import get_tenant_context
from .models import User
from .schemas import UserCreateInternal, UserDelete, UserRead, UserUpdate, UserUpdateInternal


class CRUDUser(TenantAwareCRUD[User, UserCreateInternal, UserUpdate, UserUpdateInternal, UserDelete, UserRead]):
    async def get_user_with_tier_eager(
        self, db: AsyncSession, *, username: str | None = None, email: str | None = None
    ) -> dict[str, Any] | None:
        """Get user with tier in single query using LEFT JOIN.

        P1: Direct dict construction for ~30% faster serialization.

        Args:
            db: Database session
            username: Username to look up (mutually exclusive with email)
            email: Email to look up (mutually exclusive with username)

        Returns:
            User dict with tier included, or None if not found

        Security: Respects tenant isolation via tenant context.
        """
        if not username and not email:
            return None

        tenant = get_tenant_context()

        # Build WHERE clause based on lookup type
        if username:
            lookup_clause = User.username == username
        else:
            lookup_clause = User.email == email

        # Build base statement
        stmt = select(User).options(joinedload(User.tier)).where(lookup_clause, User.is_deleted == False)

        # Add tenant filter only if tenant context exists
        if tenant is not None:
            stmt = stmt.where(User.tenant_id == tenant.id)
        result = await db.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            return None

        # P1: Direct dict construction - avoids Pydantic validation overhead
        user_dict = {
            "id": user.id,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "name": user.name,
            "username": user.username,
            "email": user.email,
            "hashed_password": user.hashed_password,  # Required for auth service password verification
            "profile_image_url": user.profile_image_url,
            "tier_id": user.tier_id,
            "tenant_id": user.tenant_id,  # Required for auth service JWT claims
            "is_superuser": user.is_superuser,
            "is_deleted": user.is_deleted,  # A1: Required for token refresh security check
            "is_email_verified": user.is_email_verified,  # A2: Required for email verification status
            "token_version": user.token_version,  # FIXED: Was missing - required for session invalidation
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
