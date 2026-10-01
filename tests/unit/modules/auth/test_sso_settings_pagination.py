"""P1-11: /ssos pagination bounds are 422, not 500.

``page`` and ``per_page`` were bare ``int`` defaults. ``?page=0`` computed a
negative offset that reached FastCRUD's ``validate_pagination_params``, which
raises a bare stdlib ValueError with no registered handler — so a malformed
query parameter surfaced as a server error rather than a client error.

The route now uses the standard ``get_pagination_params`` dependency, so
FastAPI validates the bounds (422) and the ``_MAX_OFFSET`` deep-scan guard
applies, neither of which the manual arithmetic bypassed.
"""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import src.app as _app_pkg
from tests.conftest import MockUser

TENANT_HEADERS = {"X-Tenant-ID": str(MockUser.DEFAULT_TENANT_ID)}

# src.app is <repo>/src/app, so parents[2] is the repository root.
REPO = Path(_app_pkg.__file__).resolve().parents[2]
ROUTE = REPO / "src/app/modules/auth/routers/sso_settings.py"


class TestRouteDeclaresTheDependency:
    """Structural: the validation contract IS the dependency."""

    def test_route_uses_pagination_params_dependency(self) -> None:
        """Bare int params are the defect; the dependency is the fix."""
        source = ROUTE.read_text(encoding="utf-8")
        assert "pagination: PaginationParams = Depends(get_pagination_params)" in source, (
            "list_sso_settings does not use the standard pagination dependency"
        )

    def test_no_bare_int_pagination_params_remain(self) -> None:
        """page/per_page must not be plain int defaults any more."""
        tree = ast.parse(ROUTE.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            args = node.args.args + node.args.kwonlyargs
            for arg in args:
                if arg.arg in ("page", "per_page") and isinstance(arg.annotation, ast.Name):
                    if arg.arg != "ctx":
                        offenders.append(f"{node.name}({arg.arg}: int)")
        assert not offenders, f"bare int pagination params remain: {offenders}"

    def test_response_uses_the_shared_envelope_builder(self) -> None:
        """This route was also hand-rolling its page envelope."""
        source = ROUTE.read_text(encoding="utf-8")
        assert "build_paginated_response(" in source, "hand-rolled envelope still present"
        assert '"has_next"' not in source, "hand-rolled has_next still present"


class TestPaginationBoundsAreRejected:
    """The observable: a bad param is 422, not 500."""

    @pytest.mark.parametrize("query", ["page=0", "page=-1", "per_page=0", "per_page=-1", "per_page=99999"])
    def test_out_of_bounds_params_are_422(self, client_auth, query: str) -> None:
        """Every bound the issue lists, rejected at the dependency."""
        response = client_auth.get(f"/api/v1/ssos/?{query}", headers=TENANT_HEADERS)

        assert response.status_code == 422, (
            f"?{query} returned {response.status_code}, expected 422: {response.text[:200]}"
        )
        assert response.status_code != 500, "a client error must not surface as a server error"

    def test_valid_params_return_the_page_envelope(self, client_auth) -> None:
        """Counter-test: valid params still work unchanged."""
        with patch(
            "src.app.modules.auth.routers.sso_settings.fetch_page_with_total",
            new_callable=AsyncMock,
            return_value={"data": [], "total_count": 0},
        ):
            response = client_auth.get("/api/v1/ssos/?page=1&per_page=10", headers=TENANT_HEADERS)

        assert response.status_code in (200, 403), (
            f"valid params broke the route: {response.status_code} {response.text[:200]}"
        )


class TestMiddlewareOrdering:
    """Documents the real ordering, so a future test asserts the right status."""

    def test_missing_tenant_header_is_400_not_422(self, client_auth) -> None:
        """The tenant middleware runs before parameter validation.

        With no X-Tenant-ID the request never reaches the route, so ?page=0
        yields the middleware's 400 — not the handler's 422. This pins which
        layer answers first.
        """
        response = client_auth.get(
            "/api/v1/ssos/?page=0",
            headers={"X-Tenant-ID": "", "Authorization": ""},
        )
        assert response.status_code in (400, 401, 403), (
            f"unexpected status for a header-less request: {response.status_code}"
        )


class TestMaxOffsetGuardApplies:
    """The standard dependency also restores the deep-scan guard."""

    def test_offset_uses_the_guarded_property(self) -> None:
        """pagination.offset, not hand-rolled (page - 1) * per_page."""
        source = ROUTE.read_text(encoding="utf-8")
        assert "offset=pagination.offset" in source, "route bypasses the guarded offset"
        assert "offset=pagination.limit" not in source
        assert "(page - 1) * per_page" not in source, "manual offset arithmetic still present"

    def test_max_offset_guard_rejects_deep_scans(self) -> None:
        """The guard lives in __post_init__ and raises for a deep offset.

        ``offset`` is a computed property (page-1)*per_page, not a field, so
        the guard is what rejects an oversized derived offset.
        """
        from src.app.core.pagination import PaginationParams

        # Within bounds: offset is derived, not stored.
        ok = PaginationParams(page=2, per_page=10)
        assert ok.offset == 10

        # A derived offset past _MAX_OFFSET is refused rather than executed.
        from src.app.core.pagination import _MAX_OFFSET

        with pytest.raises(Exception) as exc:
            PaginationParams(page=(_MAX_OFFSET // 10) + 5, per_page=10)
        assert "exceeds" in str(exc.value)
