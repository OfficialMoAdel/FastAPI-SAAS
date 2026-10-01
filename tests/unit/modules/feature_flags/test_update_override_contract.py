"""Regression tests for ``FeatureFlagService.update_override``.

The tenant-aware wrapper ``TenantAwareCRUD.update`` is annotated ``-> None``
and discards the base class's return value, so the service must not rely on a
row coming back from the write. These tests drive the REAL
``TenantAwareCRUD`` (only the FastCRUD leaf call is patched) so the discarded
return is genuinely exercised rather than hidden behind a ``MagicMock``.
"""

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.core.tenancy.scope import Unscoped
from src.app.modules.feature_flags.models import FeatureFlag, FeatureFlagOverride
from src.app.modules.feature_flags.schemas import FeatureFlagOverrideUpdate
from src.app.modules.feature_flags.service import FeatureFlagService


@pytest.fixture
def override_row() -> FeatureFlagOverride:
    return FeatureFlagOverride(
        id=7,
        feature_flag_id=1,
        tenant_id=uuid4(),
        custom_value={"value": True},
        reason="pilot",
    )


def _service_with_real_crud(row: FeatureFlagOverride) -> FeatureFlagService:
    """A service whose override_crud is a real TenantAwareCRUD.

    Only the FastCRUD leaf calls underneath are patched, so the wrapper's
    ``-> None`` contract is what the service actually observes.
    """
    db = AsyncMock()
    service = FeatureFlagService(db)
    crud = TenantAwareCRUD(model=FeatureFlagOverride, scope=Unscoped(reason="test"))
    service.override_crud = crud  # type: ignore[assignment]
    return service


@pytest.mark.asyncio
async def test_update_override_returns_the_updated_row(override_row: FeatureFlagOverride) -> None:
    """update_override must return the row, not None.

    Pre-fix this fails: TenantAwareCRUD.update returns None, so the service
    took its ``if result is None: return None`` branch.
    """
    service = _service_with_real_crud(override_row)

    with (
        patch.object(TenantAwareCRUD, "_resolve_write_tenant", return_value=override_row.tenant_id),
        patch("fastcrud.FastCRUD.update", new_callable=AsyncMock, return_value=None),
        patch.object(TenantAwareCRUD, "get", new_callable=AsyncMock, return_value=override_row),
        patch.object(service.db, "get", new_callable=AsyncMock, return_value=None),
    ):
        result = await service.update_override(
            override_id=override_row.id,
            data=FeatureFlagOverrideUpdate(custom_value={"value": True}),
        )

    assert result is not None, "update_override returned None; the endpoint cannot return the updated override"
    assert result is override_row


@pytest.mark.asyncio
async def test_update_override_purges_the_flag_cache(override_row: FeatureFlagOverride) -> None:
    """The flag decision cache must be purged after a successful override write.

    Pre-fix this fails: the invalidation block sits behind the
    ``if result is None: return None`` early exit, so it never runs.
    """
    service = _service_with_real_crud(override_row)
    flag = FeatureFlag(
        id=override_row.feature_flag_id,
        key="dark_mode",
        default_value=False,
        description="Dark mode toggle",
    )

    deletes: list[str] = []

    async def _record_delete(*keys: str) -> int:
        deletes.extend(keys)
        return 1

    fake_cache: Any = AsyncMock()
    fake_cache.delete = AsyncMock(side_effect=_record_delete)

    with (
        patch.object(TenantAwareCRUD, "_resolve_write_tenant", return_value=override_row.tenant_id),
        patch("fastcrud.FastCRUD.update", new_callable=AsyncMock, return_value=None),
        patch.object(TenantAwareCRUD, "get", new_callable=AsyncMock, return_value=override_row),
        patch.object(service.db, "get", new_callable=AsyncMock, return_value=flag),
        patch("src.app.modules.feature_flags.service.cache_client", fake_cache),
    ):
        await service.update_override(
            override_id=override_row.id,
            data=FeatureFlagOverrideUpdate(custom_value={"value": True}),
        )

    purged = [key for key in deletes if "dark_mode" in key]
    assert purged, f"flag cache was not purged after an override write; deletes={deletes}"


