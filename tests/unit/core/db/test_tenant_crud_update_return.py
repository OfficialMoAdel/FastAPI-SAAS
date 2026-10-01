"""Contract tests for ``TenantAwareCRUD.update``'s return value.

The wrapper used to be annotated ``-> None`` and drop FastCRUD's result on the
floor. That made a lossy call site look type-safe and hid the row (and the
matched-rows signal) from any caller that had opted into ``return_columns`` /
``schema_to_select``. These tests run against a REAL session — a mocked leaf
cannot distinguish "the wrapper discarded the value" from "FastCRUD had nothing
to give", which is exactly the distinction at stake.

What is pinned here, all of it observable from the public method:

* the declared return type is a union, not bare ``None`` (the annotation trap
  itself — an annotation change must be a visible, deliberate act);
* a plain write still yields ``None``, so every existing caller that ignores the
  return keeps working unchanged;
* ``return_columns`` / ``schema_to_select`` / ``allow_multiple`` are forwarded
  verbatim, so the row actually comes back;
* no-match raises ``NoResultFound`` rather than returning a falsy value, and a
  cross-tenant target is indistinguishable from a missing one and stays
  unmolested — tenant isolation must not weaken now that the write reports back;
* the M7 payload ``tenant_id`` guard still holds on the returning path.
"""

from __future__ import annotations

import typing
import uuid
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.exc import MultipleResultsFound, NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.core.tenancy.context import TenantContext, clear_tenant_context, set_current_tenant
from src.app.shared.exceptions import TenantContextError


class _Base(DeclarativeBase):
    pass


class Widget(_Base):
    __tablename__ = "widgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid4)


class WidgetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class WidgetUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: uuid.UUID
    name: str


TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest_asyncio.fixture()
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        # Row 2 belongs to another tenant: it is the canary for isolation.
        session.add_all(
            [
                Widget(id=1, name="one-a", tenant_id=TENANT_A),
                Widget(id=3, name="three-a", tenant_id=TENANT_A),
                Widget(id=2, name="two-b", tenant_id=TENANT_B),
            ]
        )
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture()
def tenant_a() -> TenantAwareCRUD:
    """A CRUD bound to TENANT_A through the real request-context path."""
    clear_tenant_context()
    set_current_tenant(
        TenantContext(tenant_id=TENANT_A, tenant_slug="a", tenant_name="A", is_active=True)
    )
    try:
        yield TenantAwareCRUD(model=Widget)
    finally:
        clear_tenant_context()


class TestDeclaredContract:
    """The annotation is part of the trap — pin it separately from behaviour."""

    def test_return_type_is_not_bare_none(self) -> None:
        hints = typing.get_type_hints(TenantAwareCRUD.update)
        ret = hints["return"]
        assert ret is not type(None), (
            "TenantAwareCRUD.update must not be annotated -> None: it overrides "
            "FastCRUD.update, whose result the wrapper used to silently discard."
        )
        assert _admits_none(ret), f"return annotation must admit None (no return requested), got {ret!r}"

    def test_overriding_fastcrud_return_type_is_not_narrower(self) -> None:
        """The wrapper must not advertise LESS than its base — that was the bug.

        A narrower return type is the Liskov violation that let call sites rely
        on a value the base could produce but the override promised never to.
        Compared by NAME, not identity: FastCRUD's ``SelectSchemaType`` and ours
        are distinct TypeVar objects, so an identity comparison would always
        report a false narrowing.
        """
        from fastcrud import FastCRUD

        base = _union_names(typing.get_type_hints(FastCRUD.update).get("return"))
        ours = _union_names(typing.get_type_hints(TenantAwareCRUD.update)["return"])
        assert base <= ours, f"wrapper return covers {ours} but FastCRUD's covers {base}"


def _union_names(ret: object) -> set[str]:
    """Normalize a possibly-union annotation to a comparable set of names."""
    args = getattr(ret, "__args__", ())
    if not args:
        return {getattr(ret, "__name__", str(ret))}
    return {getattr(a, "__name__", str(a)) for a in args}


def _admits_none(ret: object) -> bool:
    """True when ``ret``'s union admits ``None``."""
    return "NoneType" in _union_names(ret)


