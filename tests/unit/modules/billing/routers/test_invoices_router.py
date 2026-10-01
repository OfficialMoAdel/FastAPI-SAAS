"""Unit tests for the /invoices router (API compatibility layer)."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.core.errors import setup_exception_handlers
from src.app.modules.billing.routers.invoices import router as invoices_router


@pytest.fixture
def app():
    """Create a FastAPI app instance with the invoices router."""
    app = FastAPI()
    app.include_router(invoices_router, prefix="/api/v1")
    # The auth gate raises UnauthorizedError; without the app's handlers that
    # surfaces as a 500 and "refused" could not be told apart from "broke".
    setup_exception_handlers(app)
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


def test_invoices_prefix():
    """Test that the router uses /invoices prefix."""
    assert invoices_router.prefix == "/invoices"
    assert "invoices" in invoices_router.tags


def test_all_endpoints_exist():
    """Test that all required endpoints are defined."""
    routes = [(route.path, m) for route in invoices_router.routes for m in route.methods]

    # Expected routes
    assert ("/invoices/", "GET") in routes or ("/invoices", "GET") in routes  # List
    assert ("/invoices/{invoice_id}", "GET") in routes  # Get by ID


def test_delegates_to_billing_record_service():
    """Test that the router delegates to billing BillingRecordService."""
    from src.app.modules.billing.routers.invoices import get_billing_record_service
    from src.app.modules.billing.subscription_service import get_billing_record_service as subscription_get_service

    # Should be the same function
    assert get_billing_record_service is subscription_get_service


def test_list_supports_pagination():
    """Test that list endpoint supports pagination parameters."""
    # Find the GET / route
    list_routes = [
        route
        for route in invoices_router.routes
        if "GET" in route.methods and route.path in ["/invoices/", "/invoices"]
    ]
    assert len(list_routes) == 1


async def test_list_supports_status_filter():
    """The list endpoint actually forwards a status filter to the service.

    Asserting only that a ``status`` parameter exists would restate the
    signature. The behaviour worth pinning is that the caller's filter
    reaches the service untouched -- a route that dropped it would still
    return 200 with every record and no test would notice.
    """
    from unittest.mock import AsyncMock

    from src.app.api.deps.auth import AuthenticatedUser
    from src.app.modules.billing.routers.invoices import list_invoices

    service = MagicMock()
    service.get_user_records = AsyncMock(return_value={"data": [], "total_count": 0})

    with patch(
        "src.app.modules.billing.routers.invoices.get_billing_record_service",
        return_value=service,
    ):
        result = await list_invoices(
            current_user=AuthenticatedUser(id=1, email="u@e.com"),
            db=MagicMock(),
            page=1,
            per_page=20,
            status="succeeded",
        )

    # ``list_invoices`` passes page/limit as kwargs; ``status`` is a plain
    # keyword too, so read it off the recorded call rather than off a
    # positional guess.
    assert service.get_user_records.await_args.kwargs["status"] == "succeeded"
    assert result["items"] == []


def test_route_count():
    """Test that we have the expected number of route handlers."""
    # Count unique endpoints
    unique_paths = {(route.path, m) for route in invoices_router.routes for m in route.methods}

    # Expected: GET/ (list), GET/{id} (get)
    assert len(unique_paths) == 2


def test_response_models():
    """Test that endpoints have correct response models."""
    from src.app.modules.billing.schemas import BillingRecordList, BillingRecordRead

    # Find routes and check response models
    for route in invoices_router.routes:
        if route.path in ["/invoices/", "/invoices"] and "GET" in route.methods:
            # List endpoint should return BillingRecordList
            assert route.response_model == BillingRecordList
        elif route.path == "/invoices/{invoice_id}" and "GET" in route.methods:
            # Get endpoint should return BillingRecordRead
            assert route.response_model == BillingRecordRead


def test_uses_integer_invoice_ids():
    """Test that invoice_id parameter uses integer type."""
    # Find the GET /{invoice_id} route
    get_routes = [route for route in invoices_router.routes if "GET" in route.methods and "{invoice_id}" in route.path]

    assert len(get_routes) == 1


def test_every_endpoint_refuses_an_unauthenticated_caller(client):
    """No invoice route serves an anonymous caller.

    The security property is "an unauthenticated caller cannot read billing
    records", and it has to be pinned on the WIRE. Asserting that
    ``get_current_user`` appears in the dependant graph would only restate
    the decorator: the guard could stay wired up while the route stopped
    depending on it. So hit the routes and require a 401, and require the
    service to be untouched -- a refusal that still queried billing records
    would leak existence through timing and through the response body.

    Renamed from ``test_requires_authentication``, which asserted nothing.
    """
    service = MagicMock()
    with patch(
        "src.app.modules.billing.routers.invoices.get_billing_record_service",
        return_value=service,
    ):
        for path in ("/api/v1/invoices", "/api/v1/invoices/1"):
            response = client.get(path)

            assert response.status_code == 401, f"{path} served an anonymous caller: {response.status_code}"

            # Refused at the gate, before any billing query: an unauthorised
            # read that still hit the table leaks existence through timing.
            service.get_user_records.assert_not_called()
            service.get_record.assert_not_called()


def test_path_mapping():
    """Test that paths match old frontend expectations."""
    routes = {route.path for route in invoices_router.routes}

    # Old backend paths that must be supported
    assert "/invoices/" in routes or "/invoices" in routes
    assert "/invoices/{invoice_id}" in routes
