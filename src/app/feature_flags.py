"""Feature flags service."""

from typing import Any

from sqlalchemy import select

from src.app.core.db.database import async_session_maker
from src.app.core.logger import get_logger
from src.app.modules.tenants.models import Tenant

logger = get_logger(__name__)


class FeatureFlagsService:
    """Service for managing feature flags."""

    def __init__(self):
        """Initialize feature flags service."""
        self._session_maker = async_session_maker

    async def _load_tenant_flags(self, tenant_id: str) -> dict[str, Any]:
        """Load feature flags from database."""
        from src.app.core.db.rls import bypass_rls

        async with self._session_maker() as session:
            async with bypass_rls(session, reason="Feature flags service needs cross-tenant access"):
                result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
            tenant = result.scalar_one_or_none()

            if not tenant:
                return {}

            return tenant.feature_flags or {}

    async def is_enabled(self, tenant_id: str, flag_name: str, default: bool = False) -> bool:
        """
        Check if feature flag is enabled.

        Args:
            tenant_id: Tenant ID
            flag_name: Feature flag name
            default: Default value if flag not found

        Returns
        -------
            True if flag is enabled
        """
        flags = await self._load_tenant_flags(tenant_id)

        return bool(flags.get(flag_name, default))

    async def get_flag(self, tenant_id: str, flag_name: str, default: Any = None) -> Any:
        """
        Get feature flag value.

        Args:
            tenant_id: Tenant ID
            flag_name: Feature flag name
            default: Default value if flag not found

        Returns
        -------
            Flag value or default
        """
        flags = await self._load_tenant_flags(tenant_id)

        return flags.get(flag_name, default)

    async def set_flag(self, tenant_id: str, flag_name: str, value: Any) -> bool:
        """
        Set feature flag value.

        Args:
            tenant_id: Tenant ID
            flag_name: Feature flag name
            value: Flag value

        Returns
        -------
            True if successful
        """
        from src.app.core.db.rls import bypass_rls

        async with self._session_maker() as session:
            async with bypass_rls(session, reason="Feature flags service needs cross-tenant access"):
                result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
            tenant = result.scalar_one_or_none()

            if not tenant:
                return False

            if not tenant.feature_flags:
                tenant.feature_flags = {}

            # Create a new dict to ensure SQLAlchemy detects the change for JSONB columns
            updated_flags = dict(tenant.feature_flags)
            updated_flags[flag_name] = value
            tenant.feature_flags = updated_flags

            await session.commit()

            return True

    async def get_all_flags(self, tenant_id: str) -> dict[str, Any]:
        """
        Get all feature flags for tenant.

        Args:
            tenant_id: Tenant ID

        Returns
        -------
            Dictionary of all flags
        """
        return await self._load_tenant_flags(tenant_id)


feature_flags_service = FeatureFlagsService()
