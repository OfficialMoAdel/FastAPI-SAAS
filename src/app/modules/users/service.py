"""User service layer - encapsulates all user business logic.

GOD-SERVICE WATCH (P3, deliberate): this module is the largest service in
the codebase and carries several function-level imports used to dodge
import cycles. Scope of the agreed remediation is observability/comments
ONLY — do not split without a dedicated review of the cache/event seams
that motivate the cycles (see PLAN Round-1 Quality P3)."""


from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.app.core.db.scoped import execute_scoped
from src.app.core.events import EventPriority, event_bus
from src.app.core.logger import get_logger
from src.app.core.principal import AuthenticatedUser
from src.app.core.security import blacklist_token, get_password_hash
from src.app.core.tenancy.context import get_current_tenant, require_tenant_match
from src.app.core.tenancy.scope import ExplicitTenant, Unscoped
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules._base.service import fetch_page_with_total
from src.app.modules.audit_logs import AuditGuarantee, audit
from src.app.modules.tiers.models import Tier
from src.app.modules.tiers.service import TierService
from src.app.modules.users.crud import CRUDUser, crud_users
from src.app.modules.users.models import User
from src.app.modules.users.schemas import UserCreate, UserCreateInternal, UserRead, UserTierUpdate, UserUpdate
from src.app.modules.users.session_invalidation import InvalidationReason, should_bump_token_version
from src.app.shared.exceptions import DuplicateError, ForbiddenError, ModelNotFoundError

logger = get_logger(__name__)

from src.app.modules.users.cache import (
    UserCacheInvalidator,
    _user_email_cache_key,
)


