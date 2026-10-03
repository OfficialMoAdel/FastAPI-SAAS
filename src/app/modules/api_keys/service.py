"""API Key service layer - business logic for API key management."""

import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.events import event_bus
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger

logger = get_logger(__name__)
from src.app.core.cache.client import cache_client
from src.app.core.db.scoped import execute_scoped
from src.app.core.tenancy.scope import ExplicitTenant
from src.app.modules._base.service import BaseService
from src.app.modules.api_keys.models import APIKey
from src.app.modules.api_keys.schemas import APIKeyCreate, APIKeyRead, APIKeyResponse
from src.app.modules.audit_logs import audit
from src.app.shared.exceptions import DuplicateError, ForbiddenError, ModelNotFoundError

MAX_GRACE_PERIOD_DAYS: int = 90
__all__ = ["APIKeyService", "MAX_GRACE_PERIOD_DAYS"]
# Re-exported for test backward compatibility
# (TODO: REMOVE once tests/unit/modules/api_keys no longer patch this module attribute)
event_bus = event_bus

# Cache configuration for API key validation
API_KEY_CACHE_PREFIX = "api_key"
API_KEY_CACHE_TTL = 300  # 5 minutes


class APIKeyService(BaseService[APIKey, APIKeyRead]):
    """Service class for API key-related business logic."""

    KEY_PREFIX_LENGTH = 8
    KEY_LENGTH = 32  # 256 bits of entropy

    def __init__(self, db: AsyncSession):
        """Initialize APIKeyService with database session.

        Parameters
        ----------
        db : AsyncSession
            Database session
        """
        super().__init__(APIKey, db, select_schema=APIKeyRead)

    def _generate_key(self) -> tuple[str, str, str]:
        """Generate a new API key.

        Returns
        -------
            Tuple of (full_key, prefix, hashed_key)
        """
        # Generate a secure random key
        raw_key = secrets.token_urlsafe(self.KEY_LENGTH)
        prefix = raw_key[: self.KEY_PREFIX_LENGTH]

        # Hash the key for storage (I2: peppered HMAC via the token seam)
        from src.app.core.security.tokens import hash_token as _ht

        hashed = _ht(raw_key)

        return raw_key, prefix, hashed

    def _hash_key(self, key: str) -> str:
        """Hash an API key for comparison (I2: peppered HMAC via the token seam)."""
        from src.app.core.security.tokens import hash_token as _ht

        return _ht(key)

    async def create_api_key(
        self,
        api_key_data: APIKeyCreate,
        user_id: int,
        tenant_id: UUID,
    ) -> APIKeyResponse:
        """Create a new API key for a user.

        Parameters
        ----------
        api_key_data : APIKeyCreate
            API key creation data
        user_id : int
            ID of the user creating the key
        tenant_id : int
            ID of the tenant

        Returns
        -------
        APIKeyResponse
            Created API key with the actual key (only shown once)
        """
        # SECURITY FIX: Prevent duplicate API key names within the same tenant
        from sqlalchemy import select

        stmt = select(APIKey).where(APIKey.name == api_key_data.name)
        existing_result = await execute_scoped(
            stmt, db=self.db, model=APIKey,
            scope=ExplicitTenant(tenant_id, reason='legacy-criterion:APIKey'),
        )
        if existing_result.scalar_one_or_none():
            raise DuplicateError("name", api_key_data.name)

        # Generate the key
        full_key, prefix, hashed_key = self._generate_key()

        # Prepare scopes as JSON string if provided
        scopes_json: str | None = None
        if api_key_data.scopes:
            scopes_result = json_dumps(api_key_data.scopes, as_str=True)
            scopes_json = scopes_result if isinstance(scopes_result, str) else scopes_result.decode("utf-8")

        # Create the API key record using SQLAlchemy model directly
        key_record = APIKey(
            name=api_key_data.name,
            key_prefix=prefix,
            hashed_key=hashed_key,
            user_id=user_id,
            tenant_id=tenant_id,
            scopes=scopes_json,
            expires_at=api_key_data.expires_at,
            is_active=True,
        )
        self.db.add(key_record)
        try:
            await self.db.commit()
        except IntegrityError:
            # G-1: concurrent duplicate-name race — the SELECT pre-check
            # above passed on both sides. Same domain duplicate as the
            # pre-check (409 via the table) so the contract is identical
            # with or without the race.
            await self.db.rollback()
            raise DuplicateError("name", api_key_data.name) from None
        await self.db.refresh(key_record)

        # Audit log the creation
        await audit(
            action="api_key.created",
            entity_type="api_key",
            entity_id=key_record.id,
            tenant_id=tenant_id,
            user_id=user_id,
            details={
                "key_name": key_record.name,
                "key_prefix": key_record.key_prefix,
                "expires_at": key_record.expires_at.isoformat() if key_record.expires_at else None,
                "scopes": api_key_data.scopes,
            },
        )

        logger.info(
            f"API key created | user={user_id} | tenant={tenant_id} | "
            f"key_id={key_record.id} | key_name={key_record.name}"
        )

        return APIKeyResponse(
            id=key_record.id,
            name=key_record.name,
            key=full_key,  # Only returned on creation!
            key_prefix=prefix,
            expires_at=key_record.expires_at,
            created_at=key_record.created_at,
        )

    async def list_api_keys(
        self,
        user_id: int,
        tenant_id: UUID,
        page: int = 1,
        items_per_page: int = 10,
    ) -> dict[str, Any]:
        """List API keys for a user.

        Parameters
        ----------
        user_id : int
            ID of the user
        tenant_id : int
            ID of the tenant
        page : int
            Page number
        items_per_page : int
            Items per page

        Returns
        -------
        dict
            Paginated list of API keys (without actual keys)
        """
        return await self.get_page_with_total(
            user_id=user_id,
            tenant_id=tenant_id,
            offset=(page - 1) * items_per_page,
            limit=items_per_page,
            schema_to_select=APIKeyRead,  # type: ignore[arg-type]
        )

    async def revoke_api_key(
        self,
        key_id: int,
        user_id: int,
        tenant_id: UUID,
    ) -> dict[str, str]:
        """Revoke an API key.

        Parameters
        ----------
        key_id : int
            ID of the API key to revoke
        user_id : int
            ID of the user making the request
        tenant_id : int
            ID of the tenant

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If key not found
        ForbiddenError
            If user doesn't own the key
        """
        from sqlalchemy import select

        # T7 FIX: fetch inside the tenant/user scope so a foreign key id is
        # indistinguishable from a missing one. The previous unscoped lookup +
        # post-hoc ownership check returned 403 for existing foreign keys,
        # giving callers an existence oracle over other tenants' key ids.
        key_stmt = select(APIKey).where(
            APIKey.id == key_id,
            APIKey.user_id == user_id,
        )
        key_result = await execute_scoped(
            key_stmt, db=self.db, model=APIKey,
            scope=ExplicitTenant(tenant_id, reason='legacy-criterion:APIKey'),
        )
        key = key_result.scalar_one_or_none()

        if key is None:
            raise ModelNotFoundError("APIKey", key_id)

        api_key_hashed = key.hashed_key
        api_key_name = key.name
        api_key_prefix = key.key_prefix
        api_key_is_active = key.is_active

        # S-01 FIX (3/3): evict the cache AFTER the revoke commits, not before.
        # Two reasons:
        #   * a failed update must not clear the cache of a key that is still
        #     live (the old order left a revoked-in-cache / active-in-DB split);
        #   * a concurrent validate between the delete and the commit could
        #     re-populate the entry from the still-active row, re-arming the
        #     exact window this finding is about.
        await self.crud.update(
            db=self.db,
            id=key_id,
            object={"is_active": False},
        )

        # The adapter is None-safe: noop when Redis is down.
        #
        # A TOMBSTONE, not a delete. Deleting the key looks correct but is not:
        # the cached payload IS the authentication grant, and nothing else in
        # the write path carries revocation state, so a plain delete leaves the
        # read-side `is_active is False` guard with nothing to match and the
        # revoked key authenticating until its TTL expires. The tombstone gives
        # that guard something to reject on.
        if api_key_hashed:
            tombstone = json_dumps({"is_active": False, "revoked": True}, as_str=True)
            await cache_client.set(self._get_cache_key(api_key_hashed), tombstone, ttl=API_KEY_CACHE_TTL)

        # Audit log the revocation
        await audit(
            action="api_key.revoked",
            entity_type="api_key",
            entity_id=key_id,
            tenant_id=tenant_id,
            user_id=user_id,
            details={
                "key_name": api_key_name,
                "key_prefix": api_key_prefix,
                "was_active": api_key_is_active,
            },
        )

        logger.info(
            f"API key revoked | user={user_id} | tenant={tenant_id} | key_id={key_id} | key_name={api_key_name}"
        )

        return {"message": "API key revoked successfully"}

    def _get_cache_key(self, api_key_hash: str) -> str:
        """Get Redis cache key for an API key hash.

        Uses full hash to prevent any theoretical collision risk.
        """
        # S7 FIX: Use full hash instead of truncating to 16 chars
        return f"{API_KEY_CACHE_PREFIX}:{api_key_hash}"

    async def _get_cached_tenant_active(self, tenant_id: Any) -> bool | None:
        """Probe the shared L2 tenant cache for liveness (T8).

        Returns True when the cached tenant is active, False when it is
        suspended/deleted, and None when no cache entry exists (or probing
        fails) so the caller falls back to the authoritative DB check.

        K-R1-03 PERF (not correctness — a miss is always safe, only slow):
        the request path writes ONE tenant L2 key, ``tenant:slug:{slug}``
        (the startup warmer and the middleware resolver's L3 fill both go
        through ``tenancy.resolver.slug_cache_key``), while the
        id-keyed ``tenant:{id}`` entry is written only by
        ``TenantService._cache_tenant``. Probing the id key alone therefore
        missed on every request for a tenant the tenants service had never
        touched, paying a full DB validation (measured 0/20000 hits). So the
        probe now reads the writer's key first, from the current tenant
        context, and falls back to the id key.

        The slug entry is only trusted when its ``id`` matches the tenant
        the API key is bound to — a slug rename can leave an entry under the
        old slug. On any mismatch the probe returns None, i.e. the
        fail-safe DB path.
        """
        if not tenant_id:
            return None
        try:
            from src.app.core.tenancy.cache import get_tenant_cache_key
            from src.app.core.tenancy.context import NO_TENANT, get_current_tenant
            from src.app.core.tenancy.resolver import slug_cache_key

            ctx = get_current_tenant()
            # Probe the writer's key first. The context's slug belongs to
            # the request already; the id check below binds it to the key.
            keys: list[str] = []
            if ctx is not None and ctx is not NO_TENANT and ctx.tenant_slug:
                keys.append(slug_cache_key(ctx.tenant_slug))
            id_key = get_tenant_cache_key(tenant_id)
            if id_key not in keys:
                keys.append(id_key)

            for probe_key in keys:
                raw = await cache_client.get(probe_key)
                if raw is None:
                    continue
                data = json_loads(raw)
                if not isinstance(data, dict) or "is_active" not in data:
                    continue
                cached_id = data.get("id")
                if cached_id is not None and str(cached_id) != str(tenant_id):
                    # Stale entry from a slug rename — not this tenant.
                    logger.debug(
                        "tenant_liveness_probe_key_mismatch",
                        cache_key=probe_key,
                        cached_tenant_id=str(cached_id),
                    )
                    continue
                is_active = bool(data["is_active"])
                logger.info(
                    f"API key tenant liveness probe {'HIT' if is_active else 'MISS(inactive)'} | "
                    f"tenant_id={tenant_id} | key={probe_key}"
                )
                return is_active

            logger.info(
                f"API key tenant liveness probe MISS | tenant_id={tenant_id} | "
                f"keys={','.join(keys)}"
            )
            return None
        except Exception as e:  # noqa: BLE001 - probe must never break validation
            logger.debug("tenant_liveness_probe_failed", error=str(e))
            return None

    async def validate_api_key(
        self,
        api_key: str,
    ) -> dict[str, Any] | None:
        """Validate an API key and return the associated user info.

        Security: Verifies tenant is active to prevent suspended tenant access.
        Performance: Uses Redis caching with 5-minute TTL to reduce DB load.

        Parameters
        ----------
        api_key : str
            The API key to validate

        Returns
        -------
        dict | None
            User info if valid, None otherwise
        """
        # I2 bridge: the stored digest changed from bare SHA-256 to peppered
        # HMAC, so keys created before that change are invisible to a
        # single-digest lookup — the row never matches and the key silently
        # stops authenticating. Try both digests, re-hash a legacy row so
        # it migrates forward on first use, and cache under BOTH keys so
        # the next lookup hits the cache whichever digest ran.
        from src.app.core.security.tokens import token_digest_candidates

        current_digest, legacy_digest = token_digest_candidates(api_key)
        cache_key = self._get_cache_key(current_digest)
        legacy_cache_key = self._get_cache_key(legacy_digest)

        # Check cache first (O(1) lookup; adapter is None-safe)
        for candidate_cache_key in (cache_key, legacy_cache_key):
            cached = await cache_client.get(candidate_cache_key)
            if cached is None:
                continue
            result = json_loads(cached)
            # A REVOKED KEY IS CACHED AS A TOMBSTONE, not deleted.
            #
            # The previous version deleted the entry on revoke and expected
            # this guard to catch anything that slipped through. It could not:
            # the only writer of this payload hard-coded ``is_active: True``,
            # so `is_active is False` was unreachable and a revoked key kept
            # authenticating for the full API_KEY_CACHE_TTL (300s).
            # ``revoke`` now writes ``{"is_active": false, ...}`` here, which
            # makes this branch the thing it was written to be.
            #
            # A MISSING `is_active` (an entry written by an older deploy) is
            # treated as active so it keeps working; those all carry a 300s
            # TTL, so the exposure is bounded by one TTL from deploy.
            #
            # Residual race, stated rather than papered over: a validate that
            # read the row as active microseconds BEFORE the revoke committed
            # can still write its grant over this tombstone afterwards. That
            # window is sub-millisecond and needs a lock to close; the
            # 300-second window this fixes was not.
            if result.get("is_active") is False:
                # Stale grant for a revoked key — drop it and refuse.
                await cache_client.delete(candidate_cache_key)
                return None
            # Check expiration on cached data
            if result.get("expires_at"):
                expires_at = datetime.fromisoformat(result["expires_at"])
                if expires_at < datetime.now(UTC):
                    # Expired - delete from cache and return None
                    await cache_client.delete(candidate_cache_key)
                    return None
            # BUG FIX: Also check valid_from. Without this, a rotated key that was
            # cached before its valid_from time could be used prematurely (up to 5 min).
            if result.get("valid_from"):
                valid_from = datetime.fromisoformat(result["valid_from"])
                if valid_from > datetime.now(UTC):
                    return None
            # T8 FIX: cached key entries previously outlived tenant/key
            # suspension because nothing evicted them. Probe the shared L2
            # tenant cache: False => suspended (reject); missing => the
            # tenant entry was invalidated, so distrust the key cache and
            # take the authoritative DB path below.
            tenant_state = await self._get_cached_tenant_active(result["tenant_id"])
            if tenant_state is False:
                return None
            if tenant_state is None:
                break  # fall through to DB validation
            return {
                "user_id": result["user_id"],
                "tenant_id": result["tenant_id"],
                "scopes": result["scopes"],
                "key_id": result["key_id"],
            }

        # Cache miss - query database under BOTH digests
        result = await self._validate_from_db(current_digest, legacy_digest)
        if result is None:
            return None

        if result.pop("_legacy_digest", False):
            # Migrate the stored digest forward so future lookups hit the
            # current single-digest path. Both cache keys are written below.
            await self.crud.update(
                db=self.db, id=result["key_id"], object={"hashed_key": current_digest}, commit=False
            )

        # Cache the result (adapter noops when Redis is down).
        #
        # S-01 FIX (1/2): only the PRIMARY cache key is written now.
        # Both were written before, under the same payload, and the read loop
        # above checks `cache_key` FIRST — so the legacy entry was never needed
        # to serve a hit; it was an orphan. Worse, it is unreclaimable: the row
        # is migrated to the HMAC digest on first use, so a later revoke can
        # only compute the cache key from the stored HMAC and could never
        # delete the SHA-256 entry, which then authenticated for the full TTL.
        #
        # The legacy DB lookup is untouched — `_validate_from_db` still tries
        # both digests, so pre-I2 rows still resolve and migrate forward.
        cache_data = {
            "user_id": result["user_id"],
            "tenant_id": result["tenant_id"],
            "scopes": result["scopes"],
            "key_id": result["key_id"],
            "expires_at": result["expires_at"].isoformat() if result["expires_at"] else None,
            # BUG FIX: Cache valid_from so it can be enforced on cache hits too.
            "valid_from": result["valid_from"].isoformat() if result.get("valid_from") else None,
            # S-01 FIX (2/2): record that this grant is for an ACTIVE key.
            # `_validate_from_db` filters `APIKey.is_active.is_(True)`, so an
            # entry is only ever written for a live key; stating it lets the hit
            # path above detect a revoke that raced the write.
            "is_active": True,
        }
        payload = json_dumps(cache_data, as_str=True)
        await cache_client.set(cache_key, payload, ttl=API_KEY_CACHE_TTL)

        return {
            "user_id": result["user_id"],
            "tenant_id": result["tenant_id"],
            "scopes": result["scopes"],
            "key_id": result["key_id"],
        }

    async def _validate_from_db(
        self, current_digest: str, legacy_digest: str | None = None
    ) -> dict[str, Any] | None:
        """Validate API key from database.

        I2 bridge: accepts the legacy digest too, so keys stored under the
        pre-pepper bare SHA-256 still resolve. A legacy match sets the
        ``_legacy_digest`` flag so the caller re-hashes the row forward.

        Parameters
        ----------
        current_digest: Peppered HMAC digest (the write format).
        legacy_digest: Pre-I2 bare SHA-256 digest, tried only when the
            current digest has no row.

        Returns
        -------
        dict | None
            Key info if valid, None otherwise. Carries an internal
            ``_legacy_digest`` marker for the caller's re-hash step.
        """
        from sqlalchemy import select

        from src.app.modules.api_keys.models import APIKey
        from src.app.modules.tenants.models import Tenant

        # JOIN with tenant to check tenant.is_active
        digests = (current_digest,) if legacy_digest is None else (current_digest, legacy_digest)
        stmt = (
            select(APIKey)
            .join(Tenant, APIKey.tenant_id == Tenant.id)
            .where(
                APIKey.hashed_key.in_(digests),
                APIKey.is_active.is_(True),
                Tenant.is_active.is_(True),  # Prevent suspended tenant access
            )
        )

        result = await self.db.execute(stmt)
        key = result.scalar_one_or_none()

        if not key:
            return None

        is_legacy = bool(key.hashed_key == legacy_digest and legacy_digest is not None)

        # Check valid_from (grace period rotation support)
        # Key is not valid before this timestamp
        if key.valid_from and key.valid_from > datetime.now(UTC):
            return None

        # Check expiration
        if key.expires_at and key.expires_at < datetime.now(UTC):
            return None

        # Update last_used_at without committing — let the request lifecycle handle commit.
        # SECURITY FIX (C4): Previously committed here on the shared request-scoped session,
        # which could flush unrelated pending changes from other parts of the request.
        try:
            await self.crud.update(
                db=self.db,
                id=key.id,
                object={"last_used_at": datetime.now(UTC)},
                commit=False,
            )
        except Exception as e:
            logger.warning("failed_to_update_api_key_last_used", key_id=key.id, error=str(e))

        return {
            "user_id": key.user_id,
            "tenant_id": key.tenant_id,
            "scopes": json_loads(key.scopes) if key.scopes else [],
            "key_id": key.id,
            "expires_at": key.expires_at,
            # Include valid_from so cache can enforce it on hits
            "valid_from": key.valid_from if hasattr(key, "valid_from") else None,
            # I2 bridge: tells the caller to re-hash this row forward.
            "_legacy_digest": is_legacy,
        }

    async def rotate_api_key(
        self,
        key_id: int,
        user_id: int,
        tenant_id: UUID,
        grace_period_days: int = 7,
    ) -> APIKeyResponse:
        """Rotate an API key with grace period for zero-downtime rotation.

        Flow:
        1. Create new key immediately with valid_from set to future timestamp
        2. Old key remains valid for grace_period_days
        3. New key becomes valid after grace period expires
        4. Both keys work during grace period (seamless transition)

        Security: Revoking the old key automatically invalidates it.
        Users should update their applications within the grace period.

        Parameters
        ----------
        key_id : int
            ID of the API key to rotate
        user_id : int
            ID of the user making the request
        tenant_id : int
            ID of the tenant
        grace_period_days : int
            Days before new key becomes valid (default: 7)

        Returns
        -------
        APIKeyResponse
            New API key (only shown once)

        Raises
        ------
        ModelNotFoundError
            If key not found
        ForbiddenError
            If user doesn't own the key
        """
        from datetime import timedelta

        from sqlalchemy import select

        # Get the existing key
        # Scope lookup to tenant and user to eliminate existence oracle (K-R1-01)
        old_key_stmt = select(APIKey).where(
            APIKey.id == key_id,
            APIKey.user_id == user_id,
        )
        old_key_result = await execute_scoped(
            old_key_stmt, db=self.db, model=APIKey,
            scope=ExplicitTenant(tenant_id, reason='legacy-criterion:APIKey'),
        )
        old_key = old_key_result.scalar_one_or_none()

        if old_key is None:
            raise ModelNotFoundError("APIKey", key_id)

        # Item 8: the scoped lookup above (tenant + user filter) already
        # 404s cross-tenant/cross-user keys — the old explicit ownership
        # check was unreachable dead code (K-R1-01 oracle removal made it
        # so). Ownership of the found row holds by construction.
        if not old_key.is_active:
            raise ForbiddenError("Cannot rotate a revoked key")

        api_key = old_key
        api_key_name = api_key.name
        api_key_scopes = api_key.scopes
        api_key_expires_at = api_key.expires_at
        api_key_id = api_key.id
        api_key_prefix = api_key.key_prefix

        # BUG FIX: The old logic set valid_from=now+grace on the NEW key, making it unusable
        # during the grace period and never expired the OLD key, leaving it valid indefinitely.
        # Correct behaviour:
        #   - New key: valid immediately (valid_from=None)
        #   - Old key: expires_at = min(now + grace_period, original_expires_at)  (auto-expires after grace window)
        capped_grace_days = min(max(0, grace_period_days), MAX_GRACE_PERIOD_DAYS)
        grace_expires_at = datetime.now(UTC) + timedelta(days=capped_grace_days)
        if api_key_expires_at and isinstance(api_key_expires_at, datetime) and api_key_expires_at < grace_expires_at:
            grace_expires_at = api_key_expires_at

        # Generate the new key
        full_key, prefix, hashed_key = self._generate_key()

        # Create new key record — valid immediately (no valid_from restriction)
        import re

        # Prevent unbounded name growth on repeated rotations (K-R1-02)
        base_name = re.sub(r" \(Rotated.*\)$", "", api_key_name)
        new_name = f"{base_name[:80]} (Rotated)"

        # K-R1-02: the rotated name is derived deterministically from the
        # base name, so rotating a key that was itself produced by a
        # rotation derives the SAME name and collides with the row already
        # in the table — ``uq_api_key_name_tenant``. Pre-check mirrors
        # ``create_api_key``'s pre-check so the caller gets the same 409
        # instead of a 500.
        name_taken = await execute_scoped(
            select(APIKey).where(APIKey.name == new_name),
            db=self.db, model=APIKey,
            scope=ExplicitTenant(tenant_id, reason='legacy-criterion:APIKey'),
        )
        if name_taken.scalar_one_or_none() is not None:
            logger.warning(
                f"API key rotation name collision | user={user_id} | tenant={tenant_id} | "
                f"old_key_id={api_key_id} | new_name={new_name}"
            )
            raise DuplicateError("name", new_name)
        new_key_record = APIKey(
            name=new_name,
            key_prefix=prefix,
            hashed_key=hashed_key,
            user_id=user_id,
            tenant_id=tenant_id,
            scopes=api_key_scopes,  # Copy scopes as JSON string
            expires_at=api_key_expires_at,  # Copy original expiration
            is_active=True,
            valid_from=None,  # Active immediately
        )
        self.db.add(new_key_record)

        # Mark old key to expire after the grace period (atomic update in same transaction)
        await self.crud.update(
            db=self.db,
            id=api_key_id,
            object={"expires_at": grace_expires_at},
            commit=False,
        )

        try:
            await self.db.commit()
        except IntegrityError:
            # Same domain duplicate as the pre-check above, reached via the
            # concurrent-insert race (the SELECT passed on both sides) —
            # identical contract with or without the race, exactly as in
            # ``create_api_key``.
            await self.db.rollback()
            logger.warning(
                f"API key rotation integrity error (name race) | user={user_id} | "
                f"tenant={tenant_id} | old_key_id={api_key_id} | new_name={new_name}"
            )
            raise DuplicateError("name", new_name) from None
        await self.db.refresh(new_key_record)

        old_key_hashed = api_key.hashed_key
        if old_key_hashed:
            await cache_client.delete(self._get_cache_key(old_key_hashed))

        # Audit log the rotation
        await audit(
            action="api_key.rotated",
            entity_type="api_key",
            entity_id=new_key_record.id,
            tenant_id=tenant_id,
            user_id=user_id,
            details={
                "old_key_id": api_key_id,
                "old_key_name": api_key_name,
                "old_key_prefix": api_key_prefix,
                "new_key_id": new_key_record.id,
                "new_key_name": new_key_record.name,
                "new_key_prefix": new_key_record.key_prefix,
                "grace_period_days": grace_period_days,
                "old_key_expires_at": grace_expires_at.isoformat(),
            },
        )

        logger.info(
            f"API key rotated | user={user_id} | tenant={tenant_id} | "
            f"old_key_id={api_key_id} | new_key_id={new_key_record.id} | "
            f"grace_period={grace_period_days}d | old_key_expires_at={grace_expires_at.isoformat()}"
        )

        return APIKeyResponse(
            id=new_key_record.id,
            name=new_key_record.name,
            key=full_key,  # Only returned on creation!
            key_prefix=prefix,
            expires_at=new_key_record.expires_at,
            created_at=new_key_record.created_at,
        )


def get_api_key_service(db: AsyncSession) -> APIKeyService:
    """Dependency injection factory for APIKeyService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db

    Returns
    -------
    APIKeyService
        Service instance with database session
    """
    return APIKeyService(db)
