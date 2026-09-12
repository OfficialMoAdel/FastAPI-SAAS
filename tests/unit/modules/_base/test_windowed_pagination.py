"""P0-5 equivalence tests for fetch_page_with_total (windowed COUNT).

Verifies the single-round-trip helper returns byte-identical shapes to
FastCRUD get_multi(return_total_count=True) across: paged reads, empty
pages (incl. out-of-range fallback COUNT), empty tables, return_as_model,
validation parity, tenant scoping, and single-query execution.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastcrud import FastCRUD
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Integer, String, event
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.core.tenancy.context import TenantContext, clear_tenant_context, set_current_tenant
from src.app.modules._base.service import fetch_page_with_total


class _Base(DeclarativeBase):
    pass


class Widget(_Base):
    __tablename__ = "widgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid4)


class WidgetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


@pytest_asyncio.fixture()
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture()
def query_counter(db: AsyncSession) -> list[int]:
    queries: list[int] = []
    engine = db.bind
    sync_engine = engine.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", lambda *a: queries.append(1))
    return queries


async def _seed(db: AsyncSession, n: int, tenant: UUID | None = None) -> None:
    db.add_all([Widget(name=f"w-{i:03d}", tenant_id=tenant or uuid4()) for i in range(n)])
    await db.commit()


async def test_parity_with_get_multi(db: AsyncSession) -> None:
    await _seed(db, 25)
    crud = FastCRUD(Widget)
    expected = await crud.get_multi(db=db, offset=10, limit=10, schema_to_select=WidgetRead)
    actual = await fetch_page_with_total(crud, db, offset=10, limit=10, schema_to_select=WidgetRead)
    assert actual == {"data": expected["data"], "total_count": expected["total_count"]}
    assert actual["total_count"] == 25
    assert len(actual["data"]) == 10


async def test_single_round_trip(db: AsyncSession, query_counter: list[int]) -> None:
    await _seed(db, 25)
    crud = FastCRUD(Widget)
    query_counter.clear()
    await crud.get_multi(db=db, offset=0, limit=10, schema_to_select=WidgetRead)
    assert len(query_counter) == 2
    query_counter.clear()
    await fetch_page_with_total(crud, db, offset=0, limit=10, schema_to_select=WidgetRead)
    assert len(query_counter) == 1


async def test_empty_page_offset_zero(db: AsyncSession, query_counter: list[int]) -> None:
    crud = FastCRUD(Widget)
    query_counter.clear()
    actual = await fetch_page_with_total(crud, db, offset=0, limit=10, schema_to_select=WidgetRead)
    assert actual == {"data": [], "total_count": 0}
    assert len(query_counter) == 1  # no fallback COUNT needed


async def test_out_of_range_page_fallback_count(db: AsyncSession, query_counter: list[int]) -> None:
    await _seed(db, 5)
    crud = FastCRUD(Widget)
    expected = await crud.get_multi(db=db, offset=100, limit=10, schema_to_select=WidgetRead)
    query_counter.clear()
    actual = await fetch_page_with_total(crud, db, offset=100, limit=10, schema_to_select=WidgetRead)
    assert actual == {"data": [], "total_count": expected["total_count"]}
    assert actual["total_count"] == 5
    assert len(query_counter) == 2  # page query + fallback COUNT only


async def test_return_as_model_parity(db: AsyncSession) -> None:
    await _seed(db, 3)
    crud = FastCRUD(Widget)
    expected = await crud.get_multi(
        db=db, offset=0, limit=10, schema_to_select=WidgetRead, return_as_model=True
    )
    actual = await fetch_page_with_total(
        crud, db, offset=0, limit=10, schema_to_select=WidgetRead, return_as_model=True
    )
    assert actual["total_count"] == expected["total_count"] == 3
    assert [m.model_dump() for m in actual["data"]] == [m.model_dump() for m in expected["data"]]


async def test_validation_parity(db: AsyncSession) -> None:
    crud = FastCRUD(Widget)
    with pytest.raises(ValueError):
        await crud.get_multi(db=db, offset=-1, limit=10)
    with pytest.raises(ValueError):
        await fetch_page_with_total(crud, db, offset=-1, limit=10)


async def test_tenant_scoping_parity(db: AsyncSession) -> None:
    tid_a, tid_b = uuid4(), uuid4()
    await _seed(db, 3, tenant=tid_a)
    await _seed(db, 7, tenant=tid_b)
    crud = TenantAwareCRUD(Widget)
    ctx = TenantContext(tenant_id=tid_a, tenant_slug="a", tenant_name="a", is_active=True)
    set_current_tenant(ctx)
    try:
        expected = await crud.get_multi(db=db, offset=0, limit=50, schema_to_select=WidgetRead)
        actual = await fetch_page_with_total(crud, db, offset=0, limit=50, schema_to_select=WidgetRead)
    finally:
        clear_tenant_context()
    assert actual == {"data": expected["data"], "total_count": 3}


async def test_no_schema_select_parity(db: AsyncSession) -> None:
    """Unbounded sites (no schema_to_select) must also match exactly."""
    await _seed(db, 4)
    crud = FastCRUD(Widget)
    expected = await crud.get_multi(db=db)
    actual = await fetch_page_with_total(crud, db)
    assert actual["total_count"] == expected["total_count"] == 4
    assert actual["data"] == expected["data"]
