"""Auth contract for the GLOBAL tier catalog reads.

Run-3 pgo0.1 (``tiers.catalog_reads_are_anonymous`` /
``rate_limits.catalog_reads_are_anonymous``): every mutating route in
``tiers/router.py`` and ``rate_limits/router.py`` is superuser-gated, but the
four catalog READS (``GET /tiers``, ``GET /tiers/{name}``,
``GET /tiers/{tier_name}/rate-limits`` and its ``/{id}`` sibling) carried no
authentication at all. ``Tier`` is a GLOBAL catalog (no ``tenant_id``), so this
is NOT a cross-tenant disclosure — the defect is the read/write asymmetry, and
it is the same class the project already fixed for the billing catalog
(``billing.catalog_reads_scope_from_unauthenticated_header``, pgo0.1).

The resolved contract pinned here:
* an anonymous GET on all four reads is refused, and the refusal body carries
  no catalog internals (a 404 naming the tier would still be a disclosure —
  it confirms the catalog's contents to an anonymous caller);
* a caller-supplied ``X-Tenant-ID`` does NOT substitute for authentication;
* an authenticated principal — including a plain non-superuser — reads the
  catalog AND its seeded rows. This is the anti-over-fix guard: a superuser
  gate on the reads would deny every ordinary tenant member the tiers their own
  subscription is priced from, a worse defect than the one being fixed;
* the superuser gate on the sibling writes is unchanged, and still refuses a
  plain (non-superuser) member.

Every anonymous probe sends a valid ``X-Tenant-ID``: the tenant middleware
resolves the tenant before the auth dependency runs, so omitting the header
would yield a 400 about the header rather than exercising the auth contract.
"""

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.main import app
from src.app.modules.rate_limits.models import RateLimit
from src.app.modules.tiers.models import Tier
from tests.conftest import MockUser, get_testing_session_local, override_get_db
from tests.helpers.auth import clear_test_principal, install_test_principal

#: The tier row seeded for the authenticated reads to find.
TIER_NAME = "auth-probe-tier"
#: The rate-limit row seeded under that tier.
RL_NAME = "auth-probe-rl"
#: The seeded rate limit's path (unique per tier — see ``uq_rate_limit_tier_path``).
RL_PATH = "/api/v1/posts"
#: A second rate-limit name, used only by the write-route probes.
RL_WRITE_NAME = "auth-probe-rl-post"
#: A second tier name, used only by the write-route probes.
TIER_WRITE_NAME = "auth-probe-write"

#: The four reads under test. ``{tier}``/``{rl}`` are filled from the seed.
CATALOG_READS = [
    "/api/v1/tiers",
    "/api/v1/tiers/{tier}",
    "/api/v1/tiers/{tier}/rate-limits",
    "/api/v1/tiers/{tier}/rate-limits/{rl}",
]

#: Mutating routes whose superuser gate must not regress: (path, method, body).
WRITE_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("/api/v1/tiers", "post", {"name": TIER_WRITE_NAME, "description": "probe"}),
    ("/api/v1/tiers/{tier}", "patch", {"description": "probe"}),
    ("/api/v1/tiers/{tier}", "delete", None),
    (
        "/api/v1/tiers/{tier}/rate-limits",
        "post",
        {"name": RL_WRITE_NAME, "path": "/api/v1/tasks", "limit": 1, "period": 60},
    ),
    ("/api/v1/tiers/{tier}/rate-limits/{rl}", "patch", {"limit": 2}),
    ("/api/v1/tiers/{tier}/rate-limits/{rl}", "delete", None),
]

#: Substrings that would mean a denial disclosed catalog internals. The tier
#: name is a substring of the probe paths, so this checks the BODY only.
LEAK_MARKERS = (TIER_NAME, RL_NAME, '"limit"', '"period"')


def _tenant_headers() -> dict[str, str]:
    """A resolvable tenant identity, so auth is what denies the caller."""
    return {"X-Tenant-ID": str(MockUser.DEFAULT_TENANT_ID)}


def _url(path: str, rate_limit_id: int) -> str:
    return path.format(tier=TIER_NAME, rl=rate_limit_id)


