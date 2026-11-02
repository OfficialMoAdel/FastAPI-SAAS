"""Passkey pagination: the total is independent of the page's contents."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.pagination import PaginationParams
from src.app.modules.auth.crud import crud_passkey_credentials
from src.app.modules.auth.routers.passkey import list_credentials


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


@pytest.mark.asyncio
async def test_router_pushes_pagination_into_the_query_layer() -> None:
    db = AsyncMock()
    ctx = SimpleNamespace(db=db, user_id=1)
    page = {"data": [{"id": 7}], "total_count": 25}

    with patch(
        "src.app.modules.auth.routers.passkey.passkey_service.list_credentials_page",
        new=AsyncMock(return_value=page),
    ) as mock_page:
        result = await list_credentials(ctx=ctx, pagination=PaginationParams(page=3, per_page=10))

    mock_page.assert_awaited_once_with(db=db, user_id=1, offset=20, limit=10)
    assert result["items"] == page["data"]
    assert result["total"] == 25
    assert result["page"] == 3
    assert result["per_page"] == 10
