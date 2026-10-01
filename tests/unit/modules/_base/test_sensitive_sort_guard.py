"""Sensitive-column sort guard rejects the STRING form of ``sort_columns``.

``BaseService.get_multi`` guards ``sort_columns`` against
``{hashed_password, password, secret, api_key}`` before handing it to the CRUD
layer. The check used ``any(c in SENSITIVE for c in sort_columns)`` — with a
bare string that iterates CHARACTERS, none of which is a member, so
``sort_columns="hashed_password"`` passed the guard untouched.

The string form is a legal input: ``fetch_page_with_total`` in the same module
declares ``sort_columns: str | list[str] | None``.

This is defence in depth, not a live hole: both in-repo callers pass lists, and
the eager-load path fails closed at ``hasattr(self.model, col)`` in the same
method. The guard exists for the plain path, which forwards to FastCRUD.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest

from src.app.core.tenancy.context import (
    TenantContext,
    clear_tenant_context,
    set_current_tenant,
)
from src.app.modules._base.service import BaseService
from src.app.modules.auth.models import PasskeyCredential
from src.app.shared.exceptions import ValidationError


def _make_service() -> tuple[BaseService[PasskeyCredential], AsyncMock]:
    db = AsyncMock()
    service = BaseService(PasskeyCredential, db)
    service.crud = AsyncMock()
    return service, db


async def test_get_multi_rejects_sensitive_sort_given_as_string() -> None:
    """REGRESSION: the exact string form that used to slip through the guard."""
    service, db = _make_service()
    db.execute = AsyncMock()
    service.crud.get_multi.return_value = {"data": []}

    with pytest.raises(ValidationError, match="sensitive column"):
        await service.get_multi(sort_columns="hashed_password")

    service.crud.get_multi.assert_not_awaited()
    db.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "column",
    ["hashed_password", "password", "secret", "api_key"],
)
async def test_get_multi_rejects_every_sensitive_column_as_string(column: str) -> None:
    service, db = _make_service()
    db.execute = AsyncMock()
    service.crud.get_multi.return_value = {"data": []}

    with pytest.raises(ValidationError, match="sensitive column"):
        await service.get_multi(sort_columns=column)

    service.crud.get_multi.assert_not_awaited()


async def test_get_multi_still_rejects_sensitive_sort_given_as_list() -> None:
    """PRE-EXISTING behaviour that must survive the string fix."""
    service, db = _make_service()
    db.execute = AsyncMock()
    service.crud.get_multi.return_value = {"data": []}

    with pytest.raises(ValidationError, match="sensitive column"):
        await service.get_multi(sort_columns=["hashed_password"])

    service.crud.get_multi.assert_not_awaited()


async def test_get_multi_still_rejects_sensitive_column_in_mixed_list() -> None:
    """A sensitive name smuggled in beside a legitimate one is still caught."""
    service, db = _make_service()
    db.execute = AsyncMock()
    service.crud.get_multi.return_value = {"data": []}

    with pytest.raises(ValidationError, match="sensitive column"):
        await service.get_multi(sort_columns=["created_at", "api_key"], sort_orders=["asc", "desc"])

    service.crud.get_multi.assert_not_awaited()


async def test_get_multi_allows_legitimate_string_sort_on_plain_path() -> None:
    """The fix must not reject harmless input: a string naming a real,
    non-sensitive column is forwarded to the CRUD layer untouched."""
    service, db = _make_service()
    service.crud.get_multi.return_value = {"data": []}

    result = await service.get_multi(sort_columns="created_at", sort_orders="desc")

    assert result == []
    service.crud.get_multi.assert_awaited_once()
    assert service.crud.get_multi.await_args.kwargs["sort_columns"] == "created_at"


async def test_get_multi_allows_legitimate_list_sort_on_plain_path() -> None:
    service, db = _make_service()
    service.crud.get_multi.return_value = {"data": []}

    await service.get_multi(sort_columns=["id"], sort_orders=["asc"])

    service.crud.get_multi.assert_awaited_once()


async def test_get_multi_allows_none_and_empty_sort_columns() -> None:
    service, db = _make_service()
    service.crud.get_multi.return_value = {"data": []}

    await service.get_multi(sort_columns=None)
    await service.get_multi(sort_columns=[])

    assert service.crud.get_multi.await_count == 2


async def test_eager_path_still_applies_a_legitimate_list_sort() -> None:
    """End-to-end on the eager branch: a legitimate sort passes the guard and
    ordering is applied through the selectinload path under a tenant context."""
    service, db = _make_service()
    db.execute.return_value = MagicMock()
    db.execute.return_value.scalars.return_value.unique.return_value.all.return_value = [
        create_autospec(PasskeyCredential, instance=True)
    ]

    clear_tenant_context()
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
            page=1,
            per_page=20,
            eager_loads=["user"],
            sort_columns=["created_at"],
            sort_orders=["desc"],
        )
    finally:
        clear_tenant_context()

    assert isinstance(result, list)
    db.execute.assert_awaited_once()


async def test_eager_path_rejects_sensitive_string_sort_at_the_guard() -> None:
    """On the eager branch the string form now dies at the sensitive-column
    guard, which runs before the per-column ``hasattr`` loop. Previously the
    character iteration reached that loop and produced 'Unknown sort column';
    either way the path failed closed — a sensitive attribute was never
    sortable. The new failure is the accurate one."""
    service, db = _make_service()
    db.execute = AsyncMock()

    with pytest.raises(ValidationError, match="sensitive column"):
        await service.get_multi(eager_loads=["user"], sort_columns="hashed_password")

    db.execute.assert_not_awaited()
