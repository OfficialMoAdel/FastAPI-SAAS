from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.app.core.db.tenant_crud import TenantAwareCRUD, tenant_scope
from src.app.core.tenancy.context import (
    get_current_tenant,  # noqa: F401 — re-exported for legacy callers of this module
)
from src.app.modules.users.models import User
from src.app.modules.users.schemas import UserCreateInternal, UserDelete, UserRead, UserUpdate, UserUpdateInternal


class CRUDUser(TenantAwareCRUD[User, UserCreateInternal, UserUpdate, UserUpdateInternal, UserDelete, UserRead]):
    async def get_user_with_tier_eager(
        self,
        db: AsyncSession,
        *,
        id: int | None = None,
        username: str | None = None,
        email: str | None = None,
        require_active: bool = False,
        tenant_id: int | str | UUID | None = None,
    ) -> dict[str, Any] | None:
        """Get user with tier in single query using LEFT JOIN.

        P1: Direct dict construction for ~30% faster serialization.

        Args:
            db: Database session
            id: User ID to look up
            username: Username to look up (mutually exclusive with email and id)
            email: Email to look up (mutually exclusive with username and id)
            require_active: When True, also require ``User.is_active`` (auth-gated
                reads). Defaults to False so the auth hot paths that enforce
                activity downstream keep their current behavior.
            tenant_id: Explicit tenant scope for worker paths without an active
                tenant context. Ignored when a context tenant is active
                (context always wins — see ``tenant_scope``).

        Returns:
            User dict with tier included, or None if not found

        Security: Respects tenant isolation via tenant context.
        """
        if not id and not username and not email:
            return None

        # Lookup clause built first (case-insensitive on the pre-lowered
        # parameter so the planner can use the functional indexes).
        if id is not None:
            lookup_clause = User.id == id
        elif username is not None:
            lookup_clause = func.lower(User.username) == username.lower()
        else:
            email_val = email or ""
            lookup_clause = func.lower(User.email) == email_val.lower()

        # Tenant filter comes from the ONE seam: tenant_scope resolves the
        # context itself and is fail-closed without one (usernames/emails
        # are only unique per-tenant — no global lookup without a tenant).
        stmt = select(User).options(joinedload(User.tier)).where(lookup_clause, User.is_deleted.is_(False))
        if require_active:
            stmt = stmt.where(User.is_active.is_(True))
        stmt = stmt.where(tenant_scope(User, tenant_id))

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
