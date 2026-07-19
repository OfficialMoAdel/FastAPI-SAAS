"""Unit tests for sso/crud.py CRUDUserSSOConnection."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.modules.sso import crud as sso_crud
from src.app.modules.sso.crud import crud_user_sso_connections


def _make_db(scalar=None, scalar_one=None):
    db = MagicMock()
    result = MagicMock()
    result.scalar = MagicMock(return_value=scalar)
    result.scalar_one_or_none = MagicMock(return_value=scalar_one)
    db.execute = AsyncMock(return_value=result)
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    return db


TENANT = "11111111-1111-1111-1111-111111111111"


async def test_get_by_criteria_all_none():
    db = _make_db(scalar_one="conn")
    result = await crud_user_sso_connections.get_by_criteria(db=db, tenant_id=TENANT)
    assert result == "conn"
    db.execute.assert_awaited_once()


async def test_get_by_criteria_with_filters():
    db = _make_db(scalar_one="conn")
    result = await crud_user_sso_connections.get_by_criteria(
        db=db, tenant_id=TENANT, id=1, provider="google", provider_user_id="puid", user_id=5
    )
    assert result == "conn"


async def test_get_by_provider_user_id():
    db = _make_db(scalar_one="conn")
    result = await crud_user_sso_connections.get_by_provider_user_id(
        db=db, provider="google", provider_user_id="puid", tenant_id=TENANT
    )
    assert result == "conn"


async def test_exists_for_user_true():
    db = _make_db(scalar=3)
    result = await crud_user_sso_connections.exists_for_user(db=db, user_id=5, provider="google", tenant_id=TENANT)
    assert result is True


async def test_exists_for_user_false():
    db = _make_db(scalar=0)
    result = await crud_user_sso_connections.exists_for_user(db=db, user_id=5, provider="google", tenant_id=TENANT)
    assert result is False


async def test_exists_for_user_none_count():
    db = _make_db(scalar=None)
    result = await crud_user_sso_connections.exists_for_user(db=db, user_id=5, provider="google", tenant_id=TENANT)
    assert result is False


async def test_count_for_user_with_user_id():
    db = _make_db(scalar=7)
    result = await crud_user_sso_connections.count_for_user(db=db, tenant_id=TENANT, user_id=5)
    assert result == 7


async def test_count_for_user_none_uses_super():
    db = _make_db(scalar=2)
    count_mock = AsyncMock(return_value=9)
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sso_crud.TenantAwareCRUD, "count", count_mock)
        result = await crud_user_sso_connections.count_for_user(db=db, tenant_id=TENANT, id=3)
    assert result == 9


async def test_count_for_user_no_user_id_no_id():
    db = _make_db(scalar=4)
    result = await crud_user_sso_connections.count_for_user(db=db, tenant_id=TENANT)
    assert result == 4


async def test_delete_by_criteria_by_id():
    db = _make_db()
    delete_mock = AsyncMock()
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sso_crud.TenantAwareCRUD, "delete", delete_mock)
        await crud_user_sso_connections.delete_by_criteria(db=db, tenant_id=TENANT, id=5)
    delete_mock.assert_awaited_once_with(db=db, id=5)


async def test_delete_by_criteria_by_user_provider_found():
    db = _make_db(scalar_one="conn")
    await crud_user_sso_connections.delete_by_criteria(db=db, tenant_id=TENANT, user_id=5, provider="google")
    db.delete.assert_awaited_once_with("conn")
    db.commit.assert_awaited_once()


async def test_delete_by_criteria_by_user_provider_not_found():
    db = _make_db(scalar_one=None)
    await crud_user_sso_connections.delete_by_criteria(db=db, tenant_id=TENANT, user_id=5, provider="google")
    db.delete.assert_not_awaited()
    db.commit.assert_not_awaited()
