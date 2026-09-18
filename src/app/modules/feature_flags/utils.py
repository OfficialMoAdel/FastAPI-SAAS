"""Feature flags utility functions.

B7: the deprecated JSONB shims (``is_feature_enabled`` /
``update_feature_flag`` / ``bulk_update_feature_flags``) were removed —
``FeatureFlagService`` is the single flag module. ``get_feature_flags``
remains as the thin tenant-JSONB read.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.cache.client import cache_client
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.utils.cache_guard import cache_guard
from src.app.modules.tenants.models import Tenant

CACHE_TTL = 300  # 5 minutes


def parse_flag_bool(value: Any) -> bool:
    """Accept only boolean values, integer 1, and case-insensitive true/1 text.

    False/0 and unsupported typed values evaluate to False, never truthiness.
    Redis bytes use the same contract without decoding arbitrary content.
    """
    if isinstance(value, str | bytes):
        return value.lower() in ("true", "1", b"true", b"1")
    return value is True or (type(value) is int and value == 1)


async def get_feature_flags(db: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    """
    Get all feature flags for a tenant.

    Args:
        db: Database session
        tenant_id: Tenant UUID

    Returns:
        dict of flag name to enabled state
    """
    cache_key = cache_client.tenant_key("features", tenant_id)

    with cache_guard("flags_get_all", tenant_id=str(tenant_id)):
        cached = await cache_client.get(cache_key)
        if cached is not None:
            return dict(json_loads(cached))

    result = await db.get(Tenant, tenant_id)
    flags: dict[str, bool] = {}
    if result and result.feature_flags:
        flags = dict(result.feature_flags)
    with cache_guard("flags_write_all", tenant_id=str(tenant_id)):
        await cache_client.set(cache_key, json_dumps(flags, as_str=True), ttl=CACHE_TTL)

    return flags


__all__ = ["CACHE_TTL", "get_feature_flags"]
