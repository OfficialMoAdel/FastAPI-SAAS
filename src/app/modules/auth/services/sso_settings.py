from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.cache.client import cache_client
from src.app.core.logger import get_logger
from src.app.core.security.encryption import get_encryption
from src.app.core.tenancy.context import get_current_tenant  # Needed to get default tenant_id if not passed
from src.app.modules.audit_logs import audit
from src.app.modules.auth.crud import crud_sso_settings
from src.app.modules.auth.models import SSOSettings
from src.app.modules.auth.schemas import (
    SSOSettingsCreate,
    SSOSettingsCreateInternal,
    SSOSettingsRead,
    SSOSettingsUpdate,
)
from src.app.shared.exceptions import DuplicateError, ModelNotFoundError

logger = get_logger(__name__)

# sso-cache: single source of truth for the provider-settings cache TTL.
# The write-through in ``get_settings_by_provider`` is the ONLY populator: no
# ``sso_settings`` startup warmer is registered in ``composition.py`` (it warms
# tenants and feature flags only). That is why the batch read in
# ``get_settings_by_providers`` still resolves every miss through the
# single-provider path: the first call on a cold cache populates it, and every
# call after that is one MGET.
_SSO_SETTINGS_CACHE_TTL = 300
# The provider-settings cache key. ``SSOSettings.provider`` is globally
# unique and the table carries no tenant column, so the key is provider-only.
_SSO_SETTINGS_CACHE_PREFIX = "sso_settings:"


