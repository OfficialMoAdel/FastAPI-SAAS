"""S-06 — deleting a user must not revoke the CALLER's credentials.

`erase_user` / `erase_db_user` declared `token: str = Depends(oauth2_scheme)`
and threaded it into `UserService.delete(...)` / `.hard_delete(...)`, which
passed it to `blacklist_token`. That token is the **operator's own** bearer
credential, so a superuser deleting somebody else revoked their OWN session on
every call — while the deleted user's tokens survived wherever a token_version
check was not reached.

The comment claimed the call prevented "a deleted user [keeping] a valid
token". It did the opposite. It was also redundant: the service already calls
the DELETION session-invalidation seam, which bumps the target's
`token_version` and therefore invalidates every one of that user's tokens.

These tests assert the corrected contract: the caller's token is never
blacklisted, and the `token` parameter no longer exists on either service
method.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.users.service import UserService


def test_delete_signature_has_no_token_parameter() -> None:
    """The `token` parameter is gone — nothing needed it."""
    params = inspect.signature(UserService.delete).parameters
    assert "token" not in params, f"delete still takes {list(params)}"


def test_hard_delete_signature_has_no_token_parameter() -> None:
    params = inspect.signature(UserService.hard_delete).parameters
    assert "token" not in params, f"hard_delete still takes {list(params)}"


@pytest.fixture
def service() -> UserService:
    svc = UserService(MagicMock())
    svc.db = MagicMock()
    svc.db.execute = AsyncMock()
    return svc


@pytest.fixture
def target_user() -> Any:
    user = MagicMock()
    user.id = 99
    user.username = "victim"
    user.email = "victim@example.com"
    user.tenant_id = None
    user.token_version = 3
    return user


class TestCallerTokenIsNeverBlacklisted:
    @pytest.mark.asyncio
    async def test_delete_does_not_call_blacklist_token(self, service, target_user) -> None:
        """The wrong call is gone — asserted by making it explode if re-added."""
        with (
            patch.object(service, "get_user_for_management", AsyncMock(return_value=target_user)),
            patch.object(service, "invalidate_sessions", AsyncMock()),
            patch("src.app.modules.users.service.audit", AsyncMock()),
            patch("src.app.modules.users.service.require_tenant_match", lambda *a, **k: None),
            patch(
                "src.app.modules.users.service.blacklist_token",
                AsyncMock(side_effect=AssertionError("S-06: blacklist_token must not be called")),
                create=True,
            ),
        ):
            operator = MagicMock()
            operator.username = "operator"
            operator.is_superuser = True
            operator.id = 1

            result = await service.delete("victim", operator)

        assert "deleted" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_hard_delete_does_not_call_blacklist_token(self, service, target_user) -> None:
        with (
            patch.object(service, "get_user_for_management", AsyncMock(return_value=target_user)),
            patch.object(service.crud, "db_delete", AsyncMock(return_value=None)),
            patch.object(service, "_invalidate_user_cache", AsyncMock()),
            patch("src.app.modules.users.service.require_tenant_match", lambda *a, **k: None),
            patch(
                "src.app.modules.users.service.blacklist_token",
                AsyncMock(side_effect=AssertionError("S-06: blacklist_token must not be called")),
                create=True,
            ),
        ):
            result = await service.hard_delete("victim")

        assert "deleted from the database" in result["message"].lower()


class TestTargetIsStillRevoked:
    @pytest.mark.asyncio
    async def test_delete_invalidates_target_sessions_with_DELETION(self, service, target_user) -> None:
        """The target's credentials are revoked via the canonical DELETION seam.

        This is the part that makes the removed blacklist_token call safe to
        delete: token_version is bumped, so every one of the target's tokens
        stops validating.
        """
        with (
            patch.object(service, "get_user_for_management", AsyncMock(return_value=target_user)),
            patch.object(service, "invalidate_sessions", AsyncMock()) as invalidate,
            patch("src.app.modules.users.service.audit", AsyncMock()),
            patch("src.app.modules.users.service.require_tenant_match", lambda *a, **k: None),
        ):
            operator = MagicMock()
            operator.username = "operator"
            operator.is_superuser = True
            operator.id = 1

            await service.delete("victim", operator)

        invalidate.assert_awaited_once()
        args = invalidate.await_args
        assert args[0][0] is target_user
        assert args[0][1].value == "deletion"

    @pytest.mark.asyncio
    async def test_hard_delete_seeds_post_delete_token_version(self, service, target_user) -> None:
        """Hard delete seeds the post-delete version explicitly (row is gone)."""
        with (
            patch.object(service, "get_user_for_management", AsyncMock(return_value=target_user)),
            patch.object(service.crud, "db_delete", AsyncMock(return_value=None)),
            patch.object(service, "_invalidate_user_cache", AsyncMock()) as inv,
            patch("src.app.modules.users.service.require_tenant_match", lambda *a, **k: None),
        ):
            await service.hard_delete("victim")

        inv.assert_awaited_once()
        assert inv.await_args.kwargs["explicit_token_version"] == 4  # 3 + 1
