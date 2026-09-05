"""Session-invalidation policy and unified execution module.

Encodes the "should this change bump the token version?" decision as a named,
testable policy, and provides a single, unified entry point
(:func:`invalidate_user_sessions`) for session and cache invalidation.

The security invariant this captures:
    Credential-affecting changes (password/email/username change, deletion,
    tier change, role grant/revoke) MUST invalidate existing sessions;
    benign profile edits (name, avatar, transparent hash upgrade) MUST NOT.
"""

from collections.abc import Sequence
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.core.utils.cache import build_tenant_key

logger = get_logger(__name__)

USER_CACHE_PREFIX = "user:profile"
AUTH_INFO_CACHE_PREFIX = "user:auth"


class InvalidationReason(str, Enum):
    """Why a user's cache/session state is being invalidated."""

    PROFILE_EDIT = "profile_edit"
    CREDENTIAL_CHANGE = "credential_change"
    DELETION = "deletion"
    TIER_CHANGE = "tier_change"


# Reasons that must force re-authentication by bumping token_version.
_BUMP_REASONS: frozenset[InvalidationReason] = frozenset(
    {
        InvalidationReason.CREDENTIAL_CHANGE,
        InvalidationReason.DELETION,
        InvalidationReason.TIER_CHANGE,
    }
)


def should_bump_token_version(reason: InvalidationReason) -> bool:
    """Return True when the reason requires invalidating active sessions.

    S3 FIX semantics: only security-affecting changes bump. Benign profile
    updates leave sessions intact.
    """
    return reason in _BUMP_REASONS


def build_user_invalidation_keys(
    user_id: int | str,
    username: str | None,
    email: str | None,
    tenant_id: Any = None,
) -> tuple[list[str], list[str]]:
    """Build delete keys and token_version keys for a user.

    Returns:
        tuple of (delete_keys, token_version_keys)
    """
    delete_keys: list[str] = [f"{USER_CACHE_PREFIX}:{user_id}"]
    tid_arg = str(tenant_id) if tenant_id else None

    token_keys: list[str] = []
    if username:
        norm_username = username.lower().strip()
        token_keys.append(build_tenant_key(f"user:{norm_username}:token_version", tid_arg))
    if email:
        norm_email = email.lower().strip()
        token_keys.append(build_tenant_key(f"user:{norm_email}:token_version", tid_arg))

    if tenant_id is not None:
        tenant_id_str = str(tenant_id)
        if email:
            norm_email = email.lower().strip()
            delete_keys.extend(
                [
                    f"{USER_CACHE_PREFIX}:{tenant_id_str}:email:{norm_email}",
                    f"{AUTH_INFO_CACHE_PREFIX}:{tenant_id_str}:{norm_email}",
                ]
            )
        if username:
            norm_username = username.lower().strip()
            delete_keys.extend(
                [
                    f"{USER_CACHE_PREFIX}:{tenant_id_str}:username:{norm_username}",
                    f"{AUTH_INFO_CACHE_PREFIX}:{tenant_id_str}:{norm_username}",
                ]
            )

    return delete_keys, token_keys


async def _extract_user_records(
    db: AsyncSession,
    users_list: list[Any],
    tenant_id: Any,
) -> tuple[list[Any], list[Any], bool]:
    """Extract standard dict representation of users from various input forms.

    Returns:
        (extracted_users, affected_ids, is_raw_id)
    """
    from src.app.modules.users.models import User

    first_item = users_list[0]
    is_raw_id = isinstance(first_item, int | str) and not isinstance(first_item, dict)

    if is_raw_id:
        # Keep ids verbatim: no int() cast (UUID-string ids would crash)
        user_ids = list(users_list)
        stmt = select(
            User.id, User.email, User.username, User.tenant_id, User.token_version
        ).where(User.id.in_(user_ids))
        if tenant_id is not None:
            from src.app.core.db.tenant_crud import tenant_scope

            stmt = stmt.where(tenant_scope(User, tenant_id))
        result = await db.execute(stmt)
        rows = list(result.all())
        return rows, user_ids, True

    extracted_users: list[Any] = []
    affected_ids: list[int | str] = []
    for u in users_list:
        extracted_users.append(u)
        uid = u.get("id") if isinstance(u, dict) else getattr(u, "id", None)
        if uid is not None:
            affected_ids.append(uid)
    return extracted_users, affected_ids, False