class SSOSettingsService:
    """Service for managing SSO provider settings with encryption."""

    async def create_settings(
        self, db: AsyncSession, settings: SSOSettingsCreate, user_id: int, tenant_id: UUID | None = None
    ) -> SSOSettingsRead:
        """Create new SSO provider settings.

        Encrypts client_id and client_secret before storage.
        """
        # Check if provider already exists
        if await crud_sso_settings.exists(db=db, provider=settings.provider):
            raise DuplicateError("provider", settings.provider)

        encryption = get_encryption()

        # Determine tenant_id if not passed
        if not tenant_id:
            tenant = get_current_tenant()
            if tenant:
                tenant_id = tenant.id

        # Encrypt secrets (returns str, encode to bytes for DB)
        # Note: encrypt_column returns a versioned base64 string
        encrypted_client_id = encryption.encrypt_column(settings.client_id).encode("utf-8")
        encrypted_client_secret = encryption.encrypt_column(settings.client_secret).encode("utf-8")

        settings_internal = SSOSettingsCreateInternal(
            provider=settings.provider,
            client_id=encrypted_client_id,
            client_secret=encrypted_client_secret,
            additional_config=settings.additional_config,
            is_enabled=settings.is_enabled,
        )

        db_obj = SSOSettings(**settings_internal.model_dump())
        # Tenant context handled by middleware/FastCRUD if available

        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)

        # Invalidate cache so subsequent reads use the fresh record
        await self._invalidate_provider_cache(settings.provider)

        await audit(
            action="sso_settings.create",
            entity_type="sso_settings",
            entity_id=str(db_obj.id) if hasattr(db_obj, "id") else None,
            tenant_id=tenant_id,
            user_id=user_id,
            details={"provider": settings.provider},
        )

        return SSOSettingsRead.model_validate(db_obj)

    async def get_settings_by_provider(self, db: AsyncSession, provider: str) -> SSOSettingsRead:
        """Get public settings for a provider (no secrets).

        sso-cache FIX: a cache miss previously fell through to the DB and
        returned without repopulating. Reads now write through with the same
        TTL, and because no startup warmer exists for this domain the
        write-through is what keeps the cache warm at all after an
        invalidation.
        """
        cache_key = f"{_SSO_SETTINGS_CACHE_PREFIX}{provider}"
        try:
            cached = await cache_client.get_json(cache_key)
            if cached is not None:
                return SSOSettingsRead.model_validate(cached)
        except Exception as e:
            logger.warning("SSO settings cache read failed, falling back to DB", provider=provider, error=str(e))

        settings = await crud_sso_settings.get(db=db, provider=provider)
        if not settings:
            raise ModelNotFoundError("SSOSettings", provider, message=f"Settings for provider '{provider}' not found")

        result = SSOSettingsRead.model_validate(settings)

        # Write-through so subsequent reads hit the cache (same shape/TTL as warmer).
        # The payload is the PUBLIC read model dumped — never a hand-assembled
        # dict — so what lands in Redis is exactly what the read next door can
        # re-validate. The hand-assembled version omitted created_at, which
        # ``SSOSettingsRead`` requires, so every "hit" failed validation and
        # fell back to the DB: the write-through was paying for a write that
        # could not be read. ``client_id``/``client_secret`` are not on the
        # public model, so no secret can reach the payload.
        # The adapter is None-safe (noop when Redis is down).
        try:
            await cache_client.set_json(
                cache_key,
                result.model_dump(mode="json"),
                ttl=_SSO_SETTINGS_CACHE_TTL,
            )
        except Exception as e:
            logger.warning("SSO settings cache write-through failed", provider=provider, error=str(e))

        return result

    async def get_settings_by_providers(
        self, db: AsyncSession, providers: Sequence[str]
    ) -> dict[str, SSOSettingsRead]:
        """Get public settings for MANY providers with one cache round trip.

        The batch form of ``get_settings_by_provider``, for callers that list
        every supported provider (the ``/sso/providers`` door). The cache is
        read with ONE ``MGET``; only the providers that missed are resolved
        through ``get_settings_by_provider``, so per-provider JSON validation
        and the write-through stay in exactly one place.

        A provider with no settings row is OMITTED from the result — the
        batch form of the ``ModelNotFoundError`` the single-provider read
        raises, and what makes a not-configured provider stay disabled. A
        database failure is not swallowed: only ``ModelNotFoundError`` is
        caught here.

        Returns:
            Public settings keyed by provider, in the input provider order.
        """
        keys = [f"{_SSO_SETTINGS_CACHE_PREFIX}{provider}" for provider in providers]
        cached_payloads = await cache_client.get_json_many(keys)

        resolved: dict[str, SSOSettingsRead] = {}
        missing: list[str] = []
        for provider, payload in zip(providers, cached_payloads, strict=True):
            if payload is None:
                missing.append(provider)
                continue
            try:
                resolved[provider] = SSOSettingsRead.model_validate(payload)
            except Exception as e:  # noqa: BLE001 - a corrupt cached payload is a MISS, never a 5xx
                # Same contract as the single-provider read: an unvalidatable
                # cached payload is a MISS, not an error served to the caller.
                logger.warning(
                    "SSO settings cached payload invalid, refetching from DB", provider=provider, error=str(e)
                )
                missing.append(provider)

        for provider in missing:
            try:
                resolved[provider] = await self.get_settings_by_provider(db, provider)
            except ModelNotFoundError:
                continue

        return {provider: resolved[provider] for provider in providers if provider in resolved}

    async def get_decrypted_settings(self, db: AsyncSession, provider: str) -> dict[str, Any]:
        """Get settings with decrypted secrets (Internal Use Only).

        Returns:
            Dict containing client_id, client_secret, and additional_config.
        """
        settings = await crud_sso_settings.get(db=db, provider=provider)
        if not settings:
            raise ModelNotFoundError("SSOSettings", provider, message=f"Settings for provider '{provider}' not found")

        encryption = get_encryption()

        # Decrypt secrets (bytes from DB -> decode to str -> decrypt -> str)
        # encrypt_column returned str, which we encoded to bytes.
        # So: bytes -> decode -> str (encrypted) -> decrypt_column -> str (plaintext)
        try:
            # Handle both ORM object and dictionary (FastCRUD might return dict)
            raw_client_id = settings.client_id if hasattr(settings, "client_id") else settings["client_id"]
            raw_client_secret = (
                settings.client_secret if hasattr(settings, "client_secret") else settings["client_secret"]
            )

            client_id = encryption.decrypt_column(raw_client_id.decode("utf-8"))
            client_secret = encryption.decrypt_column(raw_client_secret.decode("utf-8"))
        except Exception as e:
            # If decryption fails, it might be a key issue or data corruption
            raise ValueError(f"Failed to decrypt credentials for {provider}: {e}") from e

        return {
            "id": settings.id if hasattr(settings, "id") else settings["id"],
            "provider": settings.provider if hasattr(settings, "provider") else settings["provider"],
            "client_id": client_id,
            "client_secret": client_secret,
            "additional_config": (
                settings.additional_config
                if hasattr(settings, "additional_config")
                else settings.get("additional_config")
            )
            or {},
            "is_enabled": settings.is_enabled if hasattr(settings, "is_enabled") else settings["is_enabled"],
            "created_at": settings.created_at if hasattr(settings, "created_at") else settings["created_at"],
            "updated_at": settings.updated_at if hasattr(settings, "updated_at") else settings["updated_at"],
        }

    async def update_settings(
        self, db: AsyncSession, provider: str, settings: SSOSettingsUpdate, user_id: int, tenant_id: UUID | None = None
    ) -> SSOSettingsRead:
        """Update SSO settings."""
        current = await crud_sso_settings.get(db=db, provider=provider)
        if not current:
            raise ModelNotFoundError("SSOSettings", provider, message=f"Settings for provider '{provider}' not found")

        # Determine tenant_id if not passed
        if not tenant_id:
            tenant = get_current_tenant()
            if tenant:
                tenant_id = tenant.id

        update_data: dict[str, bytes | dict[str, Any] | bool | None] = {}
        encryption = get_encryption()

        if settings.client_id is not None:
            update_data["client_id"] = encryption.encrypt_column(settings.client_id).encode("utf-8")

        if settings.client_secret is not None:
            update_data["client_secret"] = encryption.encrypt_column(settings.client_secret).encode("utf-8")

        if settings.additional_config is not None:
            update_data["additional_config"] = settings.additional_config

        if settings.is_enabled is not None:
            update_data["is_enabled"] = settings.is_enabled

        if not update_data:
            return SSOSettingsRead.model_validate(current)

        await crud_sso_settings.update(db=db, object=update_data, provider=provider)
        current.update(update_data)

        # Invalidate cache so subsequent reads pick up the change
        await self._invalidate_provider_cache(provider)

        current_id = current.get("id") if isinstance(current, dict) else getattr(current, "id", None)
        await audit(
            action="sso_settings.update",
            entity_type="sso_settings",
            entity_id=str(current_id) if current_id is not None else None,
            tenant_id=tenant_id,
            user_id=user_id,
            details={"provider": provider},
        )
        return SSOSettingsRead.model_validate(current)

    async def delete_settings(
        self, db: AsyncSession, provider: str, user_id: int, tenant_id: UUID | None = None
    ) -> None:
        """Delete SSO settings."""
        # Determine tenant_id if not passed (for audit log)
        if not tenant_id:
            tenant = get_current_tenant()
            if tenant:
                tenant_id = tenant.id

        if not await crud_sso_settings.exists(db=db, provider=provider):
            raise ModelNotFoundError("SSOSettings", provider, message=f"Settings for provider '{provider}' not found")

        await crud_sso_settings.delete(db=db, provider=provider)

        # Invalidate cache so stale data is immediately evicted
        await self._invalidate_provider_cache(provider)

        await audit(
            action="sso_settings.delete",
            entity_type="sso_settings",
            entity_id=provider,
            tenant_id=tenant_id,
            user_id=user_id,
            details={"provider": provider},
        )
    @staticmethod
    async def _invalidate_provider_cache(provider: str) -> None:
        """Delete the Redis cache entry for a single SSO provider."""
        try:
            await cache_client.delete(f"{_SSO_SETTINGS_CACHE_PREFIX}{provider}")
        except Exception as e:
            logger.warning("Failed to invalidate SSO settings cache", provider=provider, error=str(e))


# Legacy singleton for backward compatibility - will be removed
sso_settings_service = SSOSettingsService()
