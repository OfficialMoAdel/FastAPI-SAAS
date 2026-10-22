"""The quiet-hours columns must be strings on a MIGRATED database (FastAPI_saas-nqk6).

The ORM declares both columns as five-character strings
(`user_preferences/models.py:90,95` -> `String(5)`), and three call sites depend
on that: `crud.py:113` writes them with a `setattr` of an `"HH:MM"` string, the
consumer at `notifications/service.py:611-612` calls
`time.fromisoformat(prefs.quiet_hours_start)`, and the schemas type them
`str | None`. Migration layer 05 created both as `sa.Time(timezone=True)`, and no
later migration mentions them, so a database built by `alembic upgrade head`
really has `timetz`.

MEASURED, not assumed (perf-local, bead `FastAPI_saas-9cmn`, artefact
`perf-results/RESULT-20261018-9cmn/`; a scratch database migrated to
`07_observability` against the `create_all` database as a control, one arm per
process, every value read through the application's OWN ORM because a raw
asyncpg read hides this class of defect):

  * the create the application performs is REFUSED -
    `DatatypeMismatchError: column "quiet_hours_start" is of type time with time
    zone but expression is of type character varying` - and it is refused with
    both columns bound to NULL as well, so the failure is the BIND TYPE and no
    quiet-hours value could ever have been written;
  * a row that does exist reads back as `datetime.time`, and
    `time.fromisoformat` then raises `TypeError`, which the consumer's
    `except ValueError` does NOT catch, so the exception escapes
    `should_send_notification`;
  * `UserPreferencesRead` does not catch it either: Pydantic coerces `time`
    into the `str` it declares, so the API serialises a value the notification
    path cannot parse;
  * the `create_all` control accepted every write and read, which is why
    development never sees this. `create_all` derives the schema FROM the ORM,
    so a dev database can never show migration drift at all.

So this file pins the invariant that would have caught it, in the shape of
`test_api_keys_column_type_parity.py`: the layer that creates the table must
declare the type the ORM declares. That is the half `FastAPI_saas-nqk6` applied
- an edit to `05_content_storage.py`, not a new revision, because the schema is
not shipped and `src/migrations/AGENTS.md` says a schema change should extend
the relevant layer's `upgrade()` rather than stack a dated revision, "unless the
layer is already applied in production".

It also settles the nullability pair `FastAPI_saas-9cmn` measured incidentally
and which no gate compares:

  * `passkey_credentials.credential_id_hash` is `NOT NULL` with a unique index in
    migration 02 but nullable on the ORM, so the ORM permits a state the
    database refuses - the probe insert failed with `NotNullViolationError`. The
    ORM moves: the only writer (`auth/services/passkey.py:206-221`) always
    computes the hash, and the read path looks the credential up BY that hash.
    Loosening the database instead would be worse, because a unique index
    permits unlimited NULLs and the hash is the lookup key;
  * `passkey_credentials.device_type` is the mirror image - `NOT NULL` on the
    ORM, nullable in migration 02 - so the database is the looser side and the
    creating layer's `create_table` now tightens it.
"""

from __future__ import annotations

import ast
import re
from datetime import time
from pathlib import Path
from typing import Any

import pytest

VERSIONS = Path("src/migrations/versions")
MIGRATION = VERSIONS / "05_content_storage.py"
AUTH_MIGRATION = VERSIONS / "02_authentication.py"
PREFERENCES_MODEL = Path("src/app/modules/user_preferences/models.py")
AUTH_MODEL = Path("src/app/modules/auth/models.py")
AUTH_SCHEMAS = Path("src/app/modules/auth/schemas.py")
CONSUMER = Path("src/app/modules/notifications/service.py")

CREATING_LAYER = "05_content_storage"
QUIET_HOURS_COLUMNS = ("quiet_hours_start", "quiet_hours_end")

# The SQL families that deliver a Python `str` through SQLAlchemy's asyncpg
# dialect. Everything else on this table delivers a temporal object, which is
# what `time.fromisoformat` refuses.
STRING_DECLARATIONS = frozenset({"String", "Text"})

# The Python type the dialect hands back per declared family, as MEASURED in
# perf-results/RESULT-20261018-9cmn/ (varchar -> str, timetz -> datetime.time).
PYTHON_TYPE_BY_DECLARATION: dict[str, type[Any]] = {"String": str, "Text": str, "Time": time}

