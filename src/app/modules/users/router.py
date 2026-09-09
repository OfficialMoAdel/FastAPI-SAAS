"""User API router - HTTP handlers delegating to service layer.

RequestContext pattern (ADR-0001): every endpoint takes ``ctx:
RequestContextDep`` — one argument bundling the authenticated user, tenant
context, and request-scoped DB session. Ownership/IDOR checks go through
``ctx.require_ownership()`` (the single ownership seam) instead of inline
username/tenant comparisons.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep, get_current_superuser
from src.app.core.db.database import async_get_db
from src.app.core.pagination import PaginationParams, get_pagination_params
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.core.security import oauth2_scheme
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules._base.exceptions import ModelNotFoundError
from src.app.modules.users.schemas import (
    UserCreate,
    UserDeleteResponse,
    UserList,
    UserRead,
    UserReadPublic,
    UserStatusUpdate,
    UserTierResponse,
    UserTierUpdate,
    UserUpdate,
    UserUpdateResponse,
)
from src.app.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def get_user_service(db: AsyncSession = Depends(async_get_db)) -> UserService:
    """Dependency injection factory for UserService.

    Q-M4: factories live in router.py — service files stay Depends-free.
    (Absorbs the old pointless `_get_user_service` wrapper, M-4.)
    """
    return UserService(db)


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.USERS_MANAGE))],
)
async def write_user(
    user: UserCreate,
    service: UserService = Depends(get_user_service),
    db: AsyncSession = Depends(async_get_db),
) -> Any:
    """Create a new user."""
    from src.app.modules.auth.services.email_verification import make_email_verifier

    return await service.create(user, email_verifier=make_email_verifier(db))


@router.get(
    "",
    response_model=UserList,
    dependencies=[Depends(requires_permission(Permission.USERS_READ))],
)
async def read_users(
    pagination: PaginationParams = Depends(get_pagination_params),
    service: UserService = Depends(get_user_service),
) -> Any:
    """Get paginated list of users for the current tenant (superuser only)."""
    return await service.list_users(
        page=pagination.page,
        per_page=pagination.per_page,
    )


@router.get("/me", response_model=UserReadPublic)
async def read_users_me(
    ctx: RequestContextDep,
    service: UserService = Depends(get_user_service),
) -> Any:
    """Get current authenticated user profile.

    `ctx.user` returns auth-oriented payload; the service resolves the full
    user profile to satisfy the `UserRead` response contract.
    """
    user = await service.resolve_current_profile(ctx.user)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return user


@router.get("/{user_id}", response_model=UserReadPublic)
async def read_user(
    user_id: str,
    ctx: RequestContextDep,
    service: UserService = Depends(get_user_service),
) -> Any:
    """Get user by ID or username (authenticated users only).

    Supports both integer user IDs and username strings for backward compatibility.
    Tenant isolation is enforced in the service (cross-tenant reads 404);
    ownership via the single ownership seam (superuser bypass included).
    """
    # SECURITY: tenant isolation — a cross-tenant user is indistinguishable
    # from a nonexistent one (404, not 403). ctx.require_tenant() enforces
    # that this is a tenant-bound path.
    ctx.require_tenant()
    try:
        user = await service.get_profile(user_id, tenant_id=ctx.tenant_id)
    except ModelNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")

    # SECURITY: IDOR via the single ownership seam (superuser bypass included).
    ctx.require_ownership(get_user_attr(user, "id"))

    return user


@router.patch("/{user_id}", response_model=UserUpdateResponse)
async def patch_user(
    values: UserUpdate,
    user_id: str,
    ctx: RequestContextDep,
    service: UserService = Depends(get_user_service),
) -> Any:
    """Update a user (partial update).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    result = await service.update(user_id, values, ctx.user)
    return UserUpdateResponse(message=result.get("message", "User updated successfully"))


@router.put("/{user_id}", response_model=UserUpdateResponse)
async def put_user(
    values: UserUpdate,
    user_id: str,
    ctx: RequestContextDep,
    service: UserService = Depends(get_user_service),
) -> Any:
    """Update a user (full update, alias for PATCH).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    result = await service.update(user_id, values, ctx.user)
    return UserUpdateResponse(message=result.get("message", "User updated successfully"))


@router.delete("/{user_id}", response_model=UserDeleteResponse)
async def erase_user(
    user_id: str,
    ctx: RequestContextDep,
    service: UserService = Depends(get_user_service),
    token: str = Depends(oauth2_scheme),
) -> Any:
    """Soft delete a user.

    Supports both integer user IDs and username strings for backward compatibility.
    """
    result = await service.delete(user_id, ctx.user, token)
    return UserDeleteResponse(message=result.get("message", "User deleted successfully"))


@router.delete("/db/{user_id}", response_model=UserDeleteResponse, dependencies=[Depends(get_current_superuser)])
async def erase_db_user(
    user_id: str,
    service: UserService = Depends(get_user_service),
    token: str = Depends(oauth2_scheme),
) -> UserDeleteResponse:
    """Permanently delete a user from database (superuser only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # R1-15: typed DTO instead of dict[str, str] so the response contract is
    # explicit in the OpenAPI schema.
    return UserDeleteResponse(**await service.hard_delete(user_id, token))


@router.get("/{user_id}/rate-limits", response_model=dict[str, Any], dependencies=[Depends(get_current_superuser)])
async def read_user_rate_limits(user_id: str, service: UserService = Depends(get_user_service)) -> dict[str, Any]:
    """Get user's rate limits based on their tier.

    Supports both integer user IDs and username strings for backward compatibility.
    """
    return await service.get_with_rate_limits(user_id)


@router.get("/{user_id}/tier", response_model=UserTierResponse | None)
async def read_user_tier(
    user_id: str,
    ctx: RequestContextDep,
    service: UserService = Depends(get_user_service),
) -> Any:
    """Get user's tier information (authenticated users only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # SECURITY: IDOR via the single ownership seam — the path may carry an
    # int id or a username; check both against the principal.
    from src.app.core.utils.user_utils import get_user_attr

    principal_id = str(ctx.user_id)
    principal_username = str(get_user_attr(ctx.user, "username"))
    if str(user_id) != principal_id and str(user_id) != principal_username:
        ctx.require_ownership(user_id)

    result = await service.get_with_tier(user_id)
    if result is None:
        return None
    return UserTierResponse(
        tier_id=result.get("tier_id"),
        tier_name=result.get("tier_name"),
        tier_price=result.get("tier_price"),
        tier_currency=result.get("tier_currency"),
    )


@router.patch("/{user_id}/tier", response_model=UserUpdateResponse, dependencies=[Depends(get_current_superuser)])
async def patch_user_tier(
    user_id: str, values: UserTierUpdate, service: UserService = Depends(get_user_service)
) -> UserUpdateResponse:
    """Update user's tier (superuser only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # R1-15: typed DTO instead of dict[str, str].
    return UserUpdateResponse(**await service.update_tier(user_id, values))


@router.patch("/{user_id}/status", response_model=UserUpdateResponse, dependencies=[Depends(get_current_superuser)])
async def patch_user_status(
    user_id: str,
    values: UserStatusUpdate,
    service: UserService = Depends(get_user_service),
) -> UserUpdateResponse:
    """Activate or deactivate a user (superuser only).

    B2: deactivation bumps token_version and invalidates cached auth info so
    existing JWTs stop authorizing immediately instead of after cache TTL.
    """
    return UserUpdateResponse(**await service.set_user_status(user_id, values.is_active))