async def _flush_permissions(affected_ids: list[Any], tenant_id: Any) -> None:
    """Flush RBAC permissions cache for affected users."""
    try:
        from src.app.core.rbac.dependencies import (
            invalidate_user_permissions_cache,
            invalidate_users_permissions_cache_batch,
        )

        resolved_tid = tenant_id
        if len(affected_ids) == 1:
            await invalidate_user_permissions_cache(affected_ids[0], resolved_tid)
        else:
            await invalidate_users_permissions_cache_batch(list(affected_ids), resolved_tid)
    except Exception as e:
        logger.warning("failed_to_flush_user_permissions_cache", error=str(e))


async def _invalidate_caches_delegate(
    db: AsyncSession,
    users_list: list[Any],
    extracted_users: list[Any],
    is_raw_id: bool,
    bump: bool,
    reason: InvalidationReason,
    explicit_token_version: int | None,
) -> None:
    """Delegate cache invalidation to UserService so test patch targets and metrics are observed."""
    from typing import cast

    from src.app.modules.users.service import UserService

    user_svc = UserService(db)
    if len(users_list) == 1 and not is_raw_id and hasattr(user_svc, "_invalidate_user_cache"):
        await user_svc._invalidate_user_cache(
            cast(Any, users_list[0]),
            bump_token_version=bump,
            reason=reason,
            explicit_token_version=explicit_token_version,
        )
    elif hasattr(user_svc, "_invalidate_user_cache_batch"):
        await user_svc._invalidate_user_cache_batch(
            extracted_users,
            bump_token_version=bump,
            reason=reason,
        )
async def invalidate_user_sessions(
    db: AsyncSession,
    users: Any,
    reason: InvalidationReason,
    *,
    tenant_id: Any = None,
    flush_permissions: bool = False,
    commit: bool = True,
    explicit_token_version: int | None = None,
) -> None:
    """Invalidate all active sessions and cached state for one or more users.

    This is the ONE public seam for session invalidation. Callers state *why*
    (reason) and *who* (users). The module derives:
    - whether token_version should be bumped (reason -> policy)
    - column projection if only user IDs were supplied
    - atomic SQL bump
    - optional database commit
    - unified cache key construction via `build_user_invalidation_keys`
    - Redis deletion and 300s token_version race guard pipeline (via UserService cache delegate)
    - optional RBAC permission cache flush
    """
    if users is None:
        return

    if not isinstance(users, list | tuple | set | Sequence) or isinstance(users, str | bytes | dict):
        users_list = [users]
    else:
        users_list = list(users)

    if not users_list:
        return

    extracted_users, affected_ids, is_raw_id = await _extract_user_records(db, users_list, tenant_id)
    if not extracted_users:
        return

    # 1. SQL bump if credential/security affecting
    bump = should_bump_token_version(reason)

    if bump and affected_ids:
        from src.app.modules.users.models import User

        update_stmt = (
            sa_update(User)
            .where(User.id.in_(affected_ids))
            .values(token_version=User.token_version + 1)
        )
        if tenant_id is not None:
            from src.app.core.db.tenant_crud import tenant_scope

            update_stmt = update_stmt.where(tenant_scope(User, tenant_id))
        result = await db.execute(update_stmt)
        # Rowcount guard: a 0-row bump (wrong tenant / deleted user) must not
        # proceed to cache-invalidate, or Redis and DB silently diverge.
        rowcount = getattr(result, "rowcount", None)
        if isinstance(rowcount, int) and rowcount == 0:
            logger.warning("session_invalidation_zero_row_bump", affected_ids=affected_ids)
            if commit:
                await db.rollback()
            return

    # 2. Atomic DB commit if requested
    if commit:
        await db.commit()
    # 3. Optional RBAC permissions cache flush
    if flush_permissions and affected_ids:
        await _flush_permissions(affected_ids, tenant_id)

    # 4. Invalidate caches via UserService so test patch targets and metrics are observed
    await _invalidate_caches_delegate(
        db, users_list, extracted_users, is_raw_id, bump, reason, explicit_token_version
    )


__all__ = [
    "AUTH_INFO_CACHE_PREFIX",
    "InvalidationReason",
    "USER_CACHE_PREFIX",
    "build_user_invalidation_keys",
    "invalidate_user_sessions",
    "should_bump_token_version",
]
