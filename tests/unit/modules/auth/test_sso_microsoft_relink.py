"""Microsoft SSO: legacy Graph object ids are upgraded to the OIDC subject."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.auth.services import sso as sso_module
from src.app.modules.auth.services.sso import SSOService


async def _relink(legacy: object | None, conflict: object | None = None):
    db = AsyncMock()
    user_info = MagicMock()
    user_info.provider_user_id = "oidc-sub"
    user_info.email = "user@contoso.com"

    with (
        patch.object(
            sso_module.crud_user_sso_connections,
            "get_by_provider_email",
            AsyncMock(return_value=legacy),
        ),
        patch.object(
            sso_module.crud_user_sso_connections,
            "get_by_provider_user_id",
            AsyncMock(return_value=conflict),
        ),
    ):
        result = await SSOService()._relink_microsoft_connection(
            db, "microsoft", user_info, uuid4()
        )
    return result, db, user_info


@pytest.mark.asyncio
async def test_legacy_graph_id_is_upgraded_to_the_oidc_sub() -> None:
    legacy = MagicMock()
    legacy.provider_user_id = "graph-object-id"
    legacy.provider_email = "user@contoso.com"

    result, db, user_info = await _relink(legacy)

    assert result is legacy
    assert legacy.provider_user_id == user_info.provider_user_id
    assert legacy.provider_email == user_info.email
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_unknown_email_does_not_relink() -> None:
    result, db, _ = await _relink(None)

    assert result is None
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_existing_sub_owner_wins_over_the_legacy_row() -> None:
    legacy = MagicMock()
    conflict = MagicMock()

    result, db, _ = await _relink(legacy, conflict)

    assert result is conflict
    assert legacy.provider_user_id != "oidc-sub"
    db.commit.assert_not_awaited()
