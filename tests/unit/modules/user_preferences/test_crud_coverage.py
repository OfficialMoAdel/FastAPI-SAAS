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


# ============================================================================
# R2-13: explicit nulls clear the nullable columns and only those
# ============================================================================


async def test_update_preferences_clears_a_nullable_column() -> None:
    """`quiet_hours_start` is advertised as `str | None`, so null must clear it."""
    crud, db = _make_crud()
    prefs = UserPreferences(user_id=7, preferences={})
    prefs.quiet_hours_start = "22:00"
    prefs.quiet_hours_end = "07:00"
    db.execute.return_value = _make_result(scalar_one_or_none=prefs)

    await crud.update_preferences(
        db=db, user_id=7, updates={"quiet_hours_start": None, "quiet_hours_end": None}
    )

    assert prefs.quiet_hours_start is None
    assert prefs.quiet_hours_end is None
    assert "quiet_hours_start" not in prefs.preferences, "a column value must never land in the bag"


async def test_update_preferences_still_skips_none_for_a_non_nullable_column() -> None:
    """Writing None to a NOT NULL column fails at flush; skipping is the contract."""
    crud, db = _make_crud()
    prefs = UserPreferences(user_id=8, preferences={})
    prefs.quiet_hours_enabled = True
    db.execute.return_value = _make_result(scalar_one_or_none=prefs)

    await crud.update_preferences(db=db, user_id=8, updates={"quiet_hours_enabled": None})

    assert prefs.quiet_hours_enabled is True


async def test_update_preferences_still_skips_none_for_a_json_bag_key() -> None:
    """A stored null would be echoed back verbatim by the response merge."""
    crud, db = _make_crud()
    prefs = UserPreferences(user_id=9, preferences={"theme": "dark"})
    db.execute.return_value = _make_result(scalar_one_or_none=prefs)

    await crud.update_preferences(db=db, user_id=9, updates={"theme": None})

    assert prefs.preferences["theme"] == "dark"


# ============================================================================
# R2-20: concurrent first access is idempotent, not a 500
# ============================================================================


async def test_get_or_create_loser_of_the_insert_race_returns_the_winner() -> None:
    """The loser hits uq user_preferences.user_id; it must re-read, not raise."""
    from sqlalchemy.exc import IntegrityError

    crud, db = _make_crud()
    winner = UserPreferences(user_id=10, preferences={"theme": "light"})
    # First SELECT: nobody has a row yet. SELECT after the rollback: the winner.
    db.execute.side_effect = [
        _make_result(scalar_one_or_none=None),
        _make_result(scalar_one_or_none=winner),
    ]
    db.commit.side_effect = IntegrityError("INSERT", {}, Exception("duplicate key"))

    result = await crud.get_or_create(db=db, user_id=10)

    assert result is winner
    db.rollback.assert_awaited_once()
    db.refresh.assert_not_awaited()


async def test_get_or_create_reraises_when_the_winner_vanished() -> None:
    """A rollback followed by a missing row is a real failure, not a result."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    crud, db = _make_crud()
    db.execute.side_effect = [
        _make_result(scalar_one_or_none=None),
        _make_result(scalar_one_or_none=None),
    ]
    db.commit.side_effect = IntegrityError("INSERT", {}, Exception("duplicate key"))

    with pytest.raises(IntegrityError):
        await crud.get_or_create(db=db, user_id=11)
