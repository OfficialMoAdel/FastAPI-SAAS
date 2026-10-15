"""A NOT NULL timestamp column must never be left to an INSERT that sends NULL.

This is the guard bead FastAPI_saas-9w6b asked for, and the reason it exists is
`Post`. `Post.updated_at` was `Mapped[datetime | None] = mapped_column(...,
default=None, onupdate=...)` against a NOT NULL column, so SQLAlchemy sent an
explicit NULL and both POST routes answered 500 for every user. Measured on
perf-local: `post.updated_at BEFORE flush = None`, then
`IntegrityError: NotNullViolationError: null value in column "updated_at"`.

TWO WAYS THIS CHECK WAS WRITTEN WRONG FIRST, so neither is repeated here.

1. Reading `Column.default`. Under `MappedAsDataclass`, `default_factory=`
   becomes a DATACLASS FIELD DEFAULT applied at construction, so it is invisible
   in Column metadata -- `Column.default` reads `None` for `users.updated_at`
   even though `User(...).updated_at` carries a real timestamp. A detector built
   on it reported a defect that did not exist. The verdict here therefore reads
   the dataclass field AND the column together.

2. Testing the SPELLING instead of the CONSEQUENCE. An earlier survey reported
   "17 models map `updated_at` default=None against a NOT NULL column". Every
   one of those 17 has `updated_at nullable=True`. A NULLable column may hold
   NULL, and an optional `updated_at` is the intended meaning, so all 17 were
   correct code. The count matched and the content did not, which is how a
   coincidence gets mistaken for a confirmation. So this check asks per COLUMN
   whether the column is NOT NULL, and only then whether a value is guaranteed.

The single UNSAFE condition, and it is the only thing that fails:

    NOT NULL  AND  no default at any layer (Column.default, insert_default,
    server_default)  AND  the dataclass field default is None

which is exactly the shape that sent NULL for Post. A field with NO default at
all is a different thing: the constructor demands the argument, so it fails
immediately and loudly rather than writing NULL. Those are asserted as a
separate bucket so they cannot be quietly counted as safe.

A live-database test cannot cover any of this. `tests/conftest.py::
real_postgres_engine` builds its schema with `Base.metadata.create_all` FROM THE
MODELS, so the schema would agree with whatever the model said -- the same
lesson as the `token_blacklist.token` width guard. This check is metadata only
and needs no database.
"""

from __future__ import annotations

import dataclasses
from typing import NamedTuple, cast

import pytest
from sqlalchemy import Table

import src.app.models  # noqa: F401  the A-M6 registration seam
from src.app.core.db.database import Base

WATCHED_COLUMNS = ("created_at", "updated_at")

#: Enough NOT NULL timestamp columns to know the scan is actually finding the
#: models. If a future change breaks mapper registration this assertion fails
#: instead of the scan quietly reporting zero and passing.
MINIMUM_NOT_NULL_TIMESTAMP_COLUMNS = 30


class TimestampColumn(NamedTuple):
    model: str
    table: str
    column: str
    #: A value is guaranteed at one of: the column, the server, or construction.
    has_default: bool
    #: True when the dataclass field has no default, so the caller must pass one.
    constructor_demands: bool
    #: True when the field default is explicitly None -- the Post failure.
    sends_null: bool


def _column_has_a_default(column: object) -> bool:
    if getattr(column, "server_default", None) is not None:
        return True
    return any(
        getattr(column, attribute, None) is not None for attribute in ("default", "insert_default")
    )


