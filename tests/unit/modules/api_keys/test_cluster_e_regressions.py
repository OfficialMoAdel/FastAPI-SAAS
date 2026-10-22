"""Cluster E regressions: what the rotate endpoint REPORTS must be what it DID.

The rotate response used to be built by the router from a bare
``APIKeyResponse``, so ``grace_period_days`` and ``valid_from`` were never
passed and the schema defaults answered for them: every response claimed a
7-day grace window no matter what was asked for, and a request above the
maximum silently reported the default instead of the window that was
actually applied.

These tests pin the reporting contract at the service boundary (the value the
service computed) and at the router boundary (the value the client is told).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.api_keys.models import APIKey
from src.app.modules.api_keys.router import rotate_api_key
from src.app.modules.api_keys.schemas import APIKeyRotateResponse
from src.app.modules.api_keys.service import MAX_GRACE_PERIOD_DAYS, APIKeyService

TENANT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _existing_key() -> MagicMock:
    """A rotatable key: active, no expiry, no scopes."""
    key = MagicMock(spec=APIKey)
    key.id = 1
    key.name = "Production Key"
    key.user_id = 1
    key.tenant_id = TENANT_ID
    key.is_active = True
    key.scopes = None
    key.expires_at = None
    key.hashed_key = "hashed_old"
    return key


def _service() -> tuple[APIKeyService, AsyncMock]:
    db = AsyncMock(spec=AsyncSession)

    async def _refresh(obj, *args, **kwargs):
        obj.id = 456
        return None

    db.refresh.side_effect = _refresh
    return APIKeyService(db), db


async def _rotate(
    svc: APIKeyService,
    db: AsyncMock,
    grace_period_days: int,
) -> APIKeyRotateResponse:
    """Run a rotation with the collaborators patched out."""
    found = MagicMock()
    found.scalar_one_or_none.return_value = _existing_key()
    free = MagicMock()
    free.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(side_effect=[found, free])

    with (
        patch("src.app.modules.api_keys.service.execute_scoped", new=AsyncMock(side_effect=[found, free])),
        patch.object(svc.crud, "update", new_callable=AsyncMock),
        patch("src.app.modules.api_keys.service.audit", new_callable=AsyncMock),
        patch.object(svc, "_generate_key", return_value=("raw-new", "pfxnew", "hashed_new")),
    ):
        return await svc.rotate_api_key(
            key_id=1,
            user_id=1,
            tenant_id=TENANT_ID,
            grace_period_days=grace_period_days,
        )


class TestRotateReportsAppliedGrace:
    """The reported window is the APPLIED one, not the requested one."""

    @pytest.mark.asyncio
    async def test_reports_the_requested_grace_period(self) -> None:
        svc, db = _service()

        result = await _rotate(svc, db, grace_period_days=14)

        assert result.grace_period_days == 14

    @pytest.mark.asyncio
    async def test_reports_zero_grace_period(self) -> None:
        """A zero-day window is a real request and must not read as the default."""
        svc, db = _service()

        result = await _rotate(svc, db, grace_period_days=0)

        assert result.grace_period_days == 0

    @pytest.mark.asyncio
    async def test_reports_the_clamped_grace_period_not_the_oversized_request(self) -> None:
        """Above the maximum, the response states the window that was applied."""
        svc, db = _service()

        result = await _rotate(svc, db, grace_period_days=MAX_GRACE_PERIOD_DAYS + 30)

        assert result.grace_period_days == MAX_GRACE_PERIOD_DAYS

    @pytest.mark.asyncio
    async def test_negative_request_is_clamped_to_zero(self) -> None:
        svc, db = _service()

        result = await _rotate(svc, db, grace_period_days=-5)

        assert result.grace_period_days == 0

    @pytest.mark.asyncio
    async def test_valid_from_is_reported_as_none_for_a_valid_immediately_key(self) -> None:
        """The new key is valid immediately; the grace window is the OLD key's."""
        svc, db = _service()

        result = await _rotate(svc, db, grace_period_days=7)

        assert result.valid_from is None

    @pytest.mark.asyncio
    async def test_audit_records_the_applied_window(self) -> None:
        """The audit trail states the applied window too, not the raw request."""
        svc, db = _service()
        found = MagicMock()
        found.scalar_one_or_none.return_value = _existing_key()
        free = MagicMock()
        free.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(side_effect=[found, free])

        with (
            patch("src.app.modules.api_keys.service.execute_scoped", new=AsyncMock(side_effect=[found, free])),
            patch.object(svc.crud, "update", new_callable=AsyncMock),
            patch("src.app.modules.api_keys.service.audit", new_callable=AsyncMock) as audit_mock,
            patch.object(svc, "_generate_key", return_value=("raw-new", "pfxnew", "hashed_new")),
        ):
            await svc.rotate_api_key(
                key_id=1,
                user_id=1,
                tenant_id=TENANT_ID,
                grace_period_days=MAX_GRACE_PERIOD_DAYS + 30,
            )

        details = audit_mock.await_args.kwargs["details"]
        assert details["grace_period_days"] == MAX_GRACE_PERIOD_DAYS


class TestRotateResponsePassesTheServiceValuesThrough:
    """The router must forward the service's values, not re-derive them."""

    @pytest.mark.asyncio
    async def test_router_forwards_the_service_grace_period(self) -> None:
        ctx = MagicMock()
        ctx.user_id = 1
        ctx.tenant_id = TENANT_ID

        service = MagicMock()
        service.rotate_api_key = AsyncMock(
            return_value=APIKeyRotateResponse(
                id=456,
                name="Production Key (Rotated)",
                key="raw-new",
                key_prefix="pfxnew",
                expires_at=None,
                created_at=datetime.now(UTC),
                valid_from=None,
                grace_period_days=21,
            )
        )

        response = await rotate_api_key(
            key_id=1,
            ctx=ctx,
            service=service,
            grace_period_days=21,
        )

        service.rotate_api_key.assert_awaited_once_with(
            key_id=1,
            user_id=1,
            tenant_id=TENANT_ID,
            grace_period_days=21,
        )
        assert response.grace_period_days == 21
        assert response.valid_from is None


class TestGraceWindowBoundsTheOldKey:
    """The window the response reports is the window the old key actually got."""

    @pytest.mark.asyncio
    async def test_old_key_expiry_matches_the_reported_window(self) -> None:
        svc, db = _service()
        found = MagicMock()
        found.scalar_one_or_none.return_value = _existing_key()
        free = MagicMock()
        free.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(side_effect=[found, free])

        before = datetime.now(UTC)
        with (
            patch("src.app.modules.api_keys.service.execute_scoped", new=AsyncMock(side_effect=[found, free])),
            patch.object(svc.crud, "update", new_callable=AsyncMock) as update_mock,
            patch("src.app.modules.api_keys.service.audit", new_callable=AsyncMock),
            patch.object(svc, "_generate_key", return_value=("raw-new", "pfxnew", "hashed_new")),
        ):
            result = await svc.rotate_api_key(
                key_id=1,
                user_id=1,
                tenant_id=TENANT_ID,
                grace_period_days=3,
            )
        after = datetime.now(UTC)

        old_key_expires_at = update_mock.call_args[1]["object"]["expires_at"]
        assert before + timedelta(days=3) <= old_key_expires_at <= after + timedelta(days=3)
        assert result.grace_period_days == 3
