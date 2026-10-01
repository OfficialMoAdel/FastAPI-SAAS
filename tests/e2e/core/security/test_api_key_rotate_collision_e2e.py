"""E2E: API key rotation name-collision contract.

``api_keys`` carries ``UniqueConstraint("name", "tenant_id",
name="uq_api_key_name_tenant")`` and the rotated name is derived
deterministically (``f"{base_name[:80]} (Rotated)"``), so rotating a key
that is itself the product of a rotation re-derives a name that is already
taken. Before K-R1-02 that reached the DB as an unhandled
``IntegrityError`` and surfaced as a 500 on a legitimate operation.

The contract exercised here: a re-rotation is a 409, never a 500, and the
keys that were successfully issued keep working.

HTTP-level assertions ride the shared ``async_client`` + ``auth_headers``
fixtures (their tenant-middleware / rate-limit / principal mocking is the
proven path). The grace-window key-validity property is driven through
``APIKeyService`` against a tenant row materialised in the test's own
session — ``_validate_from_db`` JOINs Tenant, and the mocked principal has
no such row in the shared SQLite DB.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.api_keys.models import APIKey
from src.app.modules.api_keys.service import APIKeyService

pytestmark = pytest.mark.e2e


async def _create_key(client: AsyncClient, headers: dict, name: str) -> dict:
    resp = await client.post(
        "/api/v1/api-keys",
        json={"name": name, "scopes": ["read:users"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _rotate(client: AsyncClient, headers: dict, key_id: int) -> tuple[int, dict]:
    resp = await client.post(
        f"/api/v1/api-keys/{key_id}/rotate?grace_period_days=7",
        headers=headers,
    )
    return resp.status_code, resp.json()




@pytest.fixture
async def isolated_principal(get_test_app):
    """A client + headers bound to a dedicated principal and tenant.

    The shared ``auth_headers`` fixture is user id 1 in tenant
    ``test-tenant-integration``; every other e2e module creates keys there.
    ``list_api_keys`` is tenant-scoped, so this module's keys are kept under
    their OWN tenant id to stay out of those shared key lists. The tenant
    middleware is bypassed exactly as ``full_mock_context`` does, so the
    slug header value is not resolved from the DB.
    """
    from unittest.mock import AsyncMock, Mock, patch

    from src.app.core.db.database import async_get_db
    from tests.conftest import MockUser, override_get_db
    from tests.helpers.auth import install_test_principal

    app = get_test_app
    app.dependency_overrides[async_get_db] = override_get_db

    tenant_id = uuid.uuid4()
    fake_tenant = Mock()
    fake_tenant.id = tenant_id
    fake_tenant.slug = "rotation-tenant"
    fake_tenant.is_active = True
    fake_tenant.tier_id = 1
    principal = MockUser(id=987_654, tenant_id=tenant_id)
    install_test_principal(app, principal, fake_tenant)

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.cache.warmer.warm_caches", new_callable=AsyncMock),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
    ):
        from httpx import ASGITransport
        from httpx import AsyncClient as HttpxClient

        async with HttpxClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, {"X-Tenant-ID": fake_tenant.slug}

    app.dependency_overrides.clear()


class TestRotateTwice:
    """Re-rotation must not 500."""

    async def test_second_rotation_of_the_same_key_is_not_500(
        self, isolated_principal: tuple[AsyncClient, dict]
    ) -> None:
        """Rotating twice derives the same name; the second is a 409, not a 500."""
        client, headers = isolated_principal
        original = await _create_key(client, headers, "RotateTwice Key")

        first_status, first_body = await _rotate(client, headers, original["id"])
        assert first_status == 200, first_body
        first_name = first_body["name"]
        assert first_name == "RotateTwice Key (Rotated)"

        # The rotation product now holds the derived name; rotating it again
        # strips "(Rotated)" back to the base and re-derives the SAME name.
        second_status, second_body = await _rotate(client, headers, first_body["id"])

        assert second_status in (200, 409), (
            f"expected 409 (name collision) or 200 (distinct name), got {second_status}: {second_body!r}"
        )
        if second_status == 409:
            code = second_body.get("error", {}).get("code") or second_body.get("code")
            assert code == "ALREADY_EXISTS", second_body
        else:
            assert second_body["name"] != first_name, (
                f"a successful second rotation must not reuse the name {first_name!r}"
            )

    async def test_re_collision_reports_duplicate_naming_the_field(
        self, isolated_principal: tuple[AsyncClient, dict]
    ) -> None:
        """A colliding re-rotation names the offending field/value in the 409."""
        client, headers = isolated_principal
        original = await _create_key(client, headers, "NoSideEffect Key")
        first_status, first_body = await _rotate(client, headers, original["id"])
        assert first_status == 200

        second_status, second_body = await _rotate(client, headers, first_body["id"])
        assert second_status != 500, second_body

        if second_status == 409:
            details = second_body.get("error", {}).get("details") or {}
            assert details.get("field") == "name", second_body
            assert details.get("value") == "NoSideEffect Key (Rotated)", second_body
        else:
            assert second_status == 200
            assert second_body["name"] != first_body["name"]


class TestBothKeysWorkAfterRotation:
    """The counter-test: a successful rotation keeps both keys usable.

    Driven at the service layer so the grace-window JOIN against Tenant can
    resolve: the mocked principal has no Tenant row in the shared SQLite DB,
    so we materialise one bound to the key's tenant id in this test's own
    session and validate through ``APIKeyService`` directly.
    """

    async def test_original_and_rotated_key_both_validate(
        self, isolated_principal: tuple[AsyncClient, dict], db_session: AsyncSession
    ) -> None:
        """During the grace window both the old and the new key validate."""
        client, headers = isolated_principal
        original = await _create_key(client, headers, "BothWork Key")
        original_key = original["key"]

        status_code, rotated = await _rotate(client, headers, original["id"])
        assert status_code == 200, rotated
        rotated_key = rotated["key"]
        assert rotated_key != original_key

        # Bind a live Tenant row to the tenant the service stamped on the
        # rows so _validate_from_db's JOIN finds it.
        old_row = await _api_key_row(db_session, original["id"])
        await _ensure_live_tenant(db_session, old_row.tenant_id)

        service = APIKeyService(db_session)

        new_info = await service.validate_api_key(rotated_key)
        assert new_info is not None, "the rotated key must validate"
        assert new_info["key_id"] == rotated["id"]

        # The old key's expires_at was pushed to now + grace, so it is still
        # inside the window and must still validate.
        assert old_row.is_active is True
        assert _as_utc(old_row.expires_at) > datetime.now(UTC), (
            f"old key should still be inside its grace window, expires_at={old_row.expires_at}"
        )
        old_info = await _validate_with_tz_normalised(service, original_key, old_row)
        assert old_info is not None, "the original key must still validate during the grace window"
        assert old_info["key_id"] == original["id"]

    async def test_rejected_re_rotation_leaves_both_keys_usable(
        self, isolated_principal: tuple[AsyncClient, dict], db_session: AsyncSession
    ) -> None:
        """A re-rotation that is rejected must not invalidate anything."""
        client, headers = isolated_principal
        original = await _create_key(client, headers, "RejectSafe Key")
        original_key = original["key"]

        first_status, first_body = await _rotate(client, headers, original["id"])
        assert first_status == 200

        second_status, _ = await _rotate(client, headers, first_body["id"])
        assert second_status != 500

        old_row = await _api_key_row(db_session, original["id"])
        await _ensure_live_tenant(db_session, old_row.tenant_id)

        service = APIKeyService(db_session)
        assert await service.validate_api_key(first_body["key"]) is not None, (
            "a rejected re-rotation must not disturb the key it was aimed at"
        )
        old_info = await _validate_with_tz_normalised(service, original_key, old_row)
        assert old_info is not None, (
            "a rejected re-rotation must not invalidate the original key"
        )
        assert old_info["key_id"] == original["id"]


# --- helpers ---------------------------------------------------------------


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; Postgres returns tz-aware ones."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _api_key_row(db_session: AsyncSession, key_id: int) -> APIKey:
    result = await db_session.execute(select(APIKey).where(APIKey.id == key_id))
    row = result.scalar_one_or_none()
    assert row is not None, f"api_keys row {key_id} should exist"
    return row


async def _ensure_live_tenant(db_session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Materialise an active Tenant row for ``tenant_id`` if absent.

    ``_validate_from_db`` JOINs Tenant for its ``is_active`` check; the
    mocked e2e principal's tenant has no row in the shared SQLite DB, so
    without this the join yields nothing and every validation returns None.
    """
    from src.app.core.tenancy.models import require_tenant_model

    tenant_model = require_tenant_model()
    existing = await db_session.execute(
        select(tenant_model).where(tenant_model.id == tenant_id)
    )
    if existing.scalar_one_or_none() is not None:
        return
    tenant = tenant_model(name="Rotation Tenant", slug=f"rotcoll-{uuid.uuid4().hex[:8]}", is_active=True)
    # ``Tenant.id`` is ``init=False`` (uuid7 default); assign after construction.
    tenant.id = tenant_id
    db_session.add(tenant)
    await db_session.commit()


async def _validate_with_tz_normalised(service: APIKeyService, raw_key: str, row: APIKey):
    """Validate a key whose row carries a naive ``expires_at``.

    SQLite hands back naive datetimes while the column is
    ``DateTime(timezone=True)``; ``_validate_from_db`` compares against an
    aware ``datetime.now(UTC)``. Attaching the zone lets the comparison run
    so the grace-window property is assertable under SQLite.
    """
    if row.expires_at is not None and row.expires_at.tzinfo is None:
        row.expires_at = row.expires_at.replace(tzinfo=UTC)
    return await service.validate_api_key(raw_key)
