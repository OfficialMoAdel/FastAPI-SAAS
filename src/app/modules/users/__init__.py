"""Users module - user account management.

Components are imported lazily to avoid circular imports.
Cross-module imports MUST use the package root (P1-20: ruff TID252 bans
deep `users.service` / `users.router` imports outside this package):
    from src.app.modules.users import UserService, get_user_service
    from src.app.modules.users import UserDirectory
    from src.app.modules.users import router as users_router
    from src.app.modules.users.crud import crud_users
"""

__all__ = [
    "InvalidationReason",
    "User",
    "UserCacheInvalidator",
    "UserDirectory",
    "UserService",
    "get_user_directory",
    "crud_users",
    "get_user_service",
    "get_user_tenant_id",
    "users_router",
]

# Register user domain security lookups with core.security (DIP seam / PR-12)
from typing import TYPE_CHECKING, Any

from src.app.core.logger import get_logger
from src.app.core.security.jwt_service import (
    register_token_version_lookup,
    register_user_tenant_lookup,
    subject_log_ref,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    # Static-only re-exports: gives mypy real classes for the lazy
    # __getattr__ facade below (P1-20). Never executed at runtime,
    # so no circular-import cost.
    from src.app.modules.users.cache import UserCacheInvalidator as UserCacheInvalidator
    from src.app.modules.users.crud import crud_users as crud_users
    from src.app.modules.users.directory import UserDirectory as UserDirectory
    from src.app.modules.users.directory import get_user_directory as get_user_directory
    from src.app.modules.users.models import User as User
    from src.app.modules.users.router import get_user_service as get_user_service
    from src.app.modules.users.router import router as users_router
    from src.app.modules.users.service import UserService as UserService
    from src.app.modules.users.session_invalidation import InvalidationReason as InvalidationReason


async def _users_token_version_lookup(username_or_email: str) -> int | None:
    from src.app.core.db.database import local_session
    from src.app.modules.users.crud import crud_users

    async with local_session() as session:
        if "@" in username_or_email:
            user = await crud_users.get(session, email=username_or_email)
        else:
            user = await crud_users.get(session, username=username_or_email)
    if not user:
        return None
    return user.get("token_version")


async def _users_tenant_lookup(username_or_email: str, db: Any) -> Any | None:
    """Resolve a token subject's tenant WITHOUT an active tenant context.

    This is the BOOTSTRAP read: it answers "which tenant does this principal
    belong to" on the token path, *before* any tenant is known. It therefore
    cannot be tenant-scoped — the scope is the ANSWER, not an input — which is
    why it does NOT delegate to ``UserDirectory``/``crud_users.get``. Those are
    fail-closed on a missing context (``TenantContextError``), and
    ``jwt_service._resolve_tenant_id_for_token`` only reaches this function
    AFTER ``get_current_tenant()`` returned None. Delegating would make the
    fallback raise on every call.

    What it MUST still enforce, and previously did not:

    * ``is_deleted = false`` — a soft-deleted principal must not resolve a
      tenant. This was the real asymmetry with ``crud_users.get``: the same
      identifier resolved differently depending on which door read it.
    * ``lower()`` on both sides — ``users/crud.py`` lowercases, so the two
      doors must agree on what counts as the same identifier.

    The bypass is explicit rather than tribal: ``Unscoped`` names the reason
    in the audit log, and ``execute_scoped`` applies the shared DB circuit
    guard the hand-built ``db.execute`` skipped.
    """
    from sqlalchemy import func, select

    from src.app.core.db.scoped import execute_scoped
    from src.app.core.tenancy.scope import Unscoped
    from src.app.modules.users.models import User

    lowered = username_or_email.lower()
    # ONE arm per lookup, chosen by SHAPE, because each arm has to match an
    # index that exists. The username arm is served by the global functional
    # index ``idx_users_lower_username`` (01_foundation.py:114). The email arm's
    # only index is ``idx_users_tenant_lower_email`` = (tenant_id, lower(email)),
    # and this statement deliberately carries NO tenant_id predicate — the
    # tenant is the ANSWER here — so PostgreSQL cannot use it; an OR of the two
    # arms cannot be answered by a BitmapOr either, so the whole lookup degraded
    # to a sequential scan of ``users`` on the token-revocation path (every
    # logout/refresh of a token with no ``tenant_id`` claim). Choosing one arm
    # by shape is also what ``_users_token_version_lookup`` above already does,
    # so both doors classify an identifier the same way.
    identifier_column = User.email if "@" in username_or_email else User.username
    stmt = (
        select(User.tenant_id)
        .where(
            func.lower(identifier_column) == lowered,
            User.is_deleted.is_(False),
        )
        # Deterministic, and observable when it cannot be. ``username``/``email``
        # are unique only PER TENANT (``uq_user_username_tenant`` /
        # ``uq_user_email_tenant`` both include tenant_id), so the same
        # identifier can legitimately exist in two tenants — a normal SaaS case.
        # A bare ``LIMIT 1`` returned an ARBITRARY one, and that value flows into
        # ``TokenBlacklist.tenant_id`` and the blacklist cache key. Ordering by
        # the primary key makes the choice stable; fetching a SECOND row is what
        # makes the ambiguity visible instead of silent (one round trip either
        # way — ``limit(2)`` instead of ``limit(1)``).
        .order_by(User.id)
        .limit(2)
    )
    result = await execute_scoped(
        stmt,
        db=db,
        model=User,
        scope=Unscoped(reason="jwt-tenant-resolution"),
    )
    matches = result.scalars().all()
    if len(matches) > 1:
        # The subject is PII, so only the non-reversible reference is logged.
        logger.warning(
            "jwt_tenant_resolution_ambiguous",
            subject_ref=subject_log_ref(lowered),
            matches=len(matches),
            resolved_tenant_id=str(matches[0]),
        )
    return matches[0] if matches else None


async def get_user_tenant_id(db: Any, user_id: int) -> Any | None:
    """Return the tenant of a live (non-deleted) user, else None (I10 seam).

    Facade-level membership check for cross-module consumers (billing
    webhook handlers validating checkout metadata) — callers must not
    import ``users.models`` to run raw selects against User.
    """
    from sqlalchemy import select

    from src.app.modules.users.models import User

    stmt = select(User.tenant_id).where(User.id == user_id, User.is_deleted.is_(False))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def register_users_security_lookups() -> None:
    """Explicitly wire user-domain security lookups (idempotent).

    Prefer calling this from application lifespan so wiring order is
    deterministic; the import-time call below is kept for back-compat.
    """
    register_token_version_lookup(_users_token_version_lookup)
    register_user_tenant_lookup(_users_tenant_lookup)


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "users_router":
        from src.app.modules.users.router import router as users_router

        return users_router
    if name == "get_user_service":
        from src.app.modules.users.router import get_user_service

        return get_user_service
    if name == "UserService":
        from src.app.modules.users.service import UserService

        return UserService
    if name == "UserDirectory":
        from src.app.modules.users.directory import UserDirectory

        return UserDirectory
    if name == "get_user_directory":
        from src.app.modules.users.directory import get_user_directory

        return get_user_directory
    if name == "crud_users":
        from src.app.modules.users.crud import crud_users

        return crud_users
    if name == "User":
        from src.app.modules.users.models import User

        return User
    if name == "UserCacheInvalidator":
        from src.app.modules.users.cache import UserCacheInvalidator

        return UserCacheInvalidator
    if name == "InvalidationReason":
        from src.app.modules.users.session_invalidation import InvalidationReason

        return InvalidationReason
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
