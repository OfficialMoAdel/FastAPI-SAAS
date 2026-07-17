"""Coverage tests for user_preferences CRUD operations using mocked DB session."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.app.modules.user_preferences.crud import CRUDUserPreferences
from src.app.modules.user_preferences.models import UserPreferences


def _make_result(scalar_one_or_none: object = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_one_or_none
    return result


def _make_crud() -> tuple[CRUDUserPreferences, AsyncMock]:
    db = AsyncMock()
    crud = CRUDUserPreferences(UserPreferences)
    return crud, db


async def test_get_by_user_found() -> None:
    crud, db = _make_crud()
    prefs = UserPreferences(user_id=1)
    db.execute.return_value = _make_result(scalar_one_or_none=prefs)
    result = await crud.get_by_user(db=db, user_id=1)
    assert result is prefs


async def test_get_by_user_none() -> None:
    crud, db = _make_crud()
    db.execute.return_value = _make_result(scalar_one_or_none=None)
    result = await crud.get_by_user(db=db, user_id=1)
    assert result is None


async def test_get_or_create_existing() -> None:
    crud, db = _make_crud()
    prefs = UserPreferences(user_id=2)
    db.execute.return_value = _make_result(scalar_one_or_none=prefs)
    result = await crud.get_or_create(db=db, user_id=2)
    assert result is prefs
    db.add.assert_not_awaited()


async def test_get_or_create_new() -> None:
    crud, db = _make_crud()
    db.execute.return_value = _make_result(scalar_one_or_none=None)
    result = await crud.get_or_create(db=db, user_id=3)
    assert result is not None
    assert result.user_id == 3
    assert result.preferences is not None
    db.add.assert_called_once()
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once()


async def test_update_preferences_merges_into_json() -> None:
    crud, db = _make_crud()
    prefs = UserPreferences(user_id=4, preferences={"theme": "system", "language": "en"})
    db.execute.return_value = _make_result(scalar_one_or_none=prefs)
    result = await crud.update_preferences(db=db, user_id=4, updates={"theme": "dark"})
    assert result is prefs
    assert prefs.preferences["theme"] == "dark"
    assert prefs.preferences["language"] == "en"
    db.commit.assert_awaited_once()


async def test_update_preferences_skips_none() -> None:
    crud, db = _make_crud()
    prefs = UserPreferences(user_id=5, preferences={"theme": "system"})
    db.execute.return_value = _make_result(scalar_one_or_none=prefs)
    result = await crud.update_preferences(db=db, user_id=5, updates={"theme": None})
    assert result.preferences["theme"] == "system"


async def test_update_preferences_sets_attribute() -> None:
    crud, db = _make_crud()
    prefs = UserPreferences(user_id=6, preferences={})
    db.execute.return_value = _make_result(scalar_one_or_none=prefs)
    await crud.update_preferences(db=db, user_id=6, updates={"user_id": 99})
    assert prefs.user_id == 99
