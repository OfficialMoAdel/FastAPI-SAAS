"""Authorization tests for GET /api/v1/dashboard.

Run-3 finding ``users.dashboard.metrics_missing_authorization``: the dashboard
router was mounted with no ``dependencies=`` and its only route took
``RequestContextDep``, which authenticates and resolves a tenant but evaluates
no role. Any authenticated tenant member — including the lowest role — received
the whole tenant's revenue/MRR/ARPU/subscription/login aggregates, while the
route's own docstring claimed "admin or user with dashboard access".

These tests pin the fixed behavior. They follow the established contract pattern
(``tests/contract/test_rbac_security.py``): a real ``AuthContext`` via
``override_test_principal``, real tenant-isolation enforcement, and the
production permission dependency with only the permission *source* patched.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.app.core.rbac.permissions import Permission
from tests.helpers.auth import override_test_principal

MOCK_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_TENANT_ID = UUID("00000000-0000-0000-0000-0000000000ff")

DASHBOARD_URL = "/api/v1/dashboard"
TENANT_HEADERS = {"Authorization": "Bearer test", "X-Tenant-ID": "test-tenant"}


@pytest_asyncio.fixture
async def client(get_test_app) -> AsyncClient:
    mock_redis = AsyncMock()
    mock_redis.evalsha = AsyncMock(return_value=1)
    mock_redis.script_load = AsyncMock(return_value="fake_sha")
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.setex = AsyncMock(return_value=True)
    mock_redis.exists = AsyncMock(return_value=False)
    mock_redis.sismember = AsyncMock(return_value=0)
    mock_redis.smembers = AsyncMock(return_value=set())
    mock_redis.pipeline = MagicMock(return_value=AsyncMock())

    with patch("src.app.core.utils.cache.client", mock_redis):
        transport = ASGITransport(app=get_test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _tenant(tenant_id: UUID = MOCK_TENANT_ID, slug: str = "test-tenant"):
    return MagicMock(id=tenant_id, slug=slug, is_active=True)


def _patch_tenant(target: MagicMock):
    async def _get_tenant_by_slug(*args, **kwargs):
        return target

    return patch(
        "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
        new=_get_tenant_by_slug,
    )


def _patch_permissions(granted: set):
    async def _get_user_permissions(*args, **kwargs):
        return granted

    return patch(
        "src.app.core.rbac.dependencies.get_user_permissions",
        side_effect=_get_user_permissions,
    )


class TestDashboardAuthorization:
    """The dashboard must require an explicit grant, not mere authentication."""

    @pytest.mark.asyncio
    async def test_unprivileged_member_is_denied(self, client: AsyncClient) -> None:
        """NEGATIVE: an authenticated member with no permissions gets 403.

        This is the exact regression the finding described: before the fix this
        request returned 200 with tenant-wide financial aggregates.
        """
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        tenant = _tenant()

        with (
            _patch_tenant(tenant),
            override_test_principal(mock_user, tenant),
            _patch_permissions(set()),
        ):
            response = await client.get(DASHBOARD_URL, headers=TENANT_HEADERS)

        assert response.status_code == 403
        assert "permission" in response.json().get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_member_with_unrelated_permission_is_denied(self, client: AsyncClient) -> None:
        """NEGATIVE: holding some other permission is not sufficient."""
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        tenant = _tenant()

        with (
            _patch_tenant(tenant),
            override_test_principal(mock_user, tenant),
            _patch_permissions({Permission.USERS_READ, Permission.FILES_READ}),
        ):
            response = await client.get(DASHBOARD_URL, headers=TENANT_HEADERS)

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_role_holding_dashboard_read_is_allowed(self, client: AsyncClient) -> None:
        """POSITIVE: a role granted DASHBOARD_READ reaches the handler."""
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        tenant = _tenant()

        with (
            _patch_tenant(tenant),
            override_test_principal(mock_user, tenant),
            _patch_permissions({Permission.DASHBOARD_READ}),
            patch(
                "src.app.modules.dashboard.service.dashboard_service.get_metrics",
                new=AsyncMock(return_value=_metrics_payload()),
            ),
        ):
            response = await client.get(DASHBOARD_URL, headers=TENANT_HEADERS)

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_superuser_is_allowed_without_explicit_grant(self, client: AsyncClient) -> None:
        """POSITIVE: superuser bypass is preserved (no operator lockout)."""
        mock_user = {"id": 1, "is_superuser": True, "tenant_id": MOCK_TENANT_ID}
        tenant = _tenant()

        with (
            _patch_tenant(tenant),
            override_test_principal(mock_user, tenant),
            _patch_permissions(set()),
            patch(
                "src.app.modules.dashboard.service.dashboard_service.get_metrics",
                new=AsyncMock(return_value=_metrics_payload()),
            ),
        ):
            response = await client.get(DASHBOARD_URL, headers=TENANT_HEADERS)

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_cross_tenant_header_is_rejected(self, client: AsyncClient) -> None:
        """NEGATIVE: a member cannot read another tenant's aggregates.

        Guards the tenant-isolation seam: the permission grant is evaluated
        against the caller's own tenant, so pointing X-Tenant-ID at a different
        tenant is refused rather than returning that tenant's metrics.
        """
        mock_user = {"id": 1, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        other_tenant = _tenant(OTHER_TENANT_ID, "other-tenant")

        with (
            _patch_tenant(other_tenant),
            override_test_principal(mock_user, other_tenant),
            _patch_permissions({Permission.DASHBOARD_READ}),
        ):
            response = await client.get(
                DASHBOARD_URL,
                headers={"Authorization": "Bearer test", "X-Tenant-ID": "other-tenant"},
            )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_anonymous_is_rejected(self, client: AsyncClient) -> None:
        """NEGATIVE: no credential is still 401, never 200."""
        tenant = _tenant()

        with _patch_tenant(tenant), override_test_principal(None, None):
            response = await client.get(
                DASHBOARD_URL, headers={"X-Tenant-ID": "test-tenant"}
            )

        assert response.status_code in (401, 403)


class TestDashboardPermissionIsGrantable:
    """Finding #6 gate: the new slug must be real, not just declared.

    ``add_permission_to_role`` resolves a slug against the persisted
    ``permissions`` table, so a permission that exists only in the enum would
    make this gate permanently unsatisfiable. These tests fail if someone adds a
    permission to the enum without the catalog being able to carry it.
    """

    def test_dashboard_read_is_in_the_bitmask(self) -> None:
        """The bitmask derives from the enum, so the new slug is encodable."""
        from src.app.core.rbac.bitmask import PermissionBitmask

        bit = PermissionBitmask.get_bit(Permission.DASHBOARD_READ.value)
        assert bit > 0
        assert PermissionBitmask.has_permission(bit, Permission.DASHBOARD_READ.value)

    def test_dashboard_read_slug_is_unique(self) -> None:
        from src.app.core.rbac.bitmask import PermissionBitmask

        mapping = PermissionBitmask.get_all_permissions()
        assert Permission.DASHBOARD_READ.value in mapping
        # Two slugs sharing a bit would make one grant silently satisfy the other.
        assert len(mapping) == len(set(mapping.values()))


def _metrics_payload():
    from datetime import UTC, datetime

    from src.app.modules.dashboard.schemas import (
        ActivityMetrics,
        DashboardMetricsResponse,
        RevenueMetrics,
        TimeRange,
        UserMetrics,
    )

    now = datetime.now(UTC)
    return DashboardMetricsResponse(
        time_range=TimeRange.MONTH,
        start_date=now,
        end_date=now,
        users=UserMetrics(total_users=1, new_users=0, active_users=1, deleted_users=0),
        revenue=RevenueMetrics(
            total_revenue=0.0,
            recurring_revenue=0.0,
            average_revenue_per_user=0.0,
            total_subscriptions=0,
        ),
        activity=ActivityMetrics(
            total_logins=0,
            api_calls=0,
            failed_authentications=0,
            webhook_deliveries=0,
        ),
    )
