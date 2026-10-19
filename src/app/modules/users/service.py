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
from src.app.core.security import get_password_hash
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
from src.app.shared.exceptions import DuplicateError, ModelNotFoundError

logger = get_logger(__name__)

from src.app.modules.users.cache import (
    UserCacheInvalidator,
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

    async def get_user_for_management(self, identifier: str) -> dict[str, Any] | None:
        """Privileged, deliberately UNSCOPED read for management flows.

        Public and named on purpose. This used to be ``_get_active_user``,
        a private method whose docstring told future readers "do not add new
        callers here" — a rule only a comment could enforce and an IDOR audit
        could not enumerate. It is now a named row on the read interface, so
        the scope it bypasses is visible to anyone reading the interface
        rather than buried in prose.

        Scope: these flows (update/delete, tier and status changes,
        hard-delete) run under ``USERS_MANAGE`` / superuser gates and may
        execute with no active tenant context, because superusers see all
        tenants. The tenant seam is fail-closed without context and would
        break them. Every other caller must use ``UserDirectory.active``,
        ``.member`` or ``.profile`` instead.

        Deliberately NOT a :class:`UserDirectory` read, unlike every other
        read on this module. The delete path reads ``token_version`` from the
        result to seed the post-delete version, and ``UserRead`` is an API
        projection that omits privilege/session fields by design
        (``UserReadPublic`` drops them, ``UserRead`` adds back only
        ``tenant_id`` and ``is_superuser``). Reading through the directory
        would validate the row into a model without ``token_version`` and
        silently seed the cache with a guess. So this returns the raw CRUD
        row, uncached, exactly as before — the only thing that changes here
        is that the scope is named instead of hidden.
        """
        parsed_id = self._parse_user_id(identifier)
        if parsed_id is not None:
            return await self.crud.get(db=self.db, id=parsed_id, is_deleted=False)
        return await self.crud.get(db=self.db, username=identifier, is_deleted=False)

    async def resolve_current_profile(self, current_user: Any) -> UserRead | dict[str, Any] | None:
        """Resolve the full profile for the authenticated principal.

        Returns the principal directly when it already carries profile fields
        (e.g. loaded via API key or DB fallback); otherwise loads via ID or
        username. Returns None when the principal is unresolvable.

        Returns the validated :class:`UserRead` MODEL, never its dump, on both
        paths that resolve one (FastAPI_saas-wsiy). The route declares
        ``response_model=UserReadPublic``, and a dumped dict makes that response
        model a SECOND validation of the same address: M-4 (FastAPI_saas-2h3n)
        measured two ``EmailStr`` validations per ``GET /users/me`` and a
        request-level p50 of 172.507 us, against the plan's 974 us claim. FastAPI
        serializes a response-model INSTANCE through a TypeAdapter built with
        ``from_attributes=True``, and pydantic v2 short-circuits an input that is
        already an instance of the model or a subclass instead of re-running field
        validation -- so handing it the model costs the response model nothing.
        Dropping ``EmailStr`` from ``UserReadPublic`` would save the same call and
        delete the guarantee; this removes only the redundant pass.

        The one validation that remains is the floor: a typed response has to be
        validated once. Oracle:
        ``tests/unit/modules/users/test_users_me_email_validated_once.py``.
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
                # validate into the real schema so downstream `.get()` access is
                # safe. FastAPI_saas-wsiy: the MODEL, not its dump -- a dict here
                # would be re-validated by the route's response model.
                try:
                    return UserRead.model_validate(current_user)
                except Exception as e:
                    logger.warning("Unserializable user principal in resolve_current_profile", error=str(e))
                    return None
        from src.app.modules.users.directory import UserDirectory

        try:
            # Built inside the load path only: a principal that already carries
            # its profile fields returns above, without needing a session.
            directory = UserDirectory(self.db)
            if user_id is not None:
                user = await directory.by_id(int(user_id))
                if user:
                    # FastAPI_saas-wsiy: the directory already returns a UserRead.
                    return user
            elif username:
                try:
                    return await directory.by_username(username)
                except ModelNotFoundError:
                    return None
        except (ValueError, TypeError):
            if username:
                try:
                    return await directory.by_username(username)
                except ModelNotFoundError:
                    return None
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
        db_user = await self.get_user_for_management(username)

        if db_user is None:
            raise ModelNotFoundError("User", username)
        db_username = get_user_attr(db_user, "username")
        db_email = get_user_attr(db_user, "email")
        # P2-C: cross-tenant ownership via the tenancy seam (soft-404 — a
        # cross-tenant username stays invisible instead of leaking existence).
        require_tenant_match(get_user_attr(db_user, "tenant_id"), resource="User", key=username)

        # Ownership check (superusers bypass).
        # Run-3 finding users.read_user.same_tenant_existence_oracle: this path
        # had the same resolve-then-403/404 split as GET /users/{user_id}. A
        # non-owner now gets the identical soft-404 a nonexistent user gets, so
        # the update endpoints cannot be used to confirm co-tenant existence.
        if db_username != current_user.username and not current_user.is_superuser:
            raise ModelNotFoundError("User", username)
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
    ) -> dict[str, str]:
        """Soft delete a user with ownership validation.

        Parameters
        ----------
        username : str
            Username of user to delete
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
        """
        db_user = await self.get_user_for_management(username)

        if not db_user:
            raise ModelNotFoundError("User", username)

        # P2-C: same-username users in other tenants stay invisible — without
        # this check a superuser from Tenant A could delete Tenant B's user.
        require_tenant_match(get_user_attr(db_user, "tenant_id"), resource="User", key=username)

        if username != current_user.username and not current_user.is_superuser:
            # Run-3: uniform soft-404 (see update_user) so DELETE cannot be
            # used to confirm that an identifier is a member of this tenant.
            raise ModelNotFoundError("User", username)

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

        # S-06 FIX: the previous `blacklist_token(token=token, ...)` here
        # blacklisted the CALLER's bearer token, not the deleted user's — so a
        # superuser deleting someone else revoked their OWN session on every
        # call, and the deleted user's tokens survived whenever their
        # token_version was not checked. Its comment claimed it prevented "a
        # deleted user [keeping] a valid token", which is not what it did.
        #
        # It was also redundant: `invalidate_sessions(..., DELETION, ...)`
        # below is the canonical seam and already bumps the TARGET's
        # token_version, which invalidates every one of that user's tokens.
        # The `token` parameter is gone for the same reason — nothing needed it.

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

    async def hard_delete(self, username: str) -> dict[str, str]:
        """Permanently delete a user from database (superuser only).

        Parameters
        ----------
        username : str
            Username of user to delete

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
        db_user = await self.get_user_for_management(username)

        if not db_user:
            raise ModelNotFoundError("User", username)

        # P2-C: cross-tenant hard-delete stays invisible (soft-404, same as
        # update/delete) so existence is never leaked via 403.
        require_tenant_match(get_user_attr(db_user, "tenant_id"), resource="User", key=username)

        # SECURITY FIX: Delete user BEFORE invalidating cache
        # Prevents stale cache if DB operation fails

        # S-06 FIX: the `blacklist_token(token=token, ...)` that used to sit
        # here blacklisted the CALLER's token, not the hard-deleted user's. The
        # target's own revocation is the `_invalidate_user_cache(...
        # DELETION, explicit_token_version=...)` block below, which seeds the
        # post-delete token_version so the target's tokens stop validating.

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
        db_user = await self.get_user_for_management(username)

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
        db_user = await self.get_user_for_management(username)
        if db_user is None:
            raise ModelNotFoundError("User", username)

        current_status = get_user_attr(db_user, "is_active")
        if bool(current_status) == is_active:
            return {"message": f"User {get_user_attr(db_user, 'name')} already {'active' if is_active else 'inactive'}"}

        user_id = get_user_attr(db_user, "id")
        update_payload: dict[str, Any] = {"is_active": is_active}
        if not is_active:
            # P0-2: Mark as email-verified so the verification service's
            # "already verified but inactive" guard fires for admin-disabled
            # accounts, even if the user never completed email verification.
            update_payload["is_email_verified"] = True
        await self.crud.update(db=self.db, object=update_payload, id=user_id, commit=False)

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
