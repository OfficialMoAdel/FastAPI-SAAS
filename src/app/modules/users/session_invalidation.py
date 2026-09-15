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
    DEACTIVATION = "deactivation"


# Reasons that must force re-authentication by bumping token_version.
_BUMP_REASONS: frozenset[InvalidationReason] = frozenset(
    {
        InvalidationReason.CREDENTIAL_CHANGE,
        InvalidationReason.DELETION,
        InvalidationReason.TIER_CHANGE,
        InvalidationReason.DEACTIVATION,
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
        # Keep ids verbatim: no int() cast (UUID-string ids would crash).
        # tenant_id scoping stays optional-by-contract: raw-id callers are
        # role/permission bookkeeping (RBAC service passes self.tenant_id
        # always; see service.py assign/remove paths). tenant_scope itself
        # owns the fail-closed rule — a None tenant_id here means "caller
        # takes responsibility", not a bypass of the seam.
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
    fresh_token_versions: dict[Any, int] | None = None,
) -> None:
    """Delegate cache invalidation to UserService directly without reflection probes.

    1B: ``fresh_token_versions`` maps user id -> post-bump DB value, read by
    the seam after its atomic SQL bump. The single-user path forwards the
    fresh value as ``explicit_token_version`` (caller-supplied explicit wins);
    the batch path forwards the whole map so Redis can never be seeded with a
    stale ``old + 1`` guess.
    """
    from typing import cast

    from src.app.modules.users import UserService

    user_svc = UserService(db)
    if len(users_list) == 1 and not is_raw_id:
        resolved_explicit = explicit_token_version
        if bump and resolved_explicit is None:
            if fresh_token_versions:
                uid = (
                    users_list[0].get("id") if isinstance(users_list[0], dict) else getattr(users_list[0], "id", None)
                )
                resolved_explicit = fresh_token_versions.get(uid)
            if resolved_explicit is None:
                # Loud degraded fallback: the SQL bump already committed, so
                # failing here would leave DB bumped with Redis stale. Derive
                # old+1 from the caller payload as a last resort, but log at
                # error level so the divergence risk is visible in monitoring.
                # Production always resolves via the fresh read above.
                logger.error(
                    "session_invalidation_fresh_version_missed",
                    user_id=users_list[0].get("id")
                    if isinstance(users_list[0], dict)
                    else getattr(users_list[0], "id", None),
                )
                known = _known_token_version(users_list[0])
                if known is not None:
                    resolved_explicit = known + 1
        await user_svc._invalidate_user_cache(
            cast(Any, users_list[0]),
            bump_token_version=bump,
            reason=reason,
            explicit_token_version=resolved_explicit,
        )
    else:
        if bump and explicit_token_version is None and not fresh_token_versions:
            # Same loud-degraded contract as the single-user path above:
            # the SQL bump committed, so the cache layer's old+1 fallback
            # applies — log at error level so the divergence risk is visible.
            logger.error(
                "session_invalidation_fresh_version_missed_batch",
                user_count=len(extracted_users),
            )
        await user_svc._invalidate_user_cache_batch(
            extracted_users,
            bump_token_version=bump,
            reason=reason,
            explicit_token_versions=fresh_token_versions,
        )


def _known_token_version(user: Any) -> int | None:
    """Best-effort read of the caller payload's token_version (degraded fallback only)."""
    if isinstance(user, dict):
        raw: Any = user.get("token_version", 0)
    else:
        raw = getattr(user, "token_version", None)
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_returning_versions(result: Any) -> dict[Any, int] | None:
    """Parse UPDATE..RETURNING rows into ``{user_id: fresh token_version}``.

    Returns None when the result yields no fully readable rows (dialect
    without RETURNING support, or a test double) so the caller falls back to
    the legacy re-SELECT. A partially-readable row set is treated the same
    way: correctness over partial optimization.
    """
    try:
        rows = list(result.all())
    except Exception:
        return None
    if not rows:
        return None
    fresh: dict[Any, int] = {}
    for row in rows:
        try:
            fresh[row[0]] = int(row[1])
        except (IndexError, TypeError, ValueError):
            return None
    return fresh


async def _fetch_fresh_token_versions(
    db: AsyncSession,
    affected_ids: list[Any],
    tenant_id: Any,
) -> dict[Any, int]:
    """Read the post-bump token_version for each affected user.

    Best-effort: returns whatever rows are readable (empty on failure so
    test doubles without real rows do not break the seam; the cache layer
    then applies its own explicit-or-raise contract).

    tenant_id scoping is optional-by-contract (same rule as
    :func:`_extract_user_records`): RBAC bookkeeping always passes its
    tenant; a None tenant means the caller takes responsibility.
    """
    from src.app.modules.users.models import User

    try:
        stmt = select(User.id, User.token_version).where(User.id.in_(affected_ids))
        if tenant_id is not None:
            from src.app.core.db.tenant_crud import tenant_scope

            stmt = stmt.where(tenant_scope(User, tenant_id))
        result = await db.execute(stmt)
        rows = result.all()
        fresh: dict[Any, int] = {}
        for row in rows:
            try:
                fresh[row[0]] = int(row[1])
            except (IndexError, TypeError, ValueError):
                uid = getattr(row, "id", None)
                ver = getattr(row, "token_version", None)
                if uid is not None and isinstance(ver, int) and not isinstance(ver, bool):
                    fresh[uid] = ver
        return fresh
    except Exception as e:
        logger.warning("session_invalidation_fresh_version_read_failed", error=str(e))
        return {}


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

    fresh_token_versions: dict[Any, int] | None = None
    if bump and affected_ids:
        from src.app.modules.users.models import User

        # P2-D: RETURNING returns the post-bump values in the same round
        # trip, eliminating the legacy re-SELECT below when the dialect
        # (asyncpg, sqlite >= 3.35) supports it.
        update_stmt = (
            sa_update(User)
            .where(User.id.in_(affected_ids))
            .values(token_version=User.token_version + 1)
            .returning(User.id, User.token_version)
        )
        if tenant_id is not None:
            # Optional-by-contract scoping (see _fetch_fresh_token_versions):
            # RBAC callers always pass their tenant; None = caller owns it.
            from src.app.core.db.tenant_crud import tenant_scope

            update_stmt = update_stmt.where(tenant_scope(User, tenant_id))
        result = await db.execute(update_stmt)
        # Rowcount guard: a 0-row bump (wrong tenant / deleted user) must not
        # proceed to cache-invalidate, or Redis and DB silently diverge.
        # Never rollback the caller's session or return silent success here:
        # callers staged domain writes (commit=False) before this seam, so a
        # silent return would report success while discarding their write.
        rowcount = getattr(result, "rowcount", None)
        if isinstance(rowcount, int) and rowcount == 0:
            from src.app.shared.exceptions import ModelNotFoundError

            logger.warning("session_invalidation_zero_row_bump", affected_ids=affected_ids)
            raise ModelNotFoundError("User", affected_ids[0] if len(affected_ids) == 1 else affected_ids)
        # 1B: read back the fresh post-bump versions so the cache delegate
        # never seeds Redis with a stale old+1 guess. P2-D: the UPDATE
        # RETURNing rows carry the fresh values; the legacy re-SELECT only
        # runs when no rows were returned (dialect without RETURNING or
        # test double).
        if explicit_token_version is None:
            fresh_token_versions = _parse_returning_versions(result)
            if fresh_token_versions is None:
                fresh_token_versions = await _fetch_fresh_token_versions(db, affected_ids, tenant_id)

    # 2. Atomic DB commit if requested
    if commit:
        await db.commit()
    # 3. Optional RBAC permissions cache flush
    if flush_permissions and affected_ids:
        await _flush_permissions(affected_ids, tenant_id)

    # 4. Invalidate caches via UserService so test patch targets and metrics are observed
    await _invalidate_caches_delegate(
        db,
        users_list,
        extracted_users,
        is_raw_id,
        bump,
        reason,
        explicit_token_version,
        fresh_token_versions,
    )

__all__ = [
    "AUTH_INFO_CACHE_PREFIX",
    "InvalidationReason",
    "USER_CACHE_PREFIX",
    "build_user_invalidation_keys",
    "invalidate_user_sessions",
    "should_bump_token_version",
]