class UserService:
    """Service class for user-related business logic.

    Note
    ----
    ``UserService`` deliberately does **not** subclass ``BaseService``.
    Its read/update/delete contracts return validated dicts / message dicts
    rather than ORM entities, which would violate Liskov Substitution if
    inherited. It composes the same primitives (``model``, ``db``, ``crud``)
    directly instead.
    """

    model = User

    crud: CRUDUser  # Custom CRUD with get_user_with_tier_eager

    def __init__(
        self,
        db: AsyncSession,
    ) -> None:
        self.db = db
        self.crud = crud_users

    @staticmethod
    def _parse_user_id(username: str) -> int | None:
        """M-8: parse a user identifier that may be an integer ID or a username.

        Returns the integer ID when ``username`` is a valid int string, else None.
        """
        try:
            return int(username)
        except ValueError:
            return None

    async def _get_active_user(self, username: str) -> dict[str, Any] | None:
        """M-8: fetch a non-deleted user by integer ID or username.

        Consolidates the six copy-pasted ``try: int(username)`` lookup blocks.
        H-4: dead crud-fallback branch removed — the repository layer was
        always constructed, so the else-branch could never run.

        Deliberately NOT routed through :meth:`get_tenant_user`: this helper
        serves privileged management flows (update/delete/tier/status,
        hard-delete) that run under ``USERS_MANAGE`` / superuser gates and
        may execute without an active tenant context (superusers see all
        tenants) — the tenant seam is fail-closed without context and would
        break those paths. Auth-gated reads must use :meth:`get_active_user`
        and role bookkeeping must use :meth:`get_tenant_user`; do not add new
        callers here.
        """
        parsed_id = self._parse_user_id(username)
        if parsed_id is not None:
            return await self.crud.get(db=self.db, id=parsed_id, is_deleted=False)
        return await self.crud.get(db=self.db, username=username, is_deleted=False)

    async def list_users(
        self,
        *,
        page: int = 1,
        per_page: int = 10,
        is_deleted: bool = False,
    ) -> dict[str, Any]:
        """Get paginated list of users.

        H-4: repository branch removed — inline the FastCRUD call directly.
        """
        offset = (page - 1) * per_page
        result = await fetch_page_with_total(
            self.crud,
            self.db,
            offset=offset,
            limit=per_page,
            is_deleted=is_deleted,
            schema_to_select=UserRead,
        )
        from src.app.core.pagination import build_paginated_response

        return build_paginated_response(
            items=result["data"],
            total=result["total_count"],
            page=page,
            per_page=per_page,
        )

    async def _cache_user(self, user: User | dict[str, Any]) -> None:
        """Cache user profile in Redis (delegates to UserCacheInvalidator)."""
        await UserCacheInvalidator.cache_user(user)

    async def _invalidate_user_cache(
        self,
        user: User | dict[str, Any],
        bump_token_version: bool,
        reason: InvalidationReason,
        explicit_token_version: int | None = None,
    ) -> None:
        """Invalidate all cache entries for a user (delegates to UserCacheInvalidator)."""
        await UserCacheInvalidator.invalidate_user_cache(user, bump_token_version, reason, explicit_token_version)

    async def _invalidate_user_cache_batch(
        self,
        users: Sequence[Any],
        bump_token_version: bool = False,
        reason: InvalidationReason | None = None,
        explicit_token_versions: dict[Any, int] | None = None,
    ) -> None:
        """Invalidate cache entries for many users in ONE Redis pipeline (delegates to UserCacheInvalidator)."""
        await UserCacheInvalidator.invalidate_user_cache_batch(
            users, bump_token_version, reason, explicit_token_versions
        )

    async def invalidate_sessions(
        self,
        users: Any,
        reason: InvalidationReason,
        *,
        tenant_id: Any = None,
        flush_permissions: bool = False,
        commit: bool = True,
        explicit_token_version: int | None = None,
    ) -> None:
        """Public seam: invalidate sessions and cache for users."""
        from src.app.modules.users.session_invalidation import invalidate_user_sessions

        await invalidate_user_sessions(
            self.db,
            users,
            reason,
            tenant_id=tenant_id,
            flush_permissions=flush_permissions,
            commit=commit,
            explicit_token_version=explicit_token_version,
        )

    async def _get_cached_user(self, cache_key: str) -> dict[str, Any] | None:
        """Get cached user data if available (delegates to UserCacheInvalidator)."""
        return await UserCacheInvalidator.get_cached_user(cache_key)

    async def create(
        self,
        user: UserCreate,
        email_verifier: Callable[[str], Awaitable[None]] | None = None,
    ) -> User:
        """Create a new user with validation and password hashing.

        Parameters
        ----------
        user : UserCreate
            User creation data
        email_verifier : Callable[[str], Awaitable[None]] | None
            Optional callback invoked with the new user's email to send a
            verification email. Injected by routers; None skips the email.

        Returns
        -------
        User
            Created user instance

        Raises
        ------
        DuplicateError
            If the email or username already exists (uniform response — the
            field is never disclosed)
        """
        # SECURITY FIX: Check for duplicates BEFORE expensive password hashing
        # Prevents DoS via forced bcrypt operations
        # OPTIMIZATION FIX: Fetch tenant once and reuse throughout method (#16)
        tenant = get_current_tenant()  # Single fetch, reused throughout

        # Optimized: Check for duplicate email or username in single query.
        # id-only projection — only existence is tested, so no column data
        # (notably hashed_password) is ever fetched or materialized.
        stmt = select(User.id).where(
            or_(User.email == user.email, User.username == user.username), User.is_deleted.is_(False)
        )

        scope = (
            ExplicitTenant(tenant_id=tenant.id, reason="legacy-criterion:User")
            if tenant is not None
            else Unscoped(reason="legacy-criterion:User")
        )
        dup_result = await execute_scoped(stmt, db=self.db, model=User, scope=scope)
        existing_user = dup_result.scalar()

        if existing_user:
            # S-M2: uniform duplicate response — which field collided is an
            # enumeration oracle for attackers. One generic credentials-taken
            # error regardless of whether email or username matched.
            raise DuplicateError("credentials", message="An account with these credentials already exists")

        # Hash password and create internal user object
        user_dict = user.model_dump()
        user_dict["hashed_password"] = await get_password_hash(password=user_dict["password"])
        del user_dict["password"]

        # Inject tenant_id from context - uses same cached tenant
        if tenant is not None:
            user_dict["tenant_id"] = tenant.id

        user_internal = UserCreateInternal(**user_dict)

        try:
            # B8/H-7-advisory: FastCRUD.create without schema_to_select
            # returns None (verified against fastcrud 0.20.1 execution.py),
            # so the old `cast(User, ...)` + `result.id` deref 500'd every
            # registration. Commit-then-read: flush via commit=False create,
            # commit, then read back the real ORM row for the payload and
            # the `-> User` return contract.
            await self.crud.create(db=self.db, object=user_internal, commit=False)
            await self.db.commit()
            read_stmt = select(User).where(
                or_(User.email == user.email, User.username == user.username),
                User.is_deleted.is_(False),
            )
            scope = (
                ExplicitTenant(tenant_id=tenant.id, reason="legacy-criterion:User")
                if tenant is not None
                else Unscoped(reason="legacy-criterion:User")
            )
            read_result = await execute_scoped(read_stmt, db=self.db, model=User, scope=scope)
            result: User | None = read_result.scalar_one_or_none()
            if result is None:
                raise RuntimeError("User row unreadable immediately after successful commit")

            # SECURITY FIX: Ensure event is published ONLY after successful commit
            # Capture result data before commit since it may expire after session flush
            user_event_payload = {
                "user_id": result.id,
                "email": result.email,
                "username": result.username,
                "tenant_id": str(result.tenant_id),
            }

            # M22: Send verification email automatically upon registration.
            # Decoupled via injected email_verifier (PR-10): UserService no longer
            # imports AuthService directly; routers inject the verifier.
            if email_verifier is not None:
                try:
                    await email_verifier(result.email)
                except Exception as email_err:
                    logger.error(
                        "Failed to send verification email during registration",
                        error=str(email_err),
                        user_id=result.id,
                    )

            def _log_task_error(task: asyncio.Task) -> None:
                """Log errors from background event tasks so they are not silently swallowed."""
                exc = task.exception() if not task.cancelled() else None
                if exc is not None:
                    logger.error(
                        "Background event publish failed",
                        action="user.created",
                        error=str(exc),
                        exc_info=exc,
                    )

            # Fire-and-forget task to publish the event safely from async context
            from src.app.core.utils.background_task import create_safe_task

            task = create_safe_task(
                event_bus.publish("user.created", user_event_payload, priority=EventPriority.NORMAL)
            )
            task.add_done_callback(_log_task_error)

        except IntegrityError as e:
            # Handle race condition: duplicate was created between our check and insert.
            # S-M2: same uniform generic response as the pre-check — the unique
            # constraint name would otherwise reveal which field collided.
            if "uq_user_email_tenant" in str(e) or "uq_user_username_tenant" in str(e):
                await self.db.rollback()
                raise DuplicateError("credentials", message="An account with these credentials already exists") from e
            raise

        return result

    async def get_user_auth_info(
        self, *, username: str | None = None, email: str | None = None
    ) -> dict[str, Any] | None:
        """Get user with tier info for authentication (cached).

        Parameters
        ----------
        username : str | None
            Username to look up (mutually exclusive with email)
        email : str | None
            Email to look up (mutually exclusive with username)

        Returns
        -------
        dict[str, Any] | None
            User dict with tier info, or None if not found
        """
        from src.app.modules.users.directory import UserDirectory

        info = await UserDirectory(self.db).auth_info(username=username, email=email)
        return info.model_dump(mode="json") if info else None

    async def get_active_user(
        self,
        *,
        id: int | None = None,
        username: str | None = None,
        email: str | None = None,
        tenant_id: Any = None,
    ) -> dict[str, Any] | None:
        """Named-lookup seam: fetch a non-deleted, active user with tier eager-loaded.

        Enforces ``is_deleted=False`` + ``is_active=True`` + tenant scope in a
        single query, built on ``CRUDUser.get_user_with_tier_eager`` (which
        itself composes ``criterion``) — not a parallel helper. Returns the
        tier-eager auth payload dict, or None when no such user exists in scope.

        Auth-gated reads (password/MFA/passkey/magic-link verification, RBAC
        membership checks) use this; pure audit tenant-extracts use the seam
        without the active gate.

        Parameters
        ----------
        id : int | None
            User ID to look up (mutually exclusive with username/email).
        username : str | None
            Username to look up (case-insensitive, per-tenant unique).
        email : str | None
            Email to look up (case-insensitive, per-tenant unique).
        tenant_id : Any
            Explicit tenant scope for worker paths without an active tenant
            context. Ignored when a context tenant is active.
        """
        from src.app.modules.users.directory import UserDirectory

        user = await UserDirectory(self.db).active(id=id, username=username, email=email, tenant_id=tenant_id)
        return user.model_dump(mode="json") if user else None

    async def get_tenant_user(
        self,
        *,
        id: int | None = None,
        username: str | None = None,
        email: str | None = None,
        tenant_id: Any = None,
    ) -> dict[str, Any] | None:
        """Membership seam: fetch a non-deleted user with tier eager-loaded.

        Enforces ``is_deleted=False`` + tenant scope but NOT ``is_active`` —
        role-assignment bookkeeping must work for onboarding flows
        (create inactive → assign role → activate) and cleanup flows
        (strip roles from deactivated users). Authentication paths keep
        using :meth:`get_active_user`.
        """
        from src.app.modules.users.directory import UserDirectory

        user = await UserDirectory(self.db).member(id=id, username=username, email=email, tenant_id=tenant_id)
        return user.model_dump(mode="json") if user else None

    async def get_by_username(self, username: str) -> dict[str, Any]:
        """Get a user by username with Redis caching.

        Args:
        ----
            username: Username to look up

        Returns
        -------
            dict[str, Any]
            User dict

        Raises
        ------
        ModelNotFoundError
            If user not found
        """

        from src.app.modules.users.directory import UserDirectory

        return (await UserDirectory(self.db).by_username(username)).model_dump(mode="json")

    async def get(self, user_id: Any) -> dict[str, Any] | None:
        """Get a user by ID with Redis caching.

        Parameters
        ----------
        user_id : Any
            User ID (UUID)

        Returns
        -------
        dict[str, Any] | None
            User dict or None if not found
        """
        from src.app.modules.users.directory import UserDirectory

        user = await UserDirectory(self.db).by_id(user_id)
        return user.model_dump(mode="json") if user else None

    async def get_profile(
        self,
        identifier: str,
        *,
        tenant_id: Any,
    ) -> dict[str, Any]:
        """Load a user by ID or username and enforce tenant isolation.

        Cross-tenant lookups raise ModelNotFoundError so they are
        indistinguishable from nonexistent users (404, not 403).
        """
        from src.app.modules.users.directory import UserDirectory

        user = await UserDirectory(self.db).profile(identifier, tenant_id=tenant_id)
        return user.model_dump(mode="json")

    async def resolve_current_profile(self, current_user: Any) -> dict[str, Any] | None:
        """Resolve the full profile for the authenticated principal.

        Returns the principal directly when it already carries profile fields
        (e.g. loaded via API key or DB fallback); otherwise loads via ID or
        username. Returns None when the principal is unresolvable.
        """
        if isinstance(current_user, dict):
            user_id = current_user.get("id")
            username = current_user.get("username")
            if {"name", "profile_image_url"}.issubset(current_user.keys()):
                return current_user
        else:
            user_id = getattr(current_user, "id", None)
            username = getattr(current_user, "username", None)
            # Optional profile fields are None on partial principals (tests,
            # minimal payloads). Treat only populated fields as "carried".
            has_name = getattr(current_user, "name", None) is not None
            has_avatar = getattr(current_user, "profile_image_url", None) is not None
            if has_name and has_avatar:
                # Attribute-bearing principal (ORM entity, API-key payload):
                # serialize to a real dict so downstream `.get()` access is safe.
                try:
                    return UserRead.model_validate(current_user).model_dump(mode="json")
                except Exception as e:
                    logger.warning("Unserializable user principal in resolve_current_profile", error=str(e))
                    return None
        try:
            if user_id is not None:
                user = await self.get(int(user_id))
                if user:
                    return user
            elif username:
                try:
                    return await self.get_by_username(username)
                except ModelNotFoundError:
                    return None
        except (ValueError, TypeError):
            if username:
                try:
                    return await self.get_by_username(username)
                except ModelNotFoundError:
                    return None
        return None

    async def get_by_email(self, email: str) -> dict[str, Any] | None:
        """Get a user by email address with Redis caching.

        Parameters
        ----------
        email : str
            Email address to look up

        Returns
        -------
        dict[str, Any] | None
            User dict or None if not found
        """
        # Try cache first - return cached data directly to avoid DB query
        cached = await self._get_cached_user(_user_email_cache_key(email))
        if cached:
            logger.debug("Cache hit for user email")
            return cached

        user = await self.crud.get(db=self.db, email=email, is_deleted=False)
        if user:
            await self._cache_user(user)
            from src.app.modules.users.schemas import UserRead

            return UserRead.model_validate(user).model_dump(mode="json")
        return None

    async def update(
        self,
        username: str,
        values: UserUpdate,
        current_user: AuthenticatedUser,
    ) -> dict[str, str]:
        """Update a user with ownership validation.

        Parameters
        ----------
        username : str
            Username of user to update
        values : UserUpdate
            Update values
        current_user : AuthenticatedUser
            Currently authenticated user

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If user not found
        ForbiddenError
            If current user doesn't own this account
        DuplicateError
            If new email/username already taken
        """
        # SECURITY FIX: Prevent updating soft-deleted users
        db_user = await self._get_active_user(username)

        if db_user is None:
            raise ModelNotFoundError("User", username)
        db_username = get_user_attr(db_user, "username")
        db_email = get_user_attr(db_user, "email")
        # P2-C: cross-tenant ownership via the tenancy seam (soft-404 — a
        # cross-tenant username stays invisible instead of leaking existence).
        require_tenant_match(get_user_attr(db_user, "tenant_id"), resource="User", key=username)

        # Ownership check (superusers bypass)
        if db_username != current_user.username and not current_user.is_superuser:
            raise ForbiddenError("You do not have permission to modify this user's email.")
        # Check for duplicate email if changing (exclude soft-deleted users)
        reason = InvalidationReason.PROFILE_EDIT
        if values.email is not None and values.email != db_email:
            reason = InvalidationReason.CREDENTIAL_CHANGE
            tenant_id_val = get_user_attr(db_user, "tenant_id")
            if await self.crud.exists(db=self.db, email=values.email, tenant_id=tenant_id_val, is_deleted=False):
                raise DuplicateError("email", values.email)

        # Check for duplicate username if changing (exclude soft-deleted users)
        if values.username is not None and values.username != db_username:
            reason = InvalidationReason.CREDENTIAL_CHANGE
            tenant_id_val = get_user_attr(db_user, "tenant_id")
            if await self.crud.exists(db=self.db, username=values.username, tenant_id=tenant_id_val, is_deleted=False):
                raise DuplicateError("username", values.username)

        await self.crud.update(db=self.db, object=values, username=db_username, commit=False)

        # 1B: the seam owns the SQL bump + commit + cache in one place, so a
        # credential change can never bump twice or diverge Redis from the DB.
        tenant_id_val = get_user_attr(db_user, "tenant_id")
        await self.invalidate_sessions(db_user, reason, tenant_id=tenant_id_val, commit=True)
        return {"message": "User updated"}

    async def update_password_hash(self, user_id: int, hashed_password: str) -> None:
        """Persist a new password hash and invalidate cached auth data.

        Used by transparent rehash-on-login (A7): the plaintext password is
        unchanged, so token_version is NOT bumped and existing sessions stay
        valid. Only the stored hash (cost factor) is upgraded.
        """
        await self.crud.update(db=self.db, object={"hashed_password": hashed_password}, id=user_id, commit=False)
        await self.db.commit()

        db_user = await self.crud.get(db=self.db, id=user_id, is_deleted=False)
        if db_user is not None:
            # PROFILE_EDIT-class change: hash upgrade without plaintext
            # rotation must NOT force every active session to re-auth.
            await self._invalidate_user_cache(db_user, bump_token_version=False, reason=InvalidationReason.PROFILE_EDIT)

    async def delete(
        self,
        username: str,
        current_user: AuthenticatedUser,
        token: str,
    ) -> dict[str, str]:
        """Soft delete a user with ownership validation.

        Parameters
        ----------
        username : str
            Username of user to delete
        current_user : AuthenticatedUser
            Currently authenticated user
        token : str
            Access token to blacklist

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If user not found
        ForbiddenError
            If current user doesn't own this account
        """
        db_user = await self._get_active_user(username)

        if not db_user:
            raise ModelNotFoundError("User", username)

        # P2-C: same-username users in other tenants stay invisible — without
        # this check a superuser from Tenant A could delete Tenant B's user.
        require_tenant_match(get_user_attr(db_user, "tenant_id"), resource="User", key=username)

        if username != current_user.username and not current_user.is_superuser:
            raise ForbiddenError("You do not have permission to delete this user.")

        # BUG FIX: Previously used two separate auto-committing calls:
        #   1. crud.update(token_version + 1)  <- auto-commit
        #   2. crud.delete(username)            <- auto-commit (soft delete)
        # A crash between them left token_version incremented but is_deleted=False:
        # old sessions were invalidated but the user could still log in.
        # Fix: combine both into a single atomic UPDATE, commit once.
        from datetime import UTC, datetime

        from sqlalchemy import update as sa_update

        user_id_val = get_user_attr(db_user, "id")
        user_tenant_id = get_user_attr(db_user, "tenant_id")

        # SECURITY FIX: Blacklist token before DB operation to ensure a mid-flight crash
        # doesn't leave a deleted user with a valid token.
        await blacklist_token(token=token, db=self.db)

        await self.db.execute(
            sa_update(User)
            .where(User.id == user_id_val)
            .values(
                is_deleted=True,
                updated_at=datetime.now(UTC),
            )
        )
        # 1B: the seam owns the SQL bump + commit + cache; the soft-delete
        # flag staged above commits atomically with the seam's bump.
        await self.invalidate_sessions(
            db_user,
            InvalidationReason.DELETION,
            tenant_id=user_tenant_id,
            commit=True,
        )

        # SECURITY FIX: Add audit log for user deletion (Issue 15)
        await audit(
            action="user.deleted",
            entity_type="user",
            entity_id=user_id_val,
            tenant_id=user_tenant_id,
            user_id=current_user.id,
            details={"username": username, "deleted_by": current_user.username},
            guarantee=AuditGuarantee.BEST_EFFORT,
        )

        return {"message": "User deleted successfully"}

    async def hard_delete(self, username: str, token: str) -> dict[str, str]:
        """Permanently delete a user from database (superuser only).

        Parameters
        ----------
        username : str
            Username of user to delete
        token : str
            Access token to blacklist

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If user not found
        """
        # SECURITY FIX: Single query with soft-delete filter (prevents double query)
        db_user = await self._get_active_user(username)

        if not db_user:
            raise ModelNotFoundError("User", username)

        # P2-C: cross-tenant hard-delete stays invisible (soft-404, same as
        # update/delete) so existence is never leaked via 403.
        require_tenant_match(get_user_attr(db_user, "tenant_id"), resource="User", key=username)

        # SECURITY FIX: Delete user BEFORE invalidating cache
        # Prevents stale cache if DB operation fails

        # SECURITY FIX: Blacklist token before DB operation
        await blacklist_token(token=token, db=self.db)

        db_username = get_user_attr(db_user, "username")
        # Capture the pre-delete version BEFORE the row is gone — reading it
        # after db_delete risks detached/stale ORM state.
        pre_version = get_user_attr(db_user, "token_version", 0)
        await self.crud.db_delete(db=self.db, username=db_username)
        # 1B: the row is gone so no SQL bump is possible; seed Redis with the
        # pre-delete version + 1 explicitly instead of a stale-dict guess.
        try:
            explicit_version = int(pre_version) + 1
        except (TypeError, ValueError):
            explicit_version = 1
        await self._invalidate_user_cache(
            db_user,
            bump_token_version=should_bump_token_version(InvalidationReason.DELETION),
            reason=InvalidationReason.DELETION,
            explicit_token_version=explicit_version,
        )

        return {"message": "User deleted from the database"}

    async def update_tier(self, username: str, values: UserTierUpdate) -> dict[str, str]:
        """Update user's tier (superuser only).

        Parameters
        ----------
        username : str
            Username of user to update
        values : UserTierUpdate
            Tier update values

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If user or tier not found
        """
        # Direct service construction per ADR-0001 (service registry deleted)
        db_user = await self._get_active_user(username)

        if db_user is None:
            raise ModelNotFoundError("User", username)

        db_username = get_user_attr(db_user, "username")

        # Validate tier exists via the canonical TierService
        tier_svc = TierService(self.db)
        try:
            await tier_svc.get_tier_by_id(values.tier_id)
        except ModelNotFoundError:
            raise ModelNotFoundError("Tier", values.tier_id)

        await self.crud.update(db=self.db, object=values.model_dump(), username=db_username, commit=False)

        # 1B: the seam owns the SQL bump + commit + cache; the reason selects
        # TIER_CHANGE policy so cached tier claims are invalidated promptly.
        await self.invalidate_sessions(
            db_user,
            InvalidationReason.TIER_CHANGE,
            tenant_id=get_user_attr(db_user, "tenant_id"),
            commit=True,
        )

        # A3 FIX: Handle both ORM objects and dict responses
        user_name = get_user_attr(db_user, "name")
        return {"message": f"User {user_name} Tier updated"}

    async def set_user_status(
        self,
        username: str,
        is_active: bool,
    ) -> dict[str, str]:
        """Activate or deactivate a user (superuser/admin action only).

        B2 FIX (review P2): deactivation previously had no sanctioned path —
        ``is_active`` was only ever flipped False→True by email verification,
        and direct DB/CRUDAdmin edits bypassed cache invalidation entirely,
        leaving a deactivated user's cached ``is_active=True`` (and existing
        JWTs) valid for up to AUTH_INFO_CACHE_TTL. This method treats any
        status flip as DEACTIVATION-class: bump token_version in the same
        transaction (kills existing JWTs) and invalidate all cache entries.

        Callers are responsible for authorization (superuser-gated route);
        the service enforces tenant scoping via the tenant-aware CRUD seam.
        """
        db_user = await self._get_active_user(username)
        if db_user is None:
            raise ModelNotFoundError("User", username)

        current_status = get_user_attr(db_user, "is_active")
        if bool(current_status) == is_active:
            return {"message": f"User {get_user_attr(db_user, 'name')} already {'active' if is_active else 'inactive'}"}

        user_id = get_user_attr(db_user, "id")
        await self.crud.update(db=self.db, object={"is_active": is_active}, id=user_id, commit=False)

        # 1B: DEACTIVATION is bump-class — the seam kills existing JWTs in the
        # same commit and invalidates all cache entries with the fresh version.
        await self.invalidate_sessions(
            db_user,
            InvalidationReason.DEACTIVATION,
            tenant_id=get_user_attr(db_user, "tenant_id"),
            commit=True,
        )
        return {"message": f"User {get_user_attr(db_user, 'name')} {'activated' if is_active else 'deactivated'}"}

    async def downgrade_to_free_tier(self, user_id: int) -> int | None:
        """Downgrade a user to the configured free tier.

        Public seam for billing webhooks (subscription canceled/expired).
        Encapsulates free-tier lookup and the tier_id update so callers never
        import tiers/users crud internals directly. Also bumps token_version
        so cached rate-limit claims reflect the downgrade promptly.

        Args:
            user_id: ID of the user to downgrade.

        Returns:
            The new tier_id, or None if no free tier is configured.
        """
        from src.app.core.config import settings

        tier_svc = TierService(self.db)
        try:
            free_tier = await tier_svc.get_tier(settings.billing.DEFAULT_FREE_TIER_NAME)
        except ModelNotFoundError:
            logger.warning("Free tier not configured; skipping downgrade", user_id=user_id)
            return None

        free_tier_id = get_user_attr(free_tier, "id")

        await self.crud.update(
            db=self.db,
            object={"tier_id": free_tier_id},
            id=user_id,
            commit=False,
        )

        # 1B: honor the docstring contract — the seam bumps token_version so
        # existing JWTs (which embed tier claims for zero-DB rate limiting)
        # are invalidated promptly, with the fresh version passed through to
        # the cache. The tier_id update staged above commits atomically with
        # the seam's bump.
        await self.invalidate_sessions(
            user_id,
            InvalidationReason.TIER_CHANGE,
            commit=True,
        )
        return cast(int | None, free_tier_id)

    async def get_with_rate_limits(self, username: str) -> dict[str, Any]:
        """Get user with their tier's rate limits.

        Optimized to use eager loading - fetches User + Tier in single query.

        Parameters
        ----------
        username : str
            Username to look up

        Returns
        -------
        dict[str, Any]
            User data with tier_rate_limits

        Raises
        ------
        ModelNotFoundError
            If user not found
        """
        # Eager load User + Tier + RateLimits in single query
        # joinedload(User.tier).joinedload(Tier.rate_limits) ensures we fetch everything at once
        parsed_id = self._parse_user_id(username)
        if parsed_id is not None:
            stmt = (
                select(User)
                .options(joinedload(User.tier).joinedload(Tier.rate_limits))
                .where(User.id == parsed_id, User.is_deleted.is_(False))
            )
        else:
            stmt = (
                select(User)
                .options(joinedload(User.tier).joinedload(Tier.rate_limits))
                .where(User.username == username, User.is_deleted.is_(False))
            )
        # SECURITY FIX: Filter by tenant to prevent cross-tenant data exposure.
        # The /{id}/rate-limits route is superuser-only with no RequestContext
        # (superusers see all tenants), so the unscoped hatch stays here.
        tenant = get_current_tenant()
        scope = (
            ExplicitTenant(tenant_id=tenant.id, reason="legacy-criterion:User")
            if tenant is not None
            else Unscoped(reason="legacy-criterion:User")
        )
        result = await execute_scoped(stmt, db=self.db, model=User, scope=scope)
        db_user = result.unique().scalar_one_or_none()

        if db_user is None:
            raise ModelNotFoundError("User", username)

        # U7: Direct dict construction instead of Pydantic validate+dump
        # ORM data is already validated, skip redundant Pydantic overhead
        user_dict = {
            "id": db_user.id,
            "created_at": db_user.created_at,
            "updated_at": db_user.updated_at,
            "name": db_user.name,
            "username": db_user.username,
            "email": db_user.email,
            "profile_image_url": db_user.profile_image_url,
            "tier_id": db_user.tier_id,
        }

        if db_user.tier_id is None or db_user.tier is None:
            user_dict["tier_rate_limits"] = []
            return user_dict

        # Rate limits are already loaded via joinedload
        # U7: Direct dict construction for rate limits
        user_dict["tier_rate_limits"] = [
            {
                "id": rl.id,
                "tier_id": rl.tier_id,
                "name": rl.name,
                "path": rl.path,
                "limit": rl.limit,
                "period": rl.period,
            }
            for rl in db_user.tier.rate_limits
        ]

        return user_dict

    async def get_with_tier(self, username: str) -> dict[str, Any] | None:
        """Get user with tier information merged.

        Optimized to use single query via get_user_with_tier_eager.
        """
        parsed_id = self._parse_user_id(username)
        if parsed_id is not None:
            user_with_tier = await self.crud.get_user_with_tier_eager(db=self.db, id=parsed_id)
        else:
            user_with_tier = await self.crud.get_user_with_tier_eager(db=self.db, username=username)

        if not user_with_tier:
            raise ModelNotFoundError("User", username)

        # BUG FIX: Previously returned None if user has no tier, which is misleading
        # because the user DOES exist. Callers would confuse None with "user not found".
        # Now returns the user dict without tier_* keys when no tier is assigned.
        if not user_with_tier.get("tier"):
            return user_with_tier

        # Flatten structure to match expected output format
        user_dict = user_with_tier.copy()
        tier_data = user_dict.pop("tier")

        # Filter out SQLAlchemy internal state
        if "_sa_instance_state" in tier_data:
            del tier_data["_sa_instance_state"]

        for key, value in tier_data.items():
            user_dict[f"tier_{key}"] = value

        return user_dict


def get_user_service(db: AsyncSession) -> UserService:
    """Dependency injection factory for UserService.

    H-4: repository layer deleted — the service owns its data access via
    FastCRUD (``crud_users``) directly, same as every other module."""
    return UserService(db)
