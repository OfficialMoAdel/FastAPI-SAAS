"""Two columns are narrower in the schema than the ORM declares (FastAPI_saas-auz2).

`files.storage_path` is `String(1024)` on the model and `sa.String(length=500)` in
layer 05; `audit_logs.user_agent` maps `Text` on the model and is
`sa.String(length=500)` in layer 07. MEASURED by `FastAPI_saas-9cmn` on an
`alembic upgrade head` database (`perf-results/RESULT-20261018-9cmn/`, one arm per
process, every value read through the application's own ORM because a raw asyncpg
read hides this class of defect):

  * `files.storage_path` accepts 500 characters, refuses 501, and refuses 1024 -
    the width the ORM itself declares. This is not a declaration nobody has
    exercised; it is a contract the database actively refuses, and the refusal
    arrives as an untranslated `asyncpg.StringDataRightTruncationError`, so a
    caller sees a 500 rather than a validation message.
  * `audit_logs.user_agent` is `character varying(500)` and refuses 501. A
    `User-Agent` over 500 characters is ordinary, not exotic, and `Text` states
    "no cap" outright, so the cap is invisible in the model and enforced only by
    the schema.

`create_all` derives the schema FROM the ORM, so a development database can never
show either column: on a `create_all` database both are exactly what the model
says. Only a migrated database has the narrower column.

WHICH SIDE MOVES. Both columns widen to what the ORM already declares, and the
ORM is not touched. The bead offered narrowing the model to 500 instead, and that
is the option that leaves the defect in place: a `String(500)` model would still
accept a 501-character key and still be refused by the database, which is the
same untranslated `DBAPIError`. Narrowing the declaration silences the gate
without fixing the failure. `Text` on `user_agent` is the stronger claim of the
two, and layer 02 already declares magic-link `user_agent` as `sa.Text()` for the
same reason.

WHERE THE FIX LIVES. In the creating layers, not in a new revision. Per
`src/migrations/AGENTS.md` (Package Identity, and "Correcting an applied
`create_table` takes TWO files"), a schema change extends the relevant layer's
`upgrade()` rather than stacking a dated revision "unless the layer is already
applied in production". Nothing has been shipped, so the rule applies in the form
that corrects layer 05 and layer 07 in place and drops/rebuilds the database.
Two existing oracles pin the migration head by name (`08`), so a new revision for
a `create_table`-only change would contradict the repository rather than follow
it.

The two remaining `TYPE_ALLOWLIST` rows are NOT in scope and stay: `files.mime_type`
(ORM 128 / column 100) and `passkey_credentials.device_type` (ORM 100 / column 50)
were measured benign on domain grounds - a registered media type and a WebAuthn
device-type token are both far shorter than the column that enforces them.

The declarations below are read through `scripts/checks/schema_parity.py`'s own
parsers rather than re-parsed here. One parse, two questions asked of it: an
oracle with its own regex copy is a second source of truth about what the gate
will see, and the whole point of this file is that the gate and the schema agree.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

VERSIONS = Path("src/migrations/versions")
STORAGE_MIGRATION = VERSIONS / "05_content_storage.py"
AUDIT_MIGRATION = VERSIONS / "07_observability.py"
GATE = Path("scripts/checks/schema_parity.py")

# `table -> (column, layer that creates the table)`
COLUMNS: dict[str, tuple[str, str]] = {
    "files": ("storage_path", "05_content_storage"),
    "audit_logs": ("user_agent", "07_observability"),
}

# The width the audit column refused, and the one it must accept. 501 is the
# boundary `FastAPI_saas-9cmn` measured; 4096 is an ordinary long `User-Agent`
# carrying a full cookie/debug preamble, i.e. the case a 500-character cap breaks.
USER_AGENT_501 = "u" * 501
USER_AGENT_4096 = "u" * 4096


@pytest.fixture(scope="module")
def gate() -> Any:
    """`scripts/checks/schema_parity.py` loaded from its path, as its own test does."""
    spec = importlib.util.spec_from_file_location("check_schema_parity", GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def declarations(gate: Any) -> dict[tuple[str, str], tuple[str, str]]:
    """`(table, column) -> (orm declaration, migration declaration)` for the two."""
    models = gate._model_column_types()
    migrations = gate._migration_column_types()
    pairs: dict[tuple[str, str], tuple[str, str]] = {}
    for table, (column, _layer) in COLUMNS.items():
        orm_type = models.get(table, {}).get(column)
        migration_type = migrations.get(table, {}).get(column)
        assert orm_type is not None, f"the gate parsed no ORM column {table}.{column}"
        assert migration_type is not None, f"the gate parsed no migration column {table}.{column}"
        pairs[(table, column)] = (orm_type, migration_type)
    return pairs


def _orm(declarations: dict[tuple[str, str], tuple[str, str]], table: str) -> str:
    return declarations[(table, COLUMNS[table][0])][0]


def _migration(declarations: dict[tuple[str, str], tuple[str, str]], table: str) -> str:
    return declarations[(table, COLUMNS[table][0])][1]


# --------------------------------------------------------------------------
# The premise: if either layer stops creating its table, everything below is vacuous
# --------------------------------------------------------------------------
@pytest.mark.parametrize("table", sorted(COLUMNS))
def test_the_creating_layer_still_creates_the_column(table: str) -> None:
    """The layer named is the one that owns the `create_table`.

    Pinned by name rather than inferred, because an inference that silently
    matched nothing is how a parity oracle starts passing on a tree it does not
    describe.
    """
    column, layer = COLUMNS[table]
    migration = VERSIONS / f"{layer}.py"
    assert migration.exists(), f"{layer} is not a migration file"
    source = migration.read_text(encoding="utf-8")
    assert re.search(rf'op\.create_table\(\s*"{table}"', source), f"{layer} does not create {table}"
    assert re.search(rf'sa\.Column\(\s*"{column}"', source), f"{layer} does not create {table}.{column}"


# --------------------------------------------------------------------------
# The drift, as the gate classifies it
# --------------------------------------------------------------------------
def test_the_gate_reports_no_observable_drift_for_either_column(gate: Any) -> None:
    """RED today: two `narrower_in_migration` rows, both allowlisted pending auz2.

    This is the statement that matters operationally: after the fix these two
    columns are no longer a suppressed row in `TYPE_ALLOWLIST` but simply not
    drift. `DRIFT_KINDS` is read off the gate rather than restated, so widening
    `DRIFT_KINDS` later cannot quietly narrow what this asserts.
    """
    drift = gate._type_drift(gate._model_column_types(), gate._migration_column_types())
    keys = {gate._key(line) for line in drift}
    for table in COLUMNS:
        assert (table, COLUMNS[table][0]) not in keys, (
            f"{table}.{COLUMNS[table][0]} is still drift: "
            f"{[line for line in drift if line.startswith(table + '.')]}"
        )


def test_either_column_is_not_narrower_than_the_orm_in_either_spelling(
    declarations: dict[tuple[str, str], tuple[str, str]], gate: Any
) -> None:
    """RED today: 500 against 1024, and 500 against an explicit `Text`.

    The two sides spell a width differently - `String(1024)` on a model,
    `sa.String(length=1024)` in a migration - so both spellings have to be read.
    Reading only the positional form makes every migration column look unbounded
    and silently downgrades every narrowed column to benign.

    The two columns are NOT the same assertion. `files.storage_path` makes a claim
    about a number, so the column has to carry that number. `audit_logs.user_agent`
    maps `Text`, which states no number at all, so the only thing the column can do
    is fail to contradict it - and comparing a width to `None` compares a claim with
    the absence of one.
    """
    for table, (column, _layer) in COLUMNS.items():
        orm_declared = _orm(declarations, table)
        migration_declared = _migration(declarations, table)
        if gate._states_unbounded(orm_declared):
            assert gate._declared_width(migration_declared) is None, (
                f"{table}.{column} maps Text on the ORM and caps at "
                f"{gate._declared_width(migration_declared)} in the creating layer"
            )
            continue
        orm_width = gate._declared_width(orm_declared)
        assert orm_width is not None, f"the ORM no longer states a width for {table}.{column}"
        assert gate._declared_width(migration_declared) == orm_width, (
            f"{table}.{column} is declared {migration_declared!r} in the creating layer against "
            f"{orm_declared!r} on the ORM: the column caps at "
            f"{gate._declared_width(migration_declared)} where the ORM allows {orm_width}, and a "
            f"capped column refuses a value the model considers legal"
        )


def test_the_audit_user_agent_column_states_no_cap(
    declarations: dict[tuple[str, str], tuple[str, str]], gate: Any
) -> None:
    """RED today: `character varying(500)` behind a column that claims there is no cap.

    `Text` is a claim, not the absence of one: a bare `String` with no length means
    the model never said 500 was enough, whereas `Text` says outright that no cap
    applies. The column has to answer the claim it was given.
    """
    declared = _migration(declarations, "audit_logs")
    assert gate._states_unbounded(declared), (
        f"audit_logs.user_agent is declared {declared!r} in layer 07 while the model maps Text; "
        "the cap belongs to the model that asked for it, not to the schema"
    )


@pytest.mark.parametrize("user_agent", [USER_AGENT_501, USER_AGENT_4096], ids=["501", "4096"])
def test_the_declared_column_accepts_a_user_agent_the_old_cap_refused(
    user_agent: str, declarations: dict[tuple[str, str], tuple[str, str]], gate: Any
) -> None:
    """The acceptance criterion, stated over the declaration that decides it.

    This is a declaration-level assertion, and it is honest about being one: the
    runtime acceptance of a 501-character header was MEASURED on a real migrated
    database in `perf-results/RESULT-20261018-9cmn/` (500 accepted, 501 refused),
    and a unit test cannot re-probe a server. What it can do is pin the fact the
    measurement turned on - an uncapped column accepts every length, while a
    `varchar(500)` refuses the first character past 500 - so the fix cannot be
    reintroduced as "500 is nearly always enough".

    The string is built rather than pasted because its length is the point: a
    literal is invisible to a reader and to `len`.
    """
    assert len(user_agent) > 500
    assert gate._declared_width(_migration(declarations, "audit_logs")) is None, (
        f"a {len(user_agent)}-character User-Agent is refused by a column that caps at "
        f"{gate._declared_width(_migration(declarations, 'audit_logs'))}"
    )


def test_the_storage_key_column_is_the_width_the_module_generates(
    declarations: dict[tuple[str, str], tuple[str, str]], gate: Any
) -> None:
    """The width is not theoretical: it is the ORM's own, and 1024 is a real limit.

    `storage/service.py` builds `tenants/{tenant_id}/files/{uuid}.{ext}`, about 60
    characters, so 500 has never been reached in practice - which is exactly why
    nothing complained. Azure Blob caps a blob name at 1024, so the ORM's width is
    the portable maximum rather than an invented number, and it is the number the
    column must carry.
    """
    assert gate._declared_width(_migration(declarations, "files")) == 1024


# --------------------------------------------------------------------------
# The half that was never wrong, pinned so the fix cannot quietly move it
# --------------------------------------------------------------------------
def test_the_orm_side_did_not_move(declarations: dict[tuple[str, str], tuple[str, str]], gate: Any) -> None:
    """Both models still declare what they declared; the schema was what conformed.

    Written as the columns the repository cares about rather than as widths, so a
    future edit that moves the model to a different width fails HERE as well as at
    the parity assertion - one edit, two places that must agree.
    """
    assert _orm(declarations, "files").startswith("String(")
    assert _orm(declarations, "audit_logs").startswith("Text")


def test_the_audit_writer_still_takes_the_header_verbatim() -> None:
    """Why the cap matters: the value is a client header, not a field we shape.

    `auth/service.py` copies `request.headers.get("user-agent")` straight through,
    `trail.py` passes it to the writer, and the writer stores `data["user_agent"]`
    without truncating. So nothing upstream of the INSERT can shorten a long header
    - which is why the refusal surfaces as a 500 instead of a validation message.
    """
    auth_service = Path("src/app/modules/auth/service.py").read_text(encoding="utf-8")
    assert 'request.headers.get("user-agent")' in auth_service
    trail = Path("src/app/modules/audit_logs/trail.py").read_text(encoding="utf-8")
    assert "user_agent=user_agent," in trail
    writer = Path("src/app/modules/audit_logs/service.py").read_text(encoding="utf-8")
    assert '"user_agent": data.get("user_agent"),' in writer


# --------------------------------------------------------------------------
# Which side moved, and what an existing database has to do
# --------------------------------------------------------------------------
def test_neither_column_is_repaired_by_a_new_revision() -> None:
    """The repair is an edit to the creating layer, not a revision on top of it.

    `alembic` will not re-run a `create_table` against a database that already
    applied layer 05 or 07, so a pre-existing database keeps its 500-character
    columns until it is dropped and rebuilt - `DROP SCHEMA public CASCADE` +
    `CREATE SCHEMA public`, then `alembic upgrade head`. That is the supported path
    while the schema is unshipped; the reversal condition (a layer applied in
    production needs the corrected `create_table` PLUS an `ALTER` revision) is
    stated in `src/migrations/AGENTS.md`.
    """
    repairs = [
        f"{path.name}:{table}.{column}"
        for path in sorted(VERSIONS.glob("*.py"))
        for table, (column, _layer) in COLUMNS.items()
        if re.search(rf'ALTER\s+COLUMN\s+"?{column}"?\s+(?:SET\s+DATA\s+)?TYPE', path.read_text(encoding="utf-8"))
        or re.search(rf'alter_column\(\s*"{table}"\s*,\s*"{column}"', path.read_text(encoding="utf-8"))
    ]
    assert repairs == [], (
        f"{repairs} repairs one of these columns with an ALTER COLUMN; the schema is not shipped, so "
        "this is a repair revision for a database that only exists in development - drop and "
        "rebuild it instead and keep the correction in the creating layer"
    )


def test_the_two_rows_are_no_longer_allowlisted(gate: Any) -> None:
    """RED today: both rows are still in `TYPE_ALLOWLIST`, still marked pending auz2.

    A stale allowlist entry is itself a failure of this gate, but only once the
    drift is gone - while the drift remains, the entry is load-bearing. So this is
    the assertion that says the row must have been DELETED, not merely renamed:
    keeping it with a new reason would suppress a column the gate can now see
    disagreeing with its model.

    The two benign rows are asserted present in the same test, because "the
    allowlist is empty" is a weaker claim than "the allowlist holds exactly the
    two findings that were triaged benign".
    """
    allowlist: dict[tuple[str, str], str] = gate.TYPE_ALLOWLIST
    for table, (column, _layer) in COLUMNS.items():
        assert (table, column) not in allowlist, (
            f"{table}.{column} is still allowlisted: {allowlist.get((table, column))!r}"
        )
    for benign in (("files", "mime_type"), ("passkey_credentials", "device_type")):
        assert benign in allowlist, f"{benign[0]}.{benign[1]} left the allowlist without a decision"
        assert allowlist[benign].startswith("benign:"), allowlist[benign]
