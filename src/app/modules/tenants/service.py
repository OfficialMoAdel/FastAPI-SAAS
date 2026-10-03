"""Tenant service layer - encapsulates all tenant business logic."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.cache.client import cache_client
from src.app.core.cache.invalidation_exhaustion import (
    ExhaustionMarker,
    InvalidationPolicy,
    invalidate_with_exhaustion,
)
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.tenancy.cache import TENANT_CACHE_TTL, get_tenant_cache_key
from src.app.core.tenancy.resolver import negative_cache_key, slug_cache_key
from src.app.core.utils.cache_guard import cache_guard
from src.app.modules._base.service import BaseService
from src.app.modules.tenants.crud import crud_tenants
from src.app.modules.tenants.models import Tenant
from src.app.modules.tenants.schemas import TenantCreate, TenantResponse, TenantUpdate
from src.app.shared.exceptions import DuplicateError, ModelNotFoundError

logger = get_logger(__name__)

#: The tenant key family's DECLARED exhaustion policy. A stale ``is_active``
#: keeps serving a suspended tenant, so exhaustion leaves a poison-key marker
#: for the (parked, dashboard-side) reader. Flipping the family to log-only is
#: a one-line change here.
_TENANT_INVALIDATION_POLICY = InvalidationPolicy(
    family="Tenant",
    on_exhaustion=ExhaustionMarker.POISON_KEY,
    exhausted_message="Tenant cache invalidation failed after retry — stale is_active may persist until TTL",
)


def _tenant_to_dict(tenant: Tenant) -> dict[str, Any]:
    """Convert Tenant ORM object to response dict directly.

    P2-1: Bypasses Pydantic model construction for ~3x faster serialization.
    Fields match TenantResponse schema: id, created_at, updated_at, name, slug, is_active.
    """
    return {
        "id": tenant.id,
        "created_at": tenant.created_at,
        "updated_at": tenant.updated_at,
        "name": tenant.name,
        "slug": tenant.slug,
        "is_active": tenant.is_active,
        "feature_flags": tenant.feature_flags if hasattr(tenant, "feature_flags") and tenant.feature_flags else {},
        "storage_usage_bytes": getattr(tenant, "storage_usage_bytes", 0),
        "file_count": getattr(tenant, "file_count", 0),
    }


def _get_slug_cache_key(slug: str) -> str:
    """Generate Redis cache key for tenant slug lookup.

    Thin wrapper over the canonical resolver seam
    (:func:`src.app.core.tenancy.resolver.slug_cache_key`) kept for
    backward compatibility with existing importers.
    """
    return slug_cache_key(slug)


class TenantService(BaseService[Tenant]):
    """Service class for tenant-related business logic."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize TenantService with database session.

        Parameters
        ----------
        db : AsyncSession
            Database session
        """
        super().__init__(Tenant, db, select_schema=TenantResponse)
        self.crud = crud_tenants

    async def _cache_tenant(self, tenant: Tenant | dict[str, Any]) -> None:
        """Cache tenant data in Redis (both ID and slug keys).

        M-7: single dict-normalizing helper — accepts an ORM Tenant (converted
        via ``_tenant_to_dict``) or an already-serialized dict (FastCRUD
        ``schema_to_select`` path). Replaces the near-duplicate
        ``_cache_tenant_dict``.

        Writes go through the CacheClient seam (None-safe: noop when Redis
        is down).
        """
        tenant_data = _tenant_to_dict(tenant) if isinstance(tenant, Tenant) else tenant
        if not tenant_data:
            return

        tenant_id = tenant_data.get("id")
        if not tenant_id:
            return

        with cache_guard("tenant_write", tenant_id=str(tenant_id)):
            payload = json_dumps(tenant_data, as_str=True)
            cache_key = get_tenant_cache_key(tenant_id)
            await cache_client.set(cache_key, payload, ttl=TENANT_CACHE_TTL)

            slug = tenant_data.get("slug")
            if slug:
                await cache_client.set(_get_slug_cache_key(slug), payload, ttl=TENANT_CACHE_TTL)

    async def _invalidate_tenant_cache(self, tenant_id: UUID, slug: str | None = None) -> None:
        """Invalidate tenant cache in Redis and every worker's L1 cache.

        T9 FIX: the Redis deletes above only clear the shared L2 cache; each
        process keeps an in-memory TTLCache that previously served stale
        is_active=True for up to 60s. Publishing on the middleware's
        invalidation channel drops those L1 entries immediately.

        Q-M8: invalidation failure is NOT a benign cache miss — a stale
        ``is_active=True`` survives and keeps serving a suspended tenant.
        Failures are therefore logged at warning (recoverable after retry)
        / error (exhausted retries) and retried exactly once before giving
        up, leaving a poison-key marker so a later reader/ops sweep can
        detect the tenant whose cache could not be invalidated. That retry /
        visibility / marker policy is NOT written here: it is the shared
        exhaustion policy in ``core.cache.invalidation_exhaustion``, and the
        marker decision is declared on this family's policy row below.

        Deletes go through the CacheClient seam.

        S-15 FIX: the L1 drop used to sit BELOW a
        ``if cache_client.client is None: return`` guard, so it never ran when
        Redis was absent — which is exactly the scenario its own docstring
        describes ("each process keeps an in-memory TTLCache that previously
        served stale is_active=True for up to 60s"). During a Redis outage a
        deactivated tenant stayed active in every worker for the full 60s L1
        TTL, with no log and no other invalidation path.

        The local drop is now unconditional and runs FIRST: it touches only
        process memory, so it costs nothing and needs no Redis. Only the two
        Redis-dependent legs (L2 deletes, cross-worker publish) are skipped
        when there is no client.
        """
        # Local L1 first — unconditional, no Redis required.
        try:
            from src.app.core.tenancy.cache import invalidate_tenant_l1, publish_tenant_l1_invalidation

            # Local process first (publish may not be echoed to self), then fan out.
            invalidate_tenant_l1(slug)
        except Exception as e:  # noqa: BLE001 - cleanup is best-effort
            logger.warning(
                "Tenant L1 local invalidation failed — stale is_active may persist in this worker",
                tenant_id=str(tenant_id),
                slug=slug,
                error=str(e),
            )

        if cache_client.client is None:
            # No Redis: the L2 deletes and the cross-worker publish cannot run.
            # The L1 drop above already covered THIS process; other workers
            # keep their stale entry until their own 60s TTL expires, which is
            # the documented bounded-staleness trade for a Redis outage.
            logger.warning(
                "Tenant cache invalidation degraded: Redis unavailable; "
                "only this process's L1 entry was dropped. Other workers keep "
                "a stale is_active until their L1 TTL expires.",
                tenant_id=str(tenant_id),
                slug=slug,
            )
            return

        poison_key = f"tenant:invalidation_failed:{tenant_id}"

        async def _invalidate_redis() -> None:
            await cache_client.delete(get_tenant_cache_key(tenant_id))
            if slug:
                await cache_client.delete(_get_slug_cache_key(slug))

        async def _write_poison_marker() -> None:
            # TENANT_CACHE_TTL bounds the marker exactly as it bounds the
            # stale entry (ADR-0004: this card changed failure policy only).
            await cache_client.set(poison_key, slug or "", ttl=TENANT_CACHE_TTL)

        await invalidate_with_exhaustion(
            _invalidate_redis,
            policy=_TENANT_INVALIDATION_POLICY,
            log_context={"tenant_id": str(tenant_id), "slug": slug, "poison_key": poison_key},
            write_marker=_write_poison_marker,
        )

        try:
            from src.app.core.tenancy.cache import publish_tenant_l1_invalidation

            await publish_tenant_l1_invalidation(slug)
        except Exception as e:
            # Q-M8: L1 propagation failure means OTHER workers keep serving
            # stale is_active for up to the L1 TTL — warn with identifiers.
            logger.warning(
                "Tenant L1 invalidation propagation failed — stale is_active may persist on other workers",
                tenant_id=str(tenant_id),
                slug=slug,
                error=str(e),
            )

    async def _get_cached_tenant(self, tenant_id: UUID) -> dict[str, Any] | None:
        """Get tenant from cache if available."""
        with cache_guard("tenant_get", tenant_id=str(tenant_id)):
            cached = await cache_client.get(get_tenant_cache_key(tenant_id))
            if cached:
                result: dict[str, Any] = json_loads(cached)
                return result
        return None

    async def _get_cached_tenant_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Get tenant by slug from cache if available."""
        with cache_guard("tenant_get_by_slug", slug=slug):
            slug_key = _get_slug_cache_key(slug)
            cached = await cache_client.get(slug_key)

            if cached:
                try:
                    result: dict[str, Any] = json_loads(cached)
                    if isinstance(result, dict) and "id" in result:
                        return result
                except Exception:
                    pass

                # Fallback for old cache entries (UUID string)
                cached_full = await cache_client.get(get_tenant_cache_key(str(cached)))
                if cached_full:
                    result_fallback: dict[str, Any] = json_loads(cached_full)
                    return result_fallback
        return None

    async def create_tenant(self, tenant: TenantCreate) -> dict[str, Any]:
        """Create a new tenant with validation.

        Parameters
        ----------
        tenant : TenantCreate
            Tenant creation data

        Returns
        -------
        dict[str, Any]
            Created tenant data

        Raises
        ------
        DuplicateError
            If slug already exists
        """
        # Normalize slug to ensure strict database integrity
        if tenant.slug:
            tenant.slug = tenant.slug.lower().strip()

        slug_exists = await self.crud.exists(db=self.db, slug=tenant.slug)
        if slug_exists:
            logger.warning(
                "tenant_creation_failed_duplicate_slug",
                slug=tenant.slug,
            )
            raise DuplicateError("slug", tenant.slug)

        try:
            created_tenant = await self.crud.create(
                db=self.db,
                object=tenant,
            )
        except IntegrityError:
            logger.warning(
                "tenant_creation_failed_race_duplicate_slug",
                slug=tenant.slug,
            )
            raise DuplicateError("slug", tenant.slug)

        # P2-5: Always use optimized dict construction
        result = _tenant_to_dict(created_tenant)  # type: ignore[arg-type]

        # FIXED: Cache the newly created tenant for immediate lookup
        if created_tenant is not None:
            await self._cache_tenant(created_tenant)

        # Invalidate the negative cache so newly created tenants aren't hidden
        # (adapter is None-safe: noop when Redis is down).
        if tenant.slug:
            await cache_client.delete(negative_cache_key(tenant.slug))

        # B7: also clear the in-process L1 negative entries (any worker,
        # including this one, may have cached "slug not found" while probing
        # before creation) and fan out to other workers — same T9 machinery
        # deactivate/reactivate use. Without this, a slug probed before
        # creation 403s for up to the L1 TTL (60s) after the tenant exists.
        try:
            from src.app.core.tenancy.cache import (
                invalidate_tenant_l1,
                publish_tenant_l1_invalidation,
            )

            invalidate_tenant_l1(tenant.slug)
            await publish_tenant_l1_invalidation(tenant.slug)
        except Exception as e:
            logger.warning(
                "Tenant L1 negative-cache invalidation failed after creation",
                slug=tenant.slug,
                error=str(e),
            )

        logger.info(
            "tenant_created",
            tenant_id=result["id"],
            slug=result["slug"],
            name=result["name"],
        )

        return result

    async def get_tenant(self, tenant_id: UUID) -> dict[str, Any]:
        """Get a tenant by ID with Redis caching.

        P2-4: First checks Redis cache for O(1) retrieval, falls back to DB.

        Parameters
        ----------
        tenant_id : UUID
            Tenant unique identifier

        Returns
        -------
        dict[str, Any]
            Tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        # P2-4: Try cache first
        cached = await self._get_cached_tenant(tenant_id)
        if cached:
            return cached

        # Cache miss - fetch from DB
        tenant = await self.crud.get(db=self.db, id=tenant_id, schema_to_select=TenantResponse)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)

        # Cache for next request
        # NOTE: FastCRUD with schema_to_select returns a dict, not an ORM Tenant object.
        # Cache the dict directly via a lightweight method.
        if tenant:
            await self._cache_tenant(tenant)

        return tenant

    async def get_tenant_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Get a tenant by slug with Redis caching.

        P2-4: First checks Redis cache for O(1) retrieval, falls back to DB.

        Parameters
        ----------
        slug : str
            Tenant slug

        Returns
        -------
        dict[str, Any] | None
            Tenant data or None if not found
        """
        normalized_slug = slug.lower().strip()

        # P2-4: Try cache first
        cached = await self._get_cached_tenant_by_slug(normalized_slug)
        if cached:
            return cached

        # Cache miss - fetch from DB
        tenant = await self.crud.get(db=self.db, slug=normalized_slug, schema_to_select=TenantResponse)

        # Cache for next request
        # NOTE: FastCRUD with schema_to_select returns a dict, not an ORM Tenant object.
        if tenant:
            await self._cache_tenant(tenant)

        return tenant

    async def list_tenants(
        self,
        page: int = 1,
        items_per_page: int = 10,
        is_active: bool | None = None,
    ) -> dict[str, Any]:
        """List tenants with pagination and optional filtering.

        Parameters
        ----------
        page : int
            Page number (1-indexed)
        items_per_page : int
            Items per page
        is_active : bool | None
            Filter by active status

        Returns
        -------
        dict[str, Any]
            Paginated tenant list
        """
        from fastcrud import compute_offset

        offset = compute_offset(page, items_per_page)

        kwargs: dict[str, Any] = {
            "db": self.db,
            "offset": offset,
            "limit": items_per_page,
            "schema_to_select": TenantResponse,
        }

        if is_active is not None:
            kwargs["is_active"] = is_active

        from src.app.core.db.rls import bypass_rls

        # Explicit bypass: superuser endpoint lists all tenants across the platform
        async with bypass_rls(self.db, reason="list_tenants (superuser)"):
            result: dict[str, Any] = await self.crud.get_multi(**kwargs)

        return result

    async def update_tenant(
        self,
        tenant_id: UUID,
        values: TenantUpdate,
    ) -> dict[str, Any]:
        """Update a tenant.

        Optimized: Uses session refresh instead of second DB roundtrip.

        Parameters
        ----------
        tenant_id : UUID
            Tenant unique identifier
        values : TenantUpdate
            Update values

        Returns
        -------
        dict[str, Any]
            Updated tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        # P1-1: Fetch ORM object directly for in-place update
        tenant = await self.db.get(Tenant, tenant_id)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)

        update_data = values.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)

        old_slug = tenant.slug

        # Apply updates to ORM object
        for key, value in update_data.items():
            setattr(tenant, key, value)

        try:
            await self.db.commit()
            await self.db.refresh(tenant)
        except IntegrityError:
            await self.db.rollback()
            logger.warning(
                "tenant_update_failed_duplicate_slug",
                slug=tenant.slug,
            )
            raise DuplicateError("slug", tenant.slug)

        # FIXED: Invalidate tenant cache after update to prevent stale data
        await self._invalidate_tenant_cache(tenant_id, tenant.slug)
        if old_slug and old_slug != tenant.slug:
            await self._invalidate_tenant_cache(tenant_id, old_slug)

        # P2-1: Direct dict construction bypasses Pydantic for ~3x speedup
        updated_dict = _tenant_to_dict(tenant)  # type: ignore[arg-type]

        # Write back to cache immediately
        await self._cache_tenant(updated_dict)

        logger.info(
            "tenant_updated",
            tenant_id=tenant_id,
            fields=list(update_data.keys()),
        )

        return updated_dict

    async def deactivate_tenant(self, tenant_id: UUID) -> dict[str, Any]:
        """Deactivate a tenant (soft delete).

        Optimized: Uses session refresh instead of second DB roundtrip.

        Parameters
        ----------
        tenant_id : UUID
            Tenant unique identifier

        Returns
        -------
        dict[str, Any]
            Deactivated tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        # P1-1: Fetch ORM object directly for in-place update
        tenant = await self.db.get(Tenant, tenant_id)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)

        tenant.is_active = False
        tenant.updated_at = datetime.now(UTC)

        await self.db.commit()
        await self.db.refresh(tenant)

        # FIXED: Invalidate tenant cache after deactivation to prevent stale data
        await self._invalidate_tenant_cache(tenant_id, tenant.slug)

        logger.info("tenant_deactivated", tenant_id=tenant_id, slug=tenant.slug)

        # P2-1: Direct dict construction bypasses Pydantic for ~3x speedup
        updated_dict = _tenant_to_dict(tenant)  # type: ignore[arg-type]
        await self._cache_tenant(updated_dict)
        return updated_dict

    async def reactivate_tenant(self, tenant_id: UUID) -> dict[str, Any]:
        """Reactivate a deactivated tenant.

        Optimized: Uses session refresh instead of second DB roundtrip.

        Parameters
        ----------
        tenant_id : UUID
            Tenant unique identifier

        Returns
        -------
        dict[str, Any]
            Reactivated tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        # P1-1: Fetch ORM object directly for in-place update
        tenant = await self.db.get(Tenant, tenant_id)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)

        tenant.is_active = True
        tenant.updated_at = datetime.now(UTC)

        await self.db.commit()
        await self.db.refresh(tenant)

        # FIXED: Invalidate tenant cache after reactivation to prevent stale data
        await self._invalidate_tenant_cache(tenant_id, tenant.slug)

        logger.info("tenant_reactivated", tenant_id=tenant_id, slug=tenant.slug)

        # P2-1: Direct dict construction bypasses Pydantic for ~3x speedup
        updated_dict = _tenant_to_dict(tenant)  # type: ignore[arg-type]
        await self._cache_tenant(updated_dict)
        return updated_dict