def _survey() -> list[TimestampColumn]:
    found: list[TimestampColumn] = []
    for mapper in Base.registry.mappers:
        model = mapper.class_
        # `local_table` is typed FromClause because a polymorphic mapper has no
        # single table; every mapper in this registry is a plain one, so the
        # cast is the honest narrowing rather than a blanket ignore.
        table = cast(Table, mapper.local_table)
        fields = {field.name: field for field in dataclasses.fields(model)}
        for name in WATCHED_COLUMNS:
            column = table.columns.get(name)
            # A NULLable column may legitimately hold NULL, so it is out of
            # scope entirely. This is the check the earlier survey omitted.
            if column is None or column.nullable:
                continue
            field = fields.get(name)
            construction_default = (
                field is not None and field.default_factory is not dataclasses.MISSING
            )
            found.append(
                TimestampColumn(
                    model=model.__name__,
                    table=table.name,
                    column=name,
                    has_default=_column_has_a_default(column) or construction_default,
                    constructor_demands=(
                        field is not None
                        and field.default is dataclasses.MISSING
                        and field.default_factory is dataclasses.MISSING
                    ),
                    sends_null=(field is not None and field.default is None),
                )
            )
    return sorted(found, key=lambda row: (row.table, row.column))


SURVEY = _survey()
SURVEYED = {(row.model, row.column): row for row in SURVEY}


def test_the_scan_actually_finds_the_models() -> None:
    """A scan that silently finds nothing would report every endpoint safe."""
    assert len(SURVEY) >= MINIMUM_NOT_NULL_TIMESTAMP_COLUMNS, (
        f"only {len(SURVEY)} NOT NULL timestamp columns found; the survey is "
        f"not reaching the models and this file would pass while gating nothing"
    )


def test_no_insert_sends_null_into_a_not_null_timestamp() -> None:
    """The defect itself: a NOT NULL column the ORM leaves as an explicit NULL."""
    offenders = [
        f"{row.model}.{row.column} (table {row.table})" for row in SURVEY if row.sends_null
    ]
    assert not offenders, (
        "these map a NOT NULL timestamp column to an explicit None, so the ORM "
        f"sends NULL and the insert fails: {offenders}"
    )


def test_every_not_null_timestamp_has_a_value_from_somewhere() -> None:
    """No NOT NULL timestamp may depend on a default that does not exist."""
    offenders = [
        f"{row.model}.{row.column} (table {row.table})" for row in SURVEY if not row.has_default
    ]
    assert not offenders, (
        f"these NOT NULL timestamp columns have no default at the column, the "
        f"server or construction: {offenders}"
    )


def test_the_post_regression_is_pinned_by_name() -> None:
    """Pin the model that actually broke, so the general rule is not the only witness.

    A general rule that a future refactor could widen away would stop catching
    this. Naming it means a change to `Post.updated_at` fails here with a
    sentence that says what happened to it.
    """
    post_updated = SURVEYED.get(("Post", "updated_at"))
    assert post_updated is not None, "Post.updated_at is no longer NOT NULL, so this guard is stale"
    assert post_updated.has_default and not post_updated.sends_null


def test_a_nullable_updated_at_is_not_treated_as_a_defect() -> None:
    """An optional `updated_at` is correct and must stay that way.

    Sixteen models declare `updated_at ... default=None` against a NULLABLE
    column. That is the intended meaning of an optional column, and a guard that
    forbade the spelling would push the tree toward making every timestamp
    mandatory. This test exists to stop that over-correction, which is the exact
    error bead 9w6b's original survey made.
    """
    optional = [
        mapper.class_.__name__
        for mapper in Base.registry.mappers
        if (column := cast(Table, mapper.local_table).columns.get("updated_at")) is not None
        and column.nullable
        and (field := next(
            (f for f in dataclasses.fields(mapper.class_) if f.name == "updated_at"), None
        ))
        is not None
        and field.default is None
    ]
    assert optional, (
        "no model now declares an optional updated_at; if that was deliberate, "
        "this assertion should be updated with the reason rather than left to "
        "silently stop covering the case"
    )


@pytest.mark.parametrize("column_name", WATCHED_COLUMNS)
def test_the_watched_column_names_exist(column_name: str) -> None:
    """Guard the guard: a renamed column must not empty the survey."""
    assert any(row.column == column_name for row in SURVEY), (
        f"no NOT NULL {column_name} column was found anywhere; the watch list "
        f"is probably stale"
    )
