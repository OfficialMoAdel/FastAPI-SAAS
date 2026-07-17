"""Coverage tests for passkey CRUD operations using mocked DB session."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, create_autospec

from src.app.modules.passkey.crud import CRUDPasskeyCredential
from src.app.modules.passkey.models import PasskeyCredential


def _make_result(scalar_one_or_none: object = None, scalars: object = None, scalar: object = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_one_or_none
    scalars_obj = MagicMock()
    scalars_obj.all.return_value = scalars if scalars is not None else []
    result.scalars.return_value = scalars_obj
    result.scalar.return_value = scalar
    return result


def _make_crud() -> tuple[CRUDPasskeyCredential, AsyncMock]:
    db = AsyncMock()
    crud = CRUDPasskeyCredential(PasskeyCredential)
    return crud, db


async def test_get_credential_by_id() -> None:
    crud, db = _make_crud()
    cred = create_autospec(PasskeyCredential, instance=True)
    db.execute.return_value = _make_result(scalar_one_or_none=cred)
    result = await crud.get_credential(db=db, id=5)
    assert result is cred
    # Verify the query filters by id
    executed = db.execute.call_args[0][0]
    assert str(executed).find("id") != -1


async def test_get_credential_by_credential_id_and_user() -> None:
    crud, db = _make_crud()
    cred = create_autospec(PasskeyCredential, instance=True)
    db.execute.return_value = _make_result(scalar_one_or_none=cred)
    result = await crud.get_credential(db=db, credential_id=b"abc", user_id=3)
    assert result is cred
    executed = db.execute.call_args[0][0]
    assert str(executed).find("credential_id") != -1
    assert str(executed).find("user_id") != -1


async def test_get_credential_no_filters() -> None:
    crud, db = _make_crud()
    cred = create_autospec(PasskeyCredential, instance=True)
    db.execute.return_value = _make_result(scalar_one_or_none=cred)
    result = await crud.get_credential(db=db)
    assert result is cred


async def test_get_by_user() -> None:
    crud, db = _make_crud()
    creds = [create_autospec(PasskeyCredential, instance=True), create_autospec(PasskeyCredential, instance=True)]
    db.execute.return_value = _make_result(scalars=creds)
    result = await crud.get_by_user(db=db, user_id=7)
    assert result == creds
    executed = db.execute.call_args[0][0]
    assert str(executed).find("user_id") != -1
    assert str(executed).find("ORDER") != -1 or str(executed).find("order") != -1


async def test_count_by_user() -> None:
    crud, db = _make_crud()
    db.execute.return_value = _make_result(scalar=4)
    result = await crud.count_by_user(db=db, user_id=7)
    assert result == 4
    executed = db.execute.call_args[0][0]
    assert str(executed).find("count") != -1


async def test_count_by_user_returns_zero_on_none() -> None:
    crud, db = _make_crud()
    db.execute.return_value = _make_result(scalar=None)
    result = await crud.count_by_user(db=db, user_id=7)
    assert result == 0


async def test_can_create_under_limit() -> None:
    crud, db = _make_crud()
    db.execute.return_value = _make_result(scalar=3)
    result = await crud.can_create(db=db, user_id=7)
    assert result is True


async def test_can_create_at_limit() -> None:
    crud, db = _make_crud()
    db.execute.return_value = _make_result(scalar=crud.MAX_PASSKEYS_PER_USER)
    result = await crud.can_create(db=db, user_id=7)
    assert result is False


async def test_delete_credential_by_object() -> None:
    crud, db = _make_crud()
    cred = create_autospec(PasskeyCredential, instance=True)
    await crud.delete_credential(db=db, db_object=cred)
    db.delete.assert_called_once_with(cred)
    db.commit.assert_awaited_once()