def _served_marker(path: str) -> str:
    """The seeded identifier this read's 200 body must contain.

    The two ``/rate-limits`` reads serialize ``RateLimitRead`` (the rate
    limit's own name plus ``tier_id``), not the tier — asserting the TIER name
    there would fail against a correct response.
    """
    return RL_NAME if "rate-limits" in path else TIER_NAME


def _session() -> AsyncIterator[AsyncSession]:
    """An async session on the same test DB the app's own sessions use."""
    return get_testing_session_local()()


def _install_plain_member() -> None:
    """Install an authenticated, NON-superuser principal on the shared app."""
    install_test_principal(
        app,
        MockUser(
            id=9001,
            username="plainmember",
            email="plain-member@example.com",
            is_superuser=False,
            is_active=True,
        ),
        str(MockUser.DEFAULT_TENANT_ID),
    )


async def _clear(session: AsyncSession) -> None:
    await session.execute(delete(RateLimit).where(RateLimit.name.in_([RL_NAME, RL_WRITE_NAME])))
    await session.execute(delete(Tier).where(Tier.name.in_([TIER_NAME, TIER_WRITE_NAME])))


async def _seed() -> int:
    """Insert the probe tier + rate limit; return the rate limit's id."""
    async with _session() as session:
        await _clear(session)
        await session.commit()
        tier = Tier(name=TIER_NAME)
        session.add(tier)
        await session.flush()
        rate_limit = RateLimit(name=RL_NAME, path=RL_PATH, limit=5, period=60, tier_id=tier.id)
        session.add(rate_limit)
        await session.commit()
        return rate_limit.id


@pytest.fixture
async def seeded_catalog() -> AsyncIterator[int]:
    """Seed the probe rows so the counter-tests can prove real data is served.

    Without real rows the reads 404 for everyone and a counter-test could not
    tell "reachable by a member" apart from "reached an empty table". Yields the
    seeded rate-limit id, which the ``/{id}`` read and the writes are probed with.
    """
    rate_limit_id = await _seed()
    try:
        yield rate_limit_id
    finally:
        async with _session() as session:
            await _clear(session)
            await session.commit()


