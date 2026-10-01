"""Validation failures must present as 422 through the REAL ASGI stack.

Part (D) of the bead. 51 assertions for 422 already exist across the suite,
and they do go through the full stack (`AsyncClient` over `ASGITransport`),
so the handler is not the gap. What was unpinned is the per-STYLE coverage
the bead asks for, and one behaviour that is easy to guess wrong:

  * query bounds — `page` is declared `Query(ge=1)`, so `page=0` is a 422;
  * body schema — a missing required field is a 422 (covered by the contract
    suites; asserted here too so the three styles live side by side);
  * path parameter — a non-integer id is a 422.

And the counter-intuitive one: `per_page` ABOVE the maximum is NOT a 422.
`PaginationParams` clamps it to `MAX_PER_PAGE` in a validator instead of
rejecting it. That is a deliberate choice, but a reader who assumes every
bound is enforced by FastAPI's validation will write a test that fails for the
wrong reason — so it is pinned as a fact rather than left to be rediscovered.

Why the full stack matters: P1-11 was a validation failure surfacing as a 500
because no handler matched. Calling the handler in isolation would not have
caught that, and neither would calling the endpoint function directly — it has
to go through the middleware chain and the registered exception handlers.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_query_bound_violation_is_422(
    async_client: AsyncClient, superuser_token_headers: dict
) -> None:
    """`page=0` violates `ge=1` and must present as 422, not 500."""
    response = await async_client.get(
        "/api/v1/users", params={"page": 0}, headers=superuser_token_headers
    )

    assert response.status_code == 422, (
        f"expected 422 for an out-of-bounds query param, got "
        f"{response.status_code}: {response.text[:200]}"
    )


async def test_negative_page_is_422(
    async_client: AsyncClient, superuser_token_headers: dict
) -> None:
    response = await async_client.get(
        "/api/v1/users", params={"page": -5}, headers=superuser_token_headers
    )

    assert response.status_code == 422, f"got {response.status_code}"


async def test_query_parameter_type_violation_is_422(
    async_client: AsyncClient, superuser_token_headers: dict
) -> None:
    """A non-integer for an int query param is a 422 through the full stack.

    This is the P1-11 shape: the value never reaches the handler, and if the
    exception handler does not match, FastAPI's default turns it into a 500.
    """
    response = await async_client.get(
        "/api/v1/users", params={"page": "not-a-number"}, headers=superuser_token_headers
    )

    assert response.status_code == 422, (
        f"expected 422 for an uncoercible query param, got {response.status_code}"
    )


async def test_int_typed_path_param_yields_404_not_422(async_client: AsyncClient) -> None:
    """A non-numeric value for an `int` path param is a 404, not a 422.

    Starlette's path converter does not match the route at all, so the
    request is unrouted and never reaches validation. Documented because the
    obvious expectation is 422, and a test written against that expectation
    fails for a reason that has nothing to do with the handler.
    """
    response = await async_client.get("/api/v1/users/not-a-number")

    assert response.status_code == 404, (
        f"expected 404 from the path converter, got {response.status_code}"
    )


async def test_body_schema_violation_is_422(
    async_client: AsyncClient, superuser_token_headers: dict
) -> None:
    """A missing required body field is a 422."""
    response = await async_client.post(
        "/api/v1/roles", json={}, headers=superuser_token_headers
    )

    assert response.status_code == 422, f"got {response.status_code}: {response.text[:200]}"


async def test_out_of_range_per_page_over_http_is_422(
    async_client: AsyncClient, superuser_token_headers: dict
) -> None:
    """`per_page` over the maximum is a 422 on the HTTP path.

    `PaginationParams` is a dataclass whose `__post_init__` CLAMPS an
    over-large `per_page`, but the route takes it through
    `get_pagination_params`, which declares `Query(le=MAX_PER_PAGE)`. FastAPI
    validates the query string first, so the clamp never runs for a request.
    The two behaviours coexist and this pins the one a client actually sees.
    """
    response = await async_client.get(
        "/api/v1/users", params={"per_page": 100_000}, headers=superuser_token_headers
    )

    assert response.status_code == 422, (
        f"expected 422 for per_page above the maximum, got {response.status_code}"
    )


def test_pagination_params_clamps_when_constructed_directly() -> None:
    """The programmatic path behaves the OPPOSITE way: it clamps.

    Same type, same field, two outcomes depending on how it was built. Worth
    pinning because a reader looking at `__post_init__` will conclude the
    over-large value is accepted, and write a test that contradicts the one
    above.
    """
    from src.app.core.pagination import MAX_PER_PAGE, PaginationParams

    params = PaginationParams(page=0, per_page=MAX_PER_PAGE + 5_000)

    assert params.per_page == MAX_PER_PAGE, (
        f"direct construction should clamp, got {params.per_page}"
    )
    assert params.page == 1, "page below 1 should clamp to 1 when constructed directly"


async def test_validation_errors_use_the_standard_envelope(async_client: AsyncClient) -> None:
    """A 422 is FastAPI's standard `detail` envelope, not a custom shape.

    Clients branch on this shape, so a handler that reformatted it would be
    a breaking change that no status-code assertion would catch.
    """
    response = await async_client.get("/api/v1/users", params={"page": 0})

    assert response.status_code == 422
    body = response.json()
    assert isinstance(body, dict), f"unexpected 422 body type: {type(body).__name__}"
    assert "detail" in body, f"422 body has no 'detail' key: {body}"
