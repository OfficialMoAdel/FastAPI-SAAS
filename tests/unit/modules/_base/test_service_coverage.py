"""Coverage tests for BaseService pattern using mocked CRUD layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, create_autospec

from src.app.modules._base.service import BaseService, get_base_service
from src.app.modules.auth.models import PasskeyCredential


def _make_service() -> tuple[BaseService[PasskeyCredential], AsyncMock]:
    db = AsyncMock()
    service = BaseService(PasskeyCredential, db)
    service.crud = AsyncMock()
    return service, db


async def test_get() -> None:
    service, _ = _make_service()
    obj = create_autospec(PasskeyCredential, instance=True)
    service.crud.get.return_value = obj
    result = await service.get(id=1)
    assert result is obj
    service.crud.get.assert_awaited_once()


async def test_get_multi_dict_response() -> None:
    service, _ = _make_service()
    service.crud.get_multi.return_value = {"data": [create_autospec(PasskeyCredential, instance=True)]}
    result = await service.get_multi(page=1, per_page=10)
    assert isinstance(result, list)
    service.crud.get_multi.assert_awaited_once()


async def test_get_multi_list_response() -> None:
    service, _ = _make_service()
    service.crud.get_multi.return_value = [create_autospec(PasskeyCredential, instance=True)]
    result = await service.get_multi(page=2, per_page=5)
    assert isinstance(result, list)


async def test_get_multi_eager_loading_disabled() -> None:
    service, _ = _make_service()
    service.crud.get_multi.return_value = {"data": []}
    result = await service.get_multi(eager_loads=None)
    assert result == []


async def test_get_multi_eager_loading_enabled() -> None:
    service, db = _make_service()
    service.crud.inject_tenant_filter = MagicMock(side_effect=lambda k: k)
    service.crud._filter_processor = MagicMock()
    service.crud._filter_processor.parse_filters.return_value = []
    service.crud._query_builder = MagicMock()
    service.crud._query_builder.apply_sorting.side_effect = lambda q, c, o: q
    db.execute.return_value = MagicMock()
    db.execute.return_value.scalars.return_value.unique.return_value.all.return_value = [
        create_autospec(PasskeyCredential, instance=True)
    ]
    result = await service.get_multi(
        page=1, per_page=20, eager_loads=["user"], sort_columns=["id"], sort_orders=["asc"]
    )
    assert isinstance(result, list)


async def test_get_multi_joined_dict() -> None:
    service, _ = _make_service()
    service.crud.get_multi_joined.return_value = {"data": [{"id": 1}]}
    result = await service.get_multi_joined(page=1, per_page=10)
    assert result == [{"id": 1}]


async def test_get_multi_joined_list() -> None:
    service, _ = _make_service()
    service.crud.get_multi_joined.return_value = [{"id": 2}]
    result = await service.get_multi_joined(page=1, per_page=10)
    assert result == [{"id": 2}]


async def test_get_multi_joined_page_clamp_h8() -> None:
    """H-8 regression: page=0/negative previously produced a negative
    OFFSET (500 on malformed pagination input). Must clamp like get_multi."""
    service, _ = _make_service()
    service.crud.get_multi_joined.return_value = {"data": []}
    await service.get_multi_joined(page=0, per_page=10)
    call_kwargs = service.crud.get_multi_joined.await_args.kwargs
    assert call_kwargs["offset"] == 0
    # negative page also clamps to the first page
    await service.get_multi_joined(page=-3, per_page=10)
    call_kwargs = service.crud.get_multi_joined.await_args.kwargs
    assert call_kwargs["offset"] == 0
    # per_page=0 clamps to 1 (no negative OFFSET, no zero-limit weirdness)
    await service.get_multi_joined(page=2, per_page=0)
    call_kwargs = service.crud.get_multi_joined.await_args.kwargs
    assert call_kwargs["offset"] == 1
    assert call_kwargs["limit"] == 1


async def test_create() -> None:
    service, _ = _make_service()
    obj = create_autospec(PasskeyCredential, instance=True)
    service.crud.create.return_value = obj
    result = await service.create(user_id=1)
    assert result is obj
    service.crud.create.assert_awaited_once()


async def test_update() -> None:
    service, _ = _make_service()
    obj = create_autospec(PasskeyCredential, instance=True)
    service.crud.update.return_value = obj
    result = await service.update(id=1, user_id=2)
    assert result is obj
    service.crud.update.assert_awaited_once()


async def test_delete() -> None:
    service, _ = _make_service()
    service.crud.delete.return_value = None
    await service.delete(id=1)
    service.crud.delete.assert_awaited_once_with(db=service.db, id=1)


def test_get_base_service_factory() -> None:
    factory = get_base_service(PasskeyCredential)
    db = AsyncMock()
    service = factory(db)
    assert isinstance(service, BaseService)
    assert service.model is PasskeyCredential