@pytest.fixture
def plain_member_client() -> Iterator[TestClient]:
    """An authenticated, non-superuser principal served over the real router.

    Mirrors the middleware patches the standard ``client_auth`` fixture applies
    (tenant resolution + rate limiter). Without the tenant patch the middleware
    rejects the request with 403 "Access denied" BEFORE the route is reached,
    which would test the middleware instead of the auth gate.
    """
    app.dependency_overrides[async_get_db] = override_get_db
    fake_tenant = Mock()
    fake_tenant.id = MockUser.DEFAULT_TENANT_ID
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.name = "Test Tenant"
    fake_tenant.is_active = True
    fake_tenant.tier_id = 1
    _install_plain_member()
    with (
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        patch(
            "src.app.core.rbac.dependencies.has_permission_cached",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        try:
            with TestClient(app, headers={"User-Agent": "TestClient/1.0"}) as _client:
                yield _client
        finally:
            clear_test_principal(app)
            app.dependency_overrides.pop(async_get_db, None)


# --------------------------------------------------------------------------
# Anonymous callers are refused, and the refusal says nothing about the catalog
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", CATALOG_READS)
def test_catalog_read_rejects_anonymous(
    unauthenticated_client: TestClient, seeded_catalog: int, path: str
) -> None:
    """Anonymous GET on a catalog read must be denied (401/403) — not 404.

    The 404 exclusion is an explicit assertion, not incidental: the seeded rows
    EXIST, so a 404 would mean the anonymous caller reached the service layer
    and was answered from the catalog instead of refused at the gate.
    """
    url = _url(path, seeded_catalog)
    response = unauthenticated_client.get(url, headers=_tenant_headers())
    assert response.status_code in (401, 403), (
        f"{url} served anonymously: status={response.status_code} body={response.text[:300]}"
    )


@pytest.mark.parametrize("path", CATALOG_READS)
def test_anonymous_refusal_leaks_no_catalog_rows(
    unauthenticated_client: TestClient, seeded_catalog: int, path: str
) -> None:
    """The denial must not disclose the tier name, rate limit, or policy values."""
    url = _url(path, seeded_catalog)
    response = unauthenticated_client.get(url, headers=_tenant_headers())
    body = response.text
    leaked = [marker for marker in LEAK_MARKERS if marker in body]
    assert not leaked, f"{url} denial leaked catalog internals {leaked}: {body[:300]}"


def test_tenant_header_does_not_substitute_for_authentication(
    unauthenticated_client: TestClient, seeded_catalog: int
) -> None:
    """A caller-supplied X-Tenant-ID must not unlock catalog reads."""
    response = unauthenticated_client.get("/api/v1/tiers", headers=_tenant_headers())
    assert response.status_code in (401, 403), (
        f"tenant header bypassed authentication: {response.status_code} {response.text[:300]}"
    )


# --------------------------------------------------------------------------
# COUNTER-TESTS: authentication must not cost legitimate reads
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", CATALOG_READS)
def test_authenticated_principal_reads_seeded_catalog(
    client_auth: TestClient, seeded_catalog: int, path: str
) -> None:
    """A superuser reads the seeded catalog rows on all four reads."""
    url = _url(path, seeded_catalog)
    response = client_auth.get(url, headers=_tenant_headers())
    assert response.status_code == 200, f"{url} denied an authenticated principal: {response.text[:300]}"
    assert _served_marker(path) in response.text, f"{url} did not serve the seeded catalog: {response.text[:300]}"


@pytest.mark.parametrize("path", CATALOG_READS)
def test_plain_tenant_member_reads_seeded_catalog(
    plain_member_client: TestClient, seeded_catalog: int, path: str
) -> None:
    """Anti-over-fix: a plain member reads the catalog too.

    This is the guard that fails if someone 'fixes' the reads by copying the
    sibling writes' ``get_current_superuser`` gate: the catalog is global, so
    every tenant member's own tier must be readable to them.
    """
    url = _url(path, seeded_catalog)
    response = plain_member_client.get(url, headers=_tenant_headers())
    assert response.status_code == 200, (
        f"{url} denied a plain tenant member (over-fix to a superuser gate): "
        f"{response.status_code} {response.text[:300]}"
    )
    assert _served_marker(path) in response.text, f"{url} did not serve the seeded catalog: {response.text[:300]}"


# --------------------------------------------------------------------------
# The superuser gate on the sibling writes is unchanged
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path,method,body", WRITE_ROUTES)
def test_write_routes_still_reject_anonymous(
    unauthenticated_client: TestClient, seeded_catalog: int, path: str, method: str, body: dict[str, Any] | None
) -> None:
    """The pre-existing superuser gate on every write must not regress."""
    url = _url(path, seeded_catalog)
    response = unauthenticated_client.request(method, url, json=body, headers=_tenant_headers())
    assert response.status_code in (401, 403), (
        f"{method.upper()} {url} lost its superuser gate: "
        f"status={response.status_code} body={response.text[:300]}"
    )


@pytest.mark.parametrize("path,method,body", WRITE_ROUTES)
def test_write_routes_reject_plain_tenant_member(
    plain_member_client: TestClient, seeded_catalog: int, path: str, method: str, body: dict[str, Any] | None
) -> None:
    """Anti-over-fix in the other direction: authentication alone must not open writes."""
    url = _url(path, seeded_catalog)
    response = plain_member_client.request(method, url, json=body, headers=_tenant_headers())
    assert response.status_code in (401, 403), (
        f"{method.upper()} {url} accepted a non-superuser: {response.status_code} {response.text[:300]}"
    )


@pytest.mark.asyncio
async def test_seed_reaches_the_database() -> None:
    """Guard the guard: the seed must be visible to the reads' own session.

    Without this, a broken seed would make every read 404 and the counter-tests
    would pass for the wrong reason.
    """
    rate_limit_id = await _seed()
    try:
        async with _session() as session:
            tier = await session.execute(select(Tier).where(Tier.name == TIER_NAME))
            assert tier.scalar_one_or_none() is not None, "seed did not insert the tier"
            rate_limit = await session.execute(select(RateLimit).where(RateLimit.name == RL_NAME))
            assert rate_limit.scalar_one_or_none() is not None, "seed did not insert the rate limit"
            assert rate_limit_id > 0
    finally:
        async with _session() as session:
            await _clear(session)
            await session.commit()
