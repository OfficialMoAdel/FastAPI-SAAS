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
    """Eager path is scoped + guarded via execute_scoped — needs a tenant
    context now (fail-closed without one); inject_tenant_filter is no longer
    called on this path."""
    import uuid

    import pytest

    from src.app.core.tenancy.context import (
        TenantContext,
        clear_tenant_context,
        set_current_tenant,
    )
    from src.app.shared.exceptions import TenantContextError

    service, db = _make_service()
    db.execute.return_value = MagicMock()
    db.execute.return_value.scalars.return_value.unique.return_value.all.return_value = [
        create_autospec(PasskeyCredential, instance=True)
    ]
    # Fail-closed: no tenant context, no scope -> refuses before touching DB.
    clear_tenant_context()
    with pytest.raises(TenantContextError):
        await service.get_multi(page=1, per_page=20, eager_loads=["user"])
    db.execute.assert_not_awaited()

    set_current_tenant(
        TenantContext(
            tenant_id=uuid.uuid4(),
            tenant_slug="acme",
            tenant_name="Acme",
            is_active=True,
        )
    )
    try:
        result = await service.get_multi(
            page=1, per_page=20, eager_loads=["user"], sort_columns=["id"], sort_orders=["asc"]
        )
    finally:
        clear_tenant_context()
    assert isinstance(result, list)


async def test_get_multi_rejects_malformed_sort_with_domain_error() -> None:
    """C5: malformed sort/filter input is a DOMAIN validation failure (-> 400
    via core/errors.py EXCEPTION_HANDLERS), never a raw ValueError (-> 500)."""
    import pytest

    from src.app.shared.exceptions import ValidationError

    service, db = _make_service()
    db.execute = AsyncMock()

    with pytest.raises(ValidationError):
        await service.get_multi(eager_loads=["user"], sort_columns=["hashed_password"])

    with pytest.raises(ValidationError):
        await service.get_multi(eager_loads=["user"], sort_columns=["no_such_column"])

    with pytest.raises(ValidationError):
        await service.get_multi(eager_loads=["user"], no_such_filter="x")

    db.execute.assert_not_awaited()  # rejected before touching the database


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
    """Without a select_schema the CRUD's return is passed through: the
    TenantAwareCRUD wrapper returns None there, which is the documented
    contract for an untyped update, NOT a missing row (the pre-flight count
    still raises NoResultFound before any write)."""
    service, _ = _make_service()
    obj = create_autospec(PasskeyCredential, instance=True)
    service.crud.update.return_value = obj
    result = await service.update(id=1, user_id=2)
    assert result is obj
    service.crud.update.assert_awaited_once()
    # No follow-up read: the typed path re-read only when a schema is set.
    service.crud.get.assert_not_awaited()


async def test_update_typed_returns_the_row_from_the_update() -> None:
    """With a select_schema the UPDATE itself returns the row (RETURNING),
    so no re-read is needed and the row is validated against the schema."""
    from src.app.modules.auth.schemas import PasskeyCredentialResponse

    db = AsyncMock()
    service = BaseService(PasskeyCredential, db, select_schema=PasskeyCredentialResponse)
    service.crud = AsyncMock()
    row = create_autospec(PasskeyCredential, instance=True)
    service.crud.update.return_value = row

    result = await service.update(id=1, user_id=2)

    assert result is row
    service.crud.update.assert_awaited_once()
    kwargs = service.crud.update.await_args.kwargs
    assert kwargs["return_as_model"] is True
    assert kwargs["schema_to_select"] is PasskeyCredentialResponse
    service.crud.get.assert_not_awaited()


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
