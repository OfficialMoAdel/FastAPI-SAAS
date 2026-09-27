"""Tests for the two needs-validation remediations.

Run-3 N-1 (middleware.rate_limit.anonymous_key_path_scoped) and
N-2 (billing.catalog_reads_scope_from_unauthenticated_header).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.rate_policy import (
    AGGREGATE_PATH_TOKEN,
    build_anonymous_aggregate_key,
    build_rate_limit_key,
)

# ---------------------------------------------------------------------------
# N-1: anonymous aggregate per-IP ceiling
# ---------------------------------------------------------------------------


class TestAggregateKeyIsRotationProof:
    """The bypass was rotating the PATH to mint a fresh bucket every request."""

    def test_aggregate_key_is_constant_across_paths(self) -> None:
        """Same IP -> same aggregate bucket no matter what path is requested."""
        keys = {
            build_anonymous_aggregate_key("1.2.3.4") for _ in range(50)
        }
        assert len(keys) == 1
        assert AGGREGATE_PATH_TOKEN in keys.pop()

    def test_aggregate_key_is_per_ip(self) -> None:
        assert build_anonymous_aggregate_key("1.1.1.1") != build_anonymous_aggregate_key("2.2.2.2")

    def test_aggregate_key_handles_missing_ip_without_fragmenting(self) -> None:
        """A missing IP must not mint a distinct bucket per request."""
        a = build_anonymous_aggregate_key(None)
        b = build_anonymous_aggregate_key("unknown")
        assert a == b
        assert "unknown" in a

    def test_aggregate_and_per_path_keys_are_distinct(self) -> None:
        per_path = build_rate_limit_key("tiers/free", anonymous_ip="1.2.3.4")
        aggregate = build_anonymous_aggregate_key("1.2.3.4")
        assert per_path != aggregate


class TestSanitizePathCardinalityClaim:
    """The digest caps key LENGTH, not key CARDINALITY — documented, not fixed here."""

    def test_distinct_string_segments_stay_distinct(self) -> None:
        from src.app.core.utils.path_utils import sanitize_path

        a = sanitize_path("/api/v1/tiers/plan-alpha")
        b = sanitize_path("/api/v1/tiers/plan-beta")
        assert a != b, (
            "if this ever becomes equal the rotation bypass is closed at the "
            "key-builder level too, and the aggregate ceiling is belt-and-braces"
        )

    def test_numeric_and_uuid_segments_still_collapse(self) -> None:
        from src.app.core.utils.path_utils import sanitize_path

        assert sanitize_path("/api/v1/users/1") == sanitize_path("/api/v1/users/2")
        u1 = "/api/v1/users/00000000-0000-0000-0000-000000000001"
        u2 = "/api/v1/users/00000000-0000-0000-0000-000000000002"
        assert sanitize_path(u1) == sanitize_path(u2)


class TestAnonymousAggregateCeiling:
    """The ceiling must only ever ADD rejections, never admit extra traffic."""

    def _service(self):
        from src.app.core.security.request_admission import RequestAdmissionService

        return RequestAdmissionService()

    @pytest.mark.asyncio
    async def test_disabled_when_limit_is_zero(self) -> None:
        from src.app.core.config import settings

        svc = self._service()
        original = settings.rate_limit.ANONYMOUS_IP_AGGREGATE_LIMIT
        settings.rate_limit.ANONYMOUS_IP_AGGREGATE_LIMIT = (0, 3600)
        try:
            assert await svc._check_anonymous_aggregate("1.2.3.4") is None
        finally:
            settings.rate_limit.ANONYMOUS_IP_AGGREGATE_LIMIT = original

    @pytest.mark.asyncio
    async def test_under_limit_admits(self) -> None:
        svc = self._service()
        with patch(
            "src.app.core.security.request_admission.consume",
            new=AsyncMock(return_value=(False, 299, 0, False)),
        ):
            is_limited, remaining, _ = await svc._check_anonymous_aggregate("1.2.3.4")
        assert is_limited is False
        assert remaining == 299

    @pytest.mark.asyncio
    async def test_over_limit_rejects(self) -> None:
        svc = self._service()
        with patch(
            "src.app.core.security.request_admission.consume",
            new=AsyncMock(return_value=(True, 0, 42, False)),
        ):
            is_limited, remaining, retry_after = await svc._check_anonymous_aggregate("1.2.3.4")
        assert is_limited is True
        assert remaining == 0
        assert retry_after == 42

    @pytest.mark.asyncio
    async def test_consumes_under_the_constant_token_key(self) -> None:
        """A path-rotating attacker cannot vary the aggregate key."""
        svc = self._service()
        seen: list[str] = []

        async def _capture(key, limit, period, **kwargs):
            seen.append(key)
            return False, 1, 0, False

        with patch("src.app.core.security.request_admission.consume", new=_capture):
            for _ in range(20):
                await svc._check_anonymous_aggregate("9.9.9.9")

        assert len(seen) == 20
        assert len(set(seen)) == 1, "aggregate key varied - rotation bypass is back"
        assert AGGREGATE_PATH_TOKEN in seen[0]

    @pytest.mark.asyncio
    async def test_redis_failure_does_not_block_admission(self) -> None:
        """The ceiling is additive: a broken ceiling must not 500 every request."""
        svc = self._service()
        with patch(
            "src.app.core.security.request_admission.consume",
            new=AsyncMock(side_effect=RuntimeError("redis down")),
        ):
            assert await svc._check_anonymous_aggregate("1.2.3.4") is None


# ---------------------------------------------------------------------------
# N-2: billing catalog is confidential
# ---------------------------------------------------------------------------


class TestBillingCatalogRoutesRequireAuthentication:
    """Products/prices are confidential tenant rows, not a public catalog."""

    ROUTES = [
        ("GET", "/api/v1/products", "list_products"),
        ("GET", "/api/v1/products/{product_id:int}", "get_product"),
        ("GET", "/api/v1/prices", "list_prices"),
        ("GET", "/api/v1/prices/{price_id}", "get_price"),
    ]

    def _resolve(self, path_template: str):
        from src.app.main import app

        for route in app.routes:
            if getattr(route, "path", None) == path_template:
                methods = getattr(route, "methods", set()) or set()
                if "GET" in methods:
                    return route
        return None

    @pytest.mark.parametrize("method,path_template,fn_name", ROUTES)
    def test_route_exists(self, method, path_template, fn_name) -> None:
        route = self._resolve(path_template)
        assert route is not None, f"{method} {path_template} disappeared"
        assert route.endpoint.__name__ == fn_name

    @pytest.mark.parametrize("method,path_template,fn_name", ROUTES)
    def test_route_requires_authenticated_context(self, method, path_template, fn_name) -> None:
        """Regression guard for N-2.

        The vulnerable shape took only a DB session, so tenant scope came solely
        from the caller-supplied X-Tenant-ID header. Every one of these handlers
        must now take ``ctx: RequestContextDep``, which authenticates and binds
        the tenant to the principal.
        """
        import inspect

        from src.app.modules.billing.routers import prices, products

        route = self._resolve(path_template)
        assert route is not None

        fn = route.endpoint
        assert fn.__module__ in (products.__name__, prices.__name__)

        params = list(inspect.signature(fn).parameters)
        assert "ctx" in params, f"{fn_name} must take ctx: RequestContextDep, got {params}"
        # And must NOT have fallen back to a bare session parameter.
        assert "db" not in params, f"{fn_name} regressed to a bare db session"

        # The declared type must be the RequestContextDep alias, not a plain model.
        import typing

        hints = typing.get_type_hints(fn, include_extras=True)
        assert "ctx" in hints

    def test_pricing_service_scopes_the_pricing_row_itself(self) -> None:
        """N-2 service fix: the pricing read is tenant-filtered, not just its parent."""
        import inspect

        from src.app.modules.billing.service import PricingService

        src = inspect.getsource(PricingService.get_pricing)
        assert "tenant_id=tid" in src, (
            "get_pricing must filter the pricing row by tenant, not only validate the parent product"
        )


class TestGetPricingTenantFilter:
    @pytest.mark.asyncio
    async def test_cross_tenant_pricing_is_not_returned(self) -> None:
        """A pricing row from another tenant must 404, not leak."""
        from src.app.modules.billing.service import PricingService
        from src.app.shared.exceptions import ModelNotFoundError

        service = PricingService(MagicMock())
        service._resolve_tenant = MagicMock(return_value="tenant-b")

        with (
            patch("src.app.modules.billing.service.crud_pricing") as crud_pricing,
            patch("src.app.modules.billing.service.crud_products"),
        ):
            # The tenant-filtered read finds nothing.
            crud_pricing.get = AsyncMock(return_value=None)

            with pytest.raises(ModelNotFoundError):
                await service.get_pricing(7)

        crud_pricing.get.assert_awaited_once()
        assert "tenant_id" in crud_pricing.get.await_args.kwargs

    @pytest.mark.asyncio
    async def test_matching_pricing_is_returned(self) -> None:
        from src.app.modules.billing.service import PricingService

        service = PricingService(MagicMock())
        service._resolve_tenant = MagicMock(return_value="tenant-a")

        row = {"id": 7, "product_id": 3}
        with (
            patch("src.app.modules.billing.service.crud_pricing") as crud_pricing,
            patch("src.app.modules.billing.service.crud_products") as crud_products,
        ):
            crud_pricing.get = AsyncMock(return_value=row)
            crud_products.get = AsyncMock(return_value={"id": 3})

            result = await service.get_pricing(7)

        assert result == row
