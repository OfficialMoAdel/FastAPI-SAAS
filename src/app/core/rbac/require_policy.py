"""FastAPI dependency for evaluating unified ResourcePolicy instances."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    pass
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import ForbiddenException
from src.app.core.logger import get_logger
from src.app.core.rbac.policy import ResourcePolicy

logger = get_logger(__name__)


def require_policy(policy: ResourcePolicy) -> Callable[..., Any]:
    """FastAPI dependency factory that evaluates a ResourcePolicy.

    If the policy evaluation returns False, raises 403 Forbidden.

    Usage:
        @router.patch(
            "/{id}",
            dependencies=[Depends(require_policy(OwnershipPolicy(owner_field="user_id", model=User)))],
        )
        async def update_user(...):
            ...
    """
    from src.app.api.deps import get_current_user

    async def _policy_checker(
        request: Request,
        current_user: Annotated[Any, Depends(get_current_user)],
        db: AsyncSession = Depends(async_get_db),
    ) -> None:
        authorized = await policy.evaluate(request=request, user=current_user, db=db)
        if not authorized:
            logger.warning(
                "Policy authorization failed",
                policy_type=type(policy).__name__,
                path=request.url.path if request else "unknown",
            )
            raise ForbiddenException("You do not have permission to perform this action.")

    return _policy_checker
