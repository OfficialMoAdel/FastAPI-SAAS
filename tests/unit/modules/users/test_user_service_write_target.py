"""Regression tests for the P1-1 write-target fix in UserService.

`UserService.update`, `update_tier` and `hard_delete` used to filter their
write on `username=db_username`. The pre-flight COUNT inside FastCRUD's
`update`/`delete` does not filter `is_deleted`, while
`uq_user_username_tenant` is a *partial* unique index
`(username, tenant_id) WHERE is_deleted = false` — partial by design, so a
soft-deleted user's username can be recycled (see
`src/migrations/versions/01_foundation.py`). The combination meant a live user
whose username had been recycled from a soft-deleted row resolved fine but
then wrote with a filter that matched TWO rows, raising
`MultipleResultsFound` and turning a valid management call into a 500.

The fix writes by the PK of the row that was already resolved (and already
tenant-matched / ownership-checked), which identifies exactly one row and
costs no extra query. These tests pin the shape: a live row is written by
`id`, and no username-filtered write remains on any of the three paths.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.principal import AuthenticatedUser
from src.app.modules.users.schemas import UserTierUpdate, UserUpdate
from src.app.modules.users.service import UserService

from .test_user_service import mock_db  # noqa: F401  — reused session/CRUD mock


def _active_user_row(user_id: int = 1, username: str = "testuser", tenant_id=None) -> MagicMock:
    """The row `get_user_for_management` returns: live, tenant-scoped, with a PK."""
    row = MagicMock()
    row.id = user_id
    row.username = username
    row.email = "testuser@example.com"
    row.tenant_id = tenant_id
    row.is_deleted = False
    row.is_superuser = False
    row.token_version = 7
    return row


def _superuser(username: str = "admin") -> AuthenticatedUser:
    return AuthenticatedUser(
        id=99, username=username, email=f"{username}@example.com", is_superuser=True, tenant_id=None
    )


def _no_duplicates(*args, **kwargs):
    """Both duplicate probes report 'not taken'."""
    return False


class TestP1OneWriteTargetsPrimaryKey:
    """The write must be addressed by PK, not by the recycled-username filter."""

    @pytest.fixture
    def user_service(self, mock_db):  # noqa: F811
        """Same construction as tests/unit/modules/users/test_user_service.py."""
        return UserService(mock_db)

    @pytest.mark.asyncio
    async def test_update_writes_by_id_not_username(self, user_service):
        """update() targets the resolved row by PK; a recycled username cannot 500."""
        db_user = _active_user_row()
        with (
            patch.object(user_service, "get_user_for_management", new_callable=AsyncMock) as mock_get,
            patch.object(user_service.crud, "get", new_callable=AsyncMock) as mock_crud_get,
            patch.object(user_service, "_email_taken", new_callable=AsyncMock) as mock_email_taken,
            patch.object(user_service, "_username_taken", new_callable=AsyncMock) as mock_username_taken,
            patch.object(user_service.crud, "update", new_callable=AsyncMock) as mock_update,
            patch.object(user_service, "invalidate_sessions", new_callable=AsyncMock) as mock_invalidate,
        ):
            mock_get.return_value = db_user
            mock_crud_get.return_value = db_user
            mock_email_taken.return_value = False
            mock_username_taken.return_value = False
            mock_invalidate.return_value = None

            result = await user_service.update(
                username="testuser",
                values=UserUpdate(name="Renamed"),
                current_user=_superuser(),
            )

        assert result == {"message": "User updated"}
        mock_update.assert_awaited_once()
        _, kwargs = mock_update.await_args
        # PK target: exactly one row is addressed.
        assert kwargs["id"] == 1
        assert kwargs["id"] == db_user.id
        # The recycled-username filter is gone from the write entirely.
        assert "username" not in kwargs
        assert kwargs["object"].name == "Renamed"
        assert kwargs["commit"] is False

    @pytest.mark.asyncio
    async def test_update_tier_writes_by_id_not_username(self, user_service):
        """update_tier() targets the resolved row by PK."""
        db_user = _active_user_row()
        with (
            patch.object(user_service, "get_user_for_management", new_callable=AsyncMock) as mock_get,
            patch.object(user_service.crud, "get", new_callable=AsyncMock) as mock_crud_get,
            patch("src.app.modules.users.service.TierService") as mock_tier_cls,
            patch.object(user_service.crud, "update", new_callable=AsyncMock) as mock_update,
            patch.object(user_service, "invalidate_sessions", new_callable=AsyncMock) as mock_invalidate,
        ):
            mock_get.return_value = db_user
            mock_crud_get.return_value = db_user
            tier_svc = AsyncMock()
            mock_tier_cls.return_value = tier_svc
            tier_svc.get_tier_by_id.return_value = {"id": 2}
            mock_invalidate.return_value = None

            result = await user_service.update_tier(username="testuser", values=UserTierUpdate(tier_id=2))

        assert "updated" in result["message"]
        mock_update.assert_awaited_once()
        _, kwargs = mock_update.await_args
        assert kwargs["id"] == db_user.id
        assert "username" not in kwargs

    @pytest.mark.asyncio
    async def test_hard_delete_writes_by_id_not_username(self, user_service):
        """hard_delete() targets the resolved row by PK."""
        db_user = _active_user_row()
        with (
            patch.object(user_service, "get_user_for_management", new_callable=AsyncMock) as mock_get,
            patch.object(user_service.crud, "get", new_callable=AsyncMock) as mock_crud_get,
            patch.object(user_service.crud, "exists", new_callable=AsyncMock) as mock_exists,
            patch.object(user_service.crud, "db_delete", new_callable=AsyncMock) as mock_delete,
            patch.object(user_service, "_invalidate_user_cache", new_callable=AsyncMock) as mock_invalidate,
        ):
            mock_get.return_value = db_user
            mock_crud_get.return_value = db_user
            mock_exists.return_value = True
            mock_delete.return_value = None
            mock_invalidate.return_value = None

            result = await user_service.hard_delete(username="testuser")

        assert "deleted from the database" in result["message"].lower()
        mock_delete.assert_awaited_once()
        _, kwargs = mock_delete.await_args
        assert kwargs["id"] == db_user.id
        assert "username" not in kwargs

    @pytest.mark.asyncio
    async def test_resolved_row_id_is_the_write_target_for_every_path(self, user_service):
        """All three paths address the SAME row that was resolved.

        Guards against a future refactor that resolves one row and writes to a
        re-derived one: the write target must be the id read off the row the
        ownership/tenant checks were run against.
        """
        db_user = _active_user_row(user_id=4242, username="recycled")
        superuser = _superuser()

        with (
            patch.object(user_service, "get_user_for_management", new_callable=AsyncMock) as mock_get,
            patch.object(user_service.crud, "get", new_callable=AsyncMock) as mock_crud_get,
            patch.object(user_service, "_email_taken", new_callable=AsyncMock) as mock_email_taken,
            patch.object(user_service, "_username_taken", new_callable=AsyncMock) as mock_username_taken,
            patch.object(user_service, "invalidate_sessions", new_callable=AsyncMock),
        ):
            mock_get.return_value = db_user
            mock_crud_get.return_value = db_user
            mock_email_taken.return_value = False
            mock_username_taken.return_value = False

            with patch.object(user_service.crud, "update", new_callable=AsyncMock) as mock_update:
                await user_service.update(
                    username="recycled", values=UserUpdate(name="Renamed"), current_user=superuser
                )
                _, update_kwargs = mock_update.await_args

            with patch("src.app.modules.users.service.TierService") as mock_tier_cls:
                tier_svc = AsyncMock()
                mock_tier_cls.return_value = tier_svc
                tier_svc.get_tier_by_id.return_value = {"id": 3}
                with patch.object(user_service.crud, "update", new_callable=AsyncMock) as mock_tier_update:
                    await user_service.update_tier(username="recycled", values=UserTierUpdate(tier_id=3))
                    _, tier_kwargs = mock_tier_update.await_args

            with (
                patch.object(user_service.crud, "exists", new_callable=AsyncMock) as mock_exists,
                patch.object(user_service.crud, "db_delete", new_callable=AsyncMock) as mock_delete,
                patch.object(user_service, "_invalidate_user_cache", new_callable=AsyncMock),
            ):
                mock_exists.return_value = True
                mock_delete.return_value = None
                await user_service.hard_delete(username="recycled")
                _, delete_kwargs = mock_delete.await_args

        assert update_kwargs["id"] == tier_kwargs["id"] == delete_kwargs["id"] == 4242
