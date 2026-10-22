"""Passkey pagination: the total is independent of the page's contents."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.modules.auth.crud import crud_passkey_credentials


@pytest.mark.asyncio
async def test_out_of_range_page_reports_the_real_total() -> None:
    """A page past the end has no row carrying the window count."""
    db = AsyncMock()
    page_result = MagicMock()
    page_result.all.return_value = []
    count_result = MagicMock()
    count_result.scalar.return_value = 25
    db.execute = AsyncMock(side_effect=[page_result, count_result])

    page = await crud_passkey_credentials.get_page_by_user(db, 1, offset=40, limit=10)

    assert page["data"] == []
    assert page["total_count"] == 25
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_a_non_empty_page_uses_the_window_count() -> None:
    db = AsyncMock()
    first = MagicMock()
    first.id = 7
    page_result = MagicMock()
    page_result.all.return_value = [(first, 3)]
    db.execute = AsyncMock(return_value=page_result)

    page = await crud_passkey_credentials.get_page_by_user(db, 1, offset=0, limit=10)

    assert page["data"] == [first]
    assert page["total_count"] == 3
    assert db.execute.await_count == 1
