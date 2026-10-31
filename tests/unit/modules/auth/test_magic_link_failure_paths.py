"""R2-21 / R2-22 / R2-23: magic-link response honesty, retryable DB failures,
and token cleanup.

Each of these is about the *contract at the edges* of the flow, which the
existing tests (schema shape, atomic redemption) do not cover:

* R2-21 — ``may_be_delayed`` was hardcoded ``False`` on every path, so the field
  lied. It must be the SAME value everywhere, including the anti-enumeration
  padding returns, or it becomes the enumeration oracle the pad prevents.
* R2-22 — a transient DB failure was reported as a bad token (400), telling the
  client to stop retrying and masking the 503 the pool-timeout handler produces.
* R2-23 — the Redis token was minted before the tenant guard, so a failed setup
  left a live 15-minute credential behind.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from src.app.modules.auth.services.magic_link import MagicLinkService
from src.app.shared.exceptions import ServiceUnavailableError

SERVICE = "src.app.modules.auth.services.magic_link"


@pytest.fixture
def service() -> MagicLinkService:
    return MagicLinkService(db=AsyncMock(), redis=AsyncMock())


@pytest.fixture(autouse=True)
def _fast_padding():
    """The enumeration pad is a 0.15s sleep; the assertion is about the FIELD."""

    async def _no_sleep(_seconds: float) -> None:
        return None

    with patch(f"{SERVICE}.asyncio.sleep", _no_sleep):
        yield


def _manager(*, peek: dict | None = None) -> MagicMock:
    manager = MagicMock()
    manager.create = AsyncMock()
    manager.delete = AsyncMock(return_value=True)
    manager.peek = AsyncMock(return_value=peek)
    manager.consume = AsyncMock(return_value=peek)
    return manager


# ============================================================================
# R2-21: one value on every path
# ============================================================================


class TestMayBeDelayedIsHonestAndUniform:
    async def test_unknown_email_reports_the_delay_flag(self, service) -> None:
        with patch.object(service, "_get_user_by_email", AsyncMock(return_value=None)):
            response = await service.send_magic_link("nobody@example.com", "1.2.3.4", "ua")

        assert response.may_be_delayed is True

    async def test_inactive_user_reports_the_same_flag(self, service) -> None:
        user = {"id": 7, "tenant_id": "t", "is_deleted": False, "is_active": False}
        with patch.object(service, "_get_user_by_email", AsyncMock(return_value=user)):
            response = await service.send_magic_link("inactive@example.com", "1.2.3.4", "ua")

        assert response.may_be_delayed is True

    async def test_queued_email_reports_the_same_flag(self, service) -> None:
        user = {"id": 7, "tenant_id": "t", "is_deleted": False, "is_active": True}
        with (
            patch.object(service, "_get_user_by_email", AsyncMock(return_value=user)),
            patch.object(service, "_get_magic_link_manager", return_value=_manager()),
            patch.object(service, "_queue_magic_link_email", AsyncMock()),
            patch("src.app.modules.auth.crud.create_magic_link", AsyncMock()),
        ):
            response = await service.send_magic_link("real@example.com", "1.2.3.4", "ua")

        assert response.may_be_delayed is True, "the success path is queued, not sent inline"

    async def test_all_paths_agree_so_the_field_cannot_enumerate(self, service) -> None:
        """A differing value between paths would BE the enumeration oracle."""
        user = {"id": 7, "tenant_id": "t", "is_deleted": False, "is_active": True}
        with patch.object(service, "_get_user_by_email", AsyncMock(return_value=None)):
            unknown = await service.send_magic_link("nobody@example.com", "1.2.3.4", "ua")
        with (
            patch.object(service, "_get_user_by_email", AsyncMock(return_value=user)),
            patch.object(service, "_get_magic_link_manager", return_value=_manager()),
            patch.object(service, "_queue_magic_link_email", AsyncMock()),
            patch("src.app.modules.auth.crud.create_magic_link", AsyncMock()),
        ):
            known = await service.send_magic_link("real@example.com", "1.2.3.4", "ua")

        assert unknown.may_be_delayed == known.may_be_delayed
        assert unknown.message == known.message


# ============================================================================
# R2-22: a retryable outage is a 503, not a bad token
# ============================================================================


class TestTransientDbFailureIsRetryable:
    async def test_operational_error_maps_to_service_unavailable(self, service) -> None:
        manager = _manager(peek={"email": "u@example.com", "extra_data": {"user_id": 1}})
        with (
            patch.object(service, "_get_magic_link_manager", return_value=manager),
            patch(
                "src.app.modules.auth.crud.get_magic_link_by_token",
                AsyncMock(side_effect=OperationalError("SELECT 1", {}, Exception("connection lost"))),
            ),
            pytest.raises(ServiceUnavailableError),
        ):
            await service.verify_magic_link(token="tok", ip_address="1.2.3.4")

    async def test_an_unexpected_defect_is_not_reported_as_a_bad_token(self, service) -> None:
        manager = _manager(peek={"email": "u@example.com", "extra_data": {"user_id": 1}})
        with (
            patch.object(service, "_get_magic_link_manager", return_value=manager),
            patch(
                "src.app.modules.auth.crud.get_magic_link_by_token",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
            pytest.raises(RuntimeError, match="boom"),
        ):
            await service.verify_magic_link(token="tok", ip_address="1.2.3.4")


# ============================================================================
# R2-23: no token may outlive the failure that orphaned it
# ============================================================================


class TestNoOrphanedToken:
    async def test_a_missing_tenant_never_mints_a_token(self, service) -> None:
        user = {"id": 7, "tenant_id": None, "is_deleted": False, "is_active": True}
        manager = _manager()
        with (
            patch.object(service, "_get_user_by_email", AsyncMock(return_value=user)),
            patch.object(service, "_get_magic_link_manager", return_value=manager),
            pytest.raises(ServiceUnavailableError),
        ):
            await service.send_magic_link("u@example.com", "1.2.3.4", "ua")

        manager.create.assert_not_awaited()
        manager.delete.assert_not_awaited()

    async def test_a_db_failure_discards_the_token_it_orphaned(self, service) -> None:
        user = {"id": 7, "tenant_id": "t", "is_deleted": False, "is_active": True}
        manager = _manager()
        with (
            patch.object(service, "_get_user_by_email", AsyncMock(return_value=user)),
            patch.object(service, "_get_magic_link_manager", return_value=manager),
            patch(
                "src.app.modules.auth.crud.create_magic_link",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
            pytest.raises(ServiceUnavailableError),
        ):
            await service.send_magic_link("u@example.com", "1.2.3.4", "ua")

        manager.create.assert_awaited_once()
        manager.delete.assert_awaited_once()
        discarded = manager.delete.await_args.args[0]
        assert discarded == manager.create.await_args.kwargs["token"]

    async def test_an_email_queue_failure_discards_the_token(self, service) -> None:
        user = {"id": 7, "tenant_id": "t", "is_deleted": False, "is_active": True}
        manager = _manager()
        with (
            patch.object(service, "_get_user_by_email", AsyncMock(return_value=user)),
            patch.object(service, "_get_magic_link_manager", return_value=manager),
            patch("src.app.modules.auth.crud.create_magic_link", AsyncMock()),
            patch.object(
                service, "_queue_magic_link_email", AsyncMock(side_effect=RuntimeError("outbox down"))
            ),
            pytest.raises(ServiceUnavailableError),
        ):
            await service.send_magic_link("u@example.com", "1.2.3.4", "ua")

        manager.delete.assert_awaited_once()

    async def test_a_cleanup_failure_does_not_mask_the_real_error(self, service) -> None:
        user = {"id": 7, "tenant_id": "t", "is_deleted": False, "is_active": True}
        manager = _manager()
        manager.delete = AsyncMock(side_effect=RuntimeError("redis gone"))
        with (
            patch.object(service, "_get_user_by_email", AsyncMock(return_value=user)),
            patch.object(service, "_get_magic_link_manager", return_value=manager),
            patch(
                "src.app.modules.auth.crud.create_magic_link",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
            pytest.raises(ServiceUnavailableError),
        ):
            await service.send_magic_link("u@example.com", "1.2.3.4", "ua")