def _tid(value: object) -> uuid.UUID:
    """Normalize a tenant id to UUID.

    The ``RETURNING`` clause reads back through the same driver as the INSERT,
    and aiosqlite has no native UUID type — so the same column can come back as
    a ``UUID`` on one read path and a ``str`` on another. Comparing normalized
    values keeps these assertions about the tenant, not the driver's type.
    """
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class TestReturnValueIsForwarded:
    async def test_plain_write_still_returns_none(self, db: AsyncSession, tenant_a: TenantAwareCRUD) -> None:
        """Backward compatibility: callers that ignore the result are unaffected.

        FastCRUD only populates a row when asked, so a bare write must keep
        returning None — this is what every current call site depends on.
        """
        assert await tenant_a.update(db=db, object={"name": "renamed"}, id=1) is None

    async def test_return_columns_returns_the_row(
        self, db: AsyncSession, tenant_a: TenantAwareCRUD
    ) -> None:
        """Pre-fix this returned None — the row was silently thrown away."""
        returned = await tenant_a.update(
            db=db, object={"name": "returned"}, id=1, return_columns=["id", "name"]
        )
        assert returned == {"id": 1, "name": "returned"}

    async def test_schema_to_select_returns_a_validated_model(
        self, db: AsyncSession, tenant_a: TenantAwareCRUD
    ) -> None:
        returned = await tenant_a.update(
            db=db,
            object={"name": "modelled"},
            id=1,
            schema_to_select=WidgetRead,
            return_as_model=True,
        )
        assert returned == WidgetRead(id=1, name="modelled")

    async def test_allow_multiple_returns_every_updated_row(
        self, db: AsyncSession, tenant_a: TenantAwareCRUD
    ) -> None:
        returned = await tenant_a.update(
            db=db,
            object={"name": "bulk"},
            return_columns=["id", "name"],
            allow_multiple=True,
        )
        assert returned == {"data": [{"id": 1, "name": "bulk"}, {"id": 3, "name": "bulk"}]}

    async def test_returned_row_reflects_the_write_not_the_pre_state(
        self, db: AsyncSession, tenant_a: TenantAwareCRUD
    ) -> None:
        """The value must be post-update; a discarded/duplicated read would not be."""
        await tenant_a.update(db=db, object={"name": "before"}, id=1, return_columns=["name"])
        returned = await tenant_a.update(
            db=db, object={"name": "after"}, id=1, return_columns=["id", "name"]
        )
        assert returned == {"id": 1, "name": "after"}


class TestNoMatchAndIsolation:
    async def test_no_matching_row_raises_rather_than_returning_none(
        self, db: AsyncSession, tenant_a: TenantAwareCRUD
    ) -> None:
        """None means "no return requested"; a miss is an error, not a None.

        If a miss ever degraded to None, a caller could not tell a silent
        no-op from a successful write that simply returned nothing.
        """
        with pytest.raises(NoResultFound):
            await tenant_a.update(
                db=db, object={"name": "ghost"}, id=4242, return_columns=["id", "name"]
            )

    async def test_ambiguous_match_still_raises(
        self, db: AsyncSession, tenant_a: TenantAwareCRUD
    ) -> None:
        """Returning a row must not quietly relax FastCRUD's single-target check."""
        with pytest.raises(MultipleResultsFound):
            await tenant_a.update(db=db, object={"name": "ambiguous"}, return_columns=["id"])

    async def test_cross_tenant_row_is_untouched_and_indistinguishable(
        self, db: AsyncSession, tenant_a: TenantAwareCRUD
    ) -> None:
        """A reported-back write must not become a cross-tenant probe.

        Targeting another tenant's primary key yields the same NoResultFound as
        a genuinely absent row, so the return value leaks nothing about whether
        that id exists under a different tenant.
        """
        with pytest.raises(NoResultFound):
            await tenant_a.update(
                db=db, object={"name": "pwned"}, id=2, return_columns=["id", "name"]
            )
        assert (await db.get(Widget, 2)).name == "two-b"  # type: ignore[union-attr]

    async def test_cross_tenant_bulk_never_reaches_the_other_tenant(
        self, db: AsyncSession, tenant_a: TenantAwareCRUD
    ) -> None:
        """allow_multiple + return_columns must still be tenant-scoped in SET/WHERE."""
        returned = await tenant_a.update(
            db=db,
            object={"name": "swept"},
            return_columns=["id", "name"],
            allow_multiple=True,
        )
        assert returned == {"data": [{"id": 1, "name": "swept"}, {"id": 3, "name": "swept"}]}
        assert (await db.get(Widget, 2)).name == "two-b"  # type: ignore[union-attr]


class TestWriteGuardsUnchanged:
    """The return-value change must not relax the write path's tenant guards."""

    async def test_dict_payload_tenant_id_is_stripped_not_reparented(
        self, db: AsyncSession, tenant_a: TenantAwareCRUD
    ) -> None:
        returned = await tenant_a.update(
            db=db,
            object={"name": "stolen", "tenant_id": TENANT_B},
            id=1,
            return_columns=["id", "name", "tenant_id"],
        )
        assert _tid(returned["tenant_id"]) == TENANT_A  # type: ignore[index]
        assert _tid((await db.get(Widget, 1)).tenant_id) == TENANT_A  # type: ignore[union-attr]

    async def test_model_payload_tenant_id_is_coerced_to_the_caller(
        self, db: AsyncSession, tenant_a: TenantAwareCRUD
    ) -> None:
        returned = await tenant_a.update(
            db=db,
            object=WidgetUpdate(tenant_id=TENANT_B, name="coerced"),
            id=1,
            return_columns=["tenant_id"],
        )
        assert _tid(returned["tenant_id"]) == TENANT_A  # type: ignore[index]

    async def test_update_without_a_tenant_context_still_fails_closed(
        self, db: AsyncSession
    ) -> None:
        clear_tenant_context()
        crud = TenantAwareCRUD(model=Widget)
        # No context and no bound scope: the guard refuses before FastCRUD runs.
        with pytest.raises(TenantContextError, match="without active tenant context"):
            await crud.update(db=db, object={"name": "nope"}, id=1)