# A quiet-hours value the schemas accept: `^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$`.
VALID_QUIET_HOURS = "22:00"


def _create_table_block(source: str, table: str) -> str:
    """The `op.create_table("<table>", ...)` chunk of a migration.

    Split on `op.create_table(` rather than matching nested parens so it does not
    depend on indentation, then stop at the next table so a neighbouring
    create_table cannot be mistaken for part of this one.
    """
    for chunk in source.split("op.create_table(")[1:]:
        if chunk.lstrip().startswith(f'"{table}"'):
            return chunk
    raise AssertionError(f"{table} create_table not found")


def _column_declaration(block: str, table: str, column: str) -> str:
    """The whole `sa.Column("<column>", ...)` text, parens balanced.

    Balanced rather than regex-to-comma because the declared types carry their own
    parentheses (`sa.String(length=5)`, `postgresql.JSONB(astext_type=...)`).
    """
    match = re.search(rf'sa\.Column\(\s*"{column}"', block)
    if match is None:
        raise AssertionError(f"{table}.{column} is not created by this migration")
    depth = 0
    for index in range(match.start(), len(block)):
        char = block[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return block[match.start() : index + 1]
    raise AssertionError(f"unbalanced sa.Column(...) for {table}.{column}")


def _type_text(declaration: str) -> str:
    """Just the type portion of a declaration.

    Three shapes arrive here: a full `sa.Column("name", TYPE, ...)` from a
    migration, a full `mapped_column(TYPE, ...)` from the ORM, and a bare
    `sa.String(length=5)`. Without this, the first call-shaped token in a column
    declaration is `sa.Column` or `mapped_column` itself, so every column would
    read as the same type and the parity assertions below would be vacuous.
    """
    text = declaration.strip()
    for wrapper in (r"sa\.Column\((.+)\)", r"mapped_column\((.+)\)"):
        match = re.fullmatch(wrapper, text, re.DOTALL)
        if match is None:
            continue
        text = match.group(1)
        name = re.match(r'\s*(?:"[^"]*"|\w+)\s*,', text)
        if name is not None:
            text = text[name.end() :]
        break
    return text


def _declared_base_type(declaration: str) -> str:
    """`sa.String(length=5)` -> `String`; `sa.Time(timezone=True)` -> `Time`."""
    match = re.search(r"(?:sa\.)?(\w+)\(", _type_text(declaration))
    assert match is not None, f"no type constructor in {declaration}"
    return match.group(1)


def _declared_width(declaration: str) -> int | None:
    """Both `String(5)` and `sa.String(length=5)` forms; None when unstated."""
    text = _type_text(declaration)
    positional = re.search(r"String\(\s*(\d+)\s*\)", text)
    if positional is not None:
        return int(positional.group(1))
    keyword = re.search(r"length\s*=\s*(\d+)", text)
    return int(keyword.group(1)) if keyword else None


def _orm_mapped_column(model: Path, column: str) -> str:
    """The ORM `mapped_column(...)` text for one column."""
    tree = ast.parse(model.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            if stmt.target.id == column and stmt.value is not None:
                return ast.unparse(stmt.value)
    raise AssertionError(f"{model} does not map a column named {column}")


def _orm_annotation(model: Path, column: str) -> str:
    """The declared Python annotation of one ORM column, e.g. `str | None`."""
    tree = ast.parse(model.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            if stmt.target.id == column and stmt.annotation is not None:
                return _unwrap_mapped(ast.unparse(stmt.annotation))
    raise AssertionError(f"{model} does not annotate a column named {column}")


def _unwrap_mapped(annotation: str) -> str:
    """`Mapped[str | None]` -> `str | None`; the wrapper is not part of the claim."""
    match = re.fullmatch(r"Mapped\[(.+)]", annotation.strip())
    return match.group(1) if match else annotation


def _schema_field_source(schema_class: str, field: str) -> str:
    """The `ast.unparse` of one AnnAssign inside a Pydantic model."""
    tree = ast.parse(AUTH_SCHEMAS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == schema_class:
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.target.id == field:
                        return ast.unparse(stmt) if stmt.value is not None else ""
    raise AssertionError(f"{AUTH_SCHEMAS} has no {schema_class}.{field}")


def _revision_graph() -> tuple[dict[str, str | None], dict[str, Path]]:
    """`(revision -> down_revision, revision -> file)` across every version file."""
    down: dict[str, str | None] = {}
    files: dict[str, Path] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: dict[str, str | None] = {}
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
                continue
            if node.target.id not in {"revision", "down_revision"}:
                continue
            if not isinstance(node.value, ast.Constant):
                continue
            literal = node.value.value
            found[node.target.id] = literal if isinstance(literal, str) else None
        revision = found.get("revision")
        assert isinstance(revision, str), f"{path} declares no revision id"
        down[revision] = found.get("down_revision")
        files[revision] = path
    return down, files


def migration_heads() -> list[str]:
    """The revisions no other revision declares as its `down_revision`.

    This is the opposite end from `down_revision`: a revision is a head when it
    is nobody's parent. Selecting on the parent instead (`parent not in graph`)
    answers a different question - it finds the TAIL, the revision that descends
    from nothing - and calling that the head passes on any single-chain history,
    which is how a backwards version of this helper went unnoticed.
    """
    down, _ = _revision_graph()
    return sorted(revision for revision in down if revision not in set(down.values()))


# --------------------------------------------------------------------------
# The premise: if 05 does not create the table, every assertion below is vacuous
# --------------------------------------------------------------------------
def test_the_layer_that_creates_user_preferences_is_the_one_under_test() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    block = _create_table_block(source, "user_preferences")
    for column in QUIET_HOURS_COLUMNS:
        assert column in block, f"user_preferences.{column} is not created by {CREATING_LAYER}"


def test_there_is_exactly_one_migration_head() -> None:
    """A branching history would make "the latest layer is the head" meaningless.

    The head is asserted by NAME as well as by count: a head assertion that only
    checks the count cannot notice that it selected the wrong end of the chain.
    """
    heads = migration_heads()
    assert heads == ["08_enable_rls_policies"], (
        f"expected the head to be 08_enable_rls_policies, found {heads}"
    )


# --------------------------------------------------------------------------
# The drift: temporal in the migration, five-character string on the ORM
# --------------------------------------------------------------------------
@pytest.mark.parametrize("column", QUIET_HOURS_COLUMNS)
def test_the_migration_declares_the_string_the_orm_declares(column: str) -> None:
    """RED today: layer 05 declares `sa.Time(timezone=True)`.

    Everything else in this application treats these columns as `"HH:MM"`
    strings, and the column only delivers one if the migration declares a string
    type. Declaring a temporal type is not a slower or a looser version of the
    same column - it is a different type, and the ORM's bind is refused.
    """
    block = _create_table_block(MIGRATION.read_text(encoding="utf-8"), "user_preferences")
    declaration = _column_declaration(block, "user_preferences", column)
    base = _declared_base_type(declaration)
    assert base in STRING_DECLARATIONS, (
        f"user_preferences.{column} is declared {base} in {CREATING_LAYER} but the ORM, the "
        f"write path and the consumer all treat it as a string: {declaration}"
    )


@pytest.mark.parametrize("column", QUIET_HOURS_COLUMNS)
def test_the_declared_width_is_identical_on_both_sides(column: str) -> None:
    """`String(5)` on the ORM; the migration must not be wider or narrower.

    Neither side hard-codes the value under test - both are read from source -
    because three hard-coded copies of 5 is what let them drift apart silently.
    """
    block = _create_table_block(MIGRATION.read_text(encoding="utf-8"), "user_preferences")
    migration_width = _declared_width(_column_declaration(block, "user_preferences", column))
    orm_width = _declared_width(_orm_mapped_column(PREFERENCES_MODEL, column))
    assert orm_width is not None, f"the ORM no longer states a width for {column}"
    assert migration_width == orm_width, (
        f"the migration declares width {migration_width} while the ORM declares {orm_width}"
        + (
            " - a width of None means the column is not declared as a string at all, so this is the "
            "type drift showing up in the width comparison rather than a width disagreement"
        )
        if migration_width is None
        else ""
    )


@pytest.mark.parametrize("column", QUIET_HOURS_COLUMNS)
def test_the_orm_still_declares_the_string_the_three_call_sites_need(column: str) -> None:
    """The half that was never wrong, pinned so the fix cannot quietly move it."""
    mapped = _orm_mapped_column(PREFERENCES_MODEL, column)
    assert _declared_base_type(mapped) in STRING_DECLARATIONS, mapped
    assert _orm_annotation(PREFERENCES_MODEL, column) == "str | None", _orm_annotation(PREFERENCES_MODEL, column)
    assert "nullable=True" in mapped, mapped


# --------------------------------------------------------------------------
# The consumer: the exception class its handler has to cover
# --------------------------------------------------------------------------
def test_fromisoformat_raises_valueerror_and_nothing_else_for_a_string() -> None:
    """What the consumer's `except ValueError` is there for, stated as a fact.

    A malformed string is the only failure a string column can produce here, so
    the handler is correctly narrow - which is why this bead fixes the column
    type rather than widening the handler.
    """
    assert _fromisoformat_exception(VALID_QUIET_HOURS) is None
    assert _fromisoformat_exception("not-a-time") == "ValueError"
    assert _fromisoformat_exception("25:00") == "ValueError"


def test_a_temporal_value_raises_a_class_the_handler_does_not_catch() -> None:
    """The measured failure, reduced to the two facts that cause it.

    `TypeError` is not a subclass of `ValueError`, so a `timetz` column hands the
    consumer an error that escapes `should_send_notification` entirely. This
    passes today and must keep passing: it is the reason the column type is the
    thing to fix.
    """
    assert not issubclass(TypeError, ValueError)
    with pytest.raises(TypeError):
        time.fromisoformat(time(22, 0))  # type: ignore[arg-type]


def test_the_declared_column_type_is_the_one_the_consumer_can_parse() -> None:
    """RED today, and the assertion that states the consequence.

    This is the same link the two tests above make visible, tied to the declared
    type: if the migration declares a string family, the consumer receives a
    string and its handler is sufficient; if it declares anything else, the
    consumer receives an object `fromisoformat` refuses.
    """
    block = _create_table_block(MIGRATION.read_text(encoding="utf-8"), "user_preferences")
    declaration = _column_declaration(block, "user_preferences", "quiet_hours_start")
    python_type = PYTHON_TYPE_BY_DECLARATION.get(_declared_base_type(declaration))
    assert python_type is str, (
        f"{CREATING_LAYER} declares quiet_hours_start as {_declared_base_type(declaration)}, "
        f"which the dialect delivers as {python_type!r}; {CONSUMER} calls "
        f"time.fromisoformat on it, a call defined only for str"
    )


def test_the_consumer_still_parses_the_value_it_reads_and_handles_only_valueerror() -> None:
    """The three call sites, pinned: a future edit has to move them together."""
    consumer = CONSUMER.read_text(encoding="utf-8")
    assert "time.fromisoformat(prefs.quiet_hours_start)" in consumer
    assert "time.fromisoformat(prefs.quiet_hours_end)" in consumer
    assert "except ValueError as e:" in consumer, "the malformed-value handler moved or was renamed"
    crud = Path("src/app/modules/user_preferences/crud.py").read_text(encoding="utf-8")
    assert "setattr(prefs, key, value)" in crud, "quiet_hours is no longer written by setattr on the row"


def _fromisoformat_exception(value: Any) -> str | None:
    """The exception class `time.fromisoformat` raises for `value`, or None."""
    try:
        time.fromisoformat(value)
    except ValueError as error:
        return type(error).__name__
    return None


# --------------------------------------------------------------------------
# Which side moved, and what an existing database has to do
# --------------------------------------------------------------------------
def test_the_creating_layer_itself_declares_the_string_columns() -> None:
    """The repair is an edit to layer 05, not a new revision on top of it.

    Layer 05 is the origin layer that creates `user_preferences`, and the
    correction lands in its own `create_table`. That is right here: nothing has
    been shipped, and `src/migrations/AGENTS.md` fixes the rule - "a schema
    change should extend the relevant layer's upgrade()/downgrade() rather than
    stacking a new dated revision, unless the layer is already applied in
    production". Writing a revision for a schema nobody has shipped would be
    exactly the accumulation that rule exists to prevent.

    `alembic` will not re-run a `create_table` against a database that already
    applied layer 05, so a pre-existing database keeps its timetz columns until
    it is dropped and rebuilt. That is the supported path here:
    `DROP SCHEMA public CASCADE` + `CREATE SCHEMA public` + `alembic upgrade
    head`. If layer 05 ever IS applied in production the rule reverses and this
    test is wrong in the same way - the comment in the migration file says so,
    which is the earliest a reader can be warned.
    """
    heads = migration_heads()
    assert heads == ["08_enable_rls_policies"], (
        f"expected exactly one migration head (08_enable_rls_policies), found {heads}"
    )

    for column in QUIET_HOURS_COLUMNS:
        declaration = _column_declaration(
            _create_table_block(MIGRATION.read_text(encoding="utf-8"), "user_preferences"),
            "user_preferences",
            column,
        )
        assert "sa.String" in declaration, f"{column} is declared {declaration}, not a string"
        assert _declared_width(declaration) == 5, (
            f"{column} declares width {_declared_width(declaration)}, the ORM says 5"
        )

    repairs = [
        path
        for path in sorted(VERSIONS.glob("*.py"))
        if 'ALTER COLUMN "quiet_hours_start"' in path.read_text(encoding="utf-8")
    ]
    assert repairs == [], (
        f"{[p.name for p in repairs]} repairs quiet_hours_start with an ALTER COLUMN; the schema is not "
        "shipped, so this is a repair revision for a database that only exists in "
        "development - drop and rebuild it instead (drop the schema, then run "
        "alembic upgrade head) and keep the correction in the creating layer"
    )


# --------------------------------------------------------------------------
# The nullability pair, measured incidentally by FastAPI_saas-9cmn
# --------------------------------------------------------------------------
def test_credential_id_hash_is_not_optional_on_the_orm_anymore() -> None:
    """RED today: migration 02 says NOT NULL, the ORM says nullable.

    The ORM permitting a state the database refuses is what made the probe insert
    fail with `NotNullViolationError`. The ORM moves because the database side is
    the true one: `idx_passkey_credential_id_hash` is UNIQUE, a unique index
    permits unlimited NULLs, and the hash is the key the read path looks a
    credential up by (`auth/services/passkey.py:624`).
    """
    mapped = _orm_mapped_column(AUTH_MODEL, "credential_id_hash")
    assert "nullable=False" in mapped, mapped
    assert "default=None" not in mapped, mapped
    assert _orm_annotation(AUTH_MODEL, "credential_id_hash") == "str", (
        "credential_id_hash is required, so the annotation must not offer None"
    )
    source = _create_table_block(AUTH_MIGRATION.read_text(encoding="utf-8"), "passkey_credentials")
    assert "nullable=False" in _column_declaration(source, "passkey_credentials", "credential_id_hash")


def test_the_create_schema_agrees_with_the_database_about_the_hash() -> None:
    """RED today: `PasskeyCredentialCreate.credential_id_hash: str | None = None`.

    Making the model strict while the create schema still offers a default would
    leave the same disagreement one layer up: a caller could build a credential
    with no hash and get an IntegrityError at flush. The only writer always
    computes it (`auth/services/passkey.py:206-221`), so nothing is lost.
    """
    declaration = _schema_field_source("PasskeyCredentialCreate", "credential_id_hash")
    assert "| None" not in declaration, declaration
    assert "= None" not in declaration, declaration


def test_the_creating_layer_tightens_device_type_to_match_the_orm() -> None:
    """The mirror image, where the DATABASE was the looser side.

    `device_type` is `NOT NULL` on the ORM and nullable in migration 02, so this
    divergence can only ever let a bad row in, never refuse a good one - which is
    why it produced no symptom until a probe looked. Layer 02 is the layer that
    creates `passkey_credentials`, and it now declares the column NOT NULL,
    because this project is pre-release and `src/migrations/AGENTS.md` says to
    extend the layer rather than stack a revision on top of it.

    It deliberately does NOT touch the width (50 against the ORM's 100): that is
    `FastAPI_saas-9cmn`'s benign row, measured to be benign on domain grounds, and
    widening it here would widen this bead's blast radius.
    """
    source = _create_table_block(AUTH_MIGRATION.read_text(encoding="utf-8"), "passkey_credentials")
    declaration = _column_declaration(source, "passkey_credentials", "device_type")
    assert "nullable=False" in declaration, declaration

    repairs = [
        path
        for path in sorted(VERSIONS.glob("*.py"))
        if 'ALTER COLUMN "device_type" SET NOT NULL' in path.read_text(encoding="utf-8")
    ]
    assert repairs == [], (
        f"{[p.name for p in repairs]} sets device_type NOT NULL with an ALTER COLUMN; the schema is not "
        "shipped, so this is a repair revision for a database that only exists in development"
    )


def _function_source(path: Path, function: str) -> str:
    """`ast.unparse` of one function's body, so prose in the docstring is excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            return ast.unparse(node)
    raise AssertionError(f"{path} has no function {function}")
