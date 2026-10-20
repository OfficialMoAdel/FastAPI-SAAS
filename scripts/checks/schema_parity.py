"""Static ORM <-> migration column parity gate.

A column declared on a SQLAlchemy model but never created (or added) by a
migration is invisible to every other gate in this repo:

* ``create_all`` DERIVES the schema from the ORM, so a database built that way
  always has the column and nothing diverges;
* the index gates compare indexes, not columns;
* the test suite passes, because the tests run against a create_all schema.

The failure mode is a hard one: on a database built by ``alembic upgrade head``
the query raises ``UndefinedColumnError``. ``pricing.currency`` was exactly
this — declared on the ORM, read by ``billing/service.py:189``, and absent from
migration 04, so every Pricing read failed on any migrated database.

NAMES were not enough (``FastAPI_saas-d201``). ``api_keys.scopes`` was ``Text``
on the model and ``postgresql.JSONB`` in migration 03, which this gate could
not see because it compared names only. On a migrated database the asyncpg
dialect decodes a jsonb column into a Python object, so ``json_loads`` on the
value the writer had stored as a string raised — and ``FastAPI_saas-vthy``
measured the consequence: minting kept working (asyncpg sends untyped
parameters, the server coerces the literal) while every ``X-API-Key`` request
500'd on the read.

So this gate also compares TYPES, and the comparison is deliberately narrow: it
fails only where a difference is OBSERVABLE at runtime.

* different SQL family -> the driver hands the application a different Python
  object than the ORM promised (the ``api_keys.scopes`` class);
* the migration narrower than the ORM -> a value the ORM accepts is rejected or
  truncated by the column;
* the migration wider -> benign, the database accepts everything the ORM does;
* different spelling, same shape (``BYTEA``/``LargeBinary``,
  ``DateTime``/``DateTime(True)``, ``JSON``/``JSONB``, ``BigInteger``/``Integer``)
  -> benign.

Census that settled that scope (351 columns compared, 114 raw mismatches, of
which 58 were the ORM side failing to resolve at all and only 2 were a family
mismatch): ``perf-results/RESULT-20261017-vthy/api_keys_column_type_parity.json``.

The ORM side must resolve EVERY column or the comparison is blind on exactly the
columns it cannot see, which is why the resolvers below fall back to the
``Mapped[...]`` annotation and resolve ``ForeignKey("t.col")`` by reference
rather than recording the call's source text.

The CI ``alembic check`` stays non-blocking; see ``.github/workflows/tests.yml``
for why this gate and that step divide the work rather than overlap.

This is a pure-AST gate: no database, no ORM import, so it runs in the lint
job and fails fast.

Exit codes: 0 = parity, 1 = drift, 2 = gate could not run.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

#: Repo root = two levels up from this file (scripts/checks/<gate>.py). The
#: paths are anchored to the ROOT rather than to the current working
#: directory so the gate reads the same tree whichever directory it is run
#: from — a CWD-relative path silently reads the wrong tree, or nothing.
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "app"
MIGRATIONS = REPO_ROOT / "src" / "migrations" / "versions"

# Relationship/association helpers that look like columns to a naive walk.
NON_COLUMN_MARKERS = ("relationship(", "association_proxy(")

# SQL family per DECLARED type name. Two declarations in the same family hand
# the application the same Python shape, so a difference between them is a
# spelling, not a defect. Only families whose result shape differs need to be
# exact; widths are compared separately.
FAMILY_BY_DECLARED = {
    "Integer": "int",
    "BigInteger": "int",
    "SmallInteger": "int",
    "Numeric": "decimal",
    "Float": "float",
    "Boolean": "bool",
    "String": "string",
    "Text": "string",
    "Enum": "string",
    "UUID": "uuid",
    "JSON": "json",
    "JSONB": "json",
    "DateTime": "datetime",
    "Date": "date",
    "Time": "time",
    "BYTEA": "bytes",
    "LargeBinary": "bytes",
}

# Python annotation -> declared type, used when ``mapped_column()`` carries no
# positional type argument. Derived from the annotations in this tree, not
# guessed at: every entry here is a column the census could otherwise not read.
PYTHON_TO_DECLARED = {
    "int": "Integer",
    "str": "String",
    "bool": "Boolean",
    "float": "Float",
    "bytes": "LargeBinary",
    "datetime": "DateTime",
    "date": "Date",
    "time": "Time",
    "Decimal": "Numeric",
    "UUID": "UUID",
    "Uuid": "UUID",
    "dict": "JSON",
}

DRIFT_KINDS = ("family_mismatch", "narrower_in_migration")

# Observable drift this gate reports but does not fail on. Every value must say
# WHY, and must distinguish a triaged `benign:` claim from a `pending` one owned
# by a bead: an allowlist whose entries cannot be re-checked is worse than no
# allowlist, because it reads as a clean bill of health. A stale entry — one
# that no longer corresponds to real drift — is itself a failure, so this
# cannot quietly outlive the finding it excuses.
TYPE_ALLOWLIST: dict[tuple[str, str], str] = {
    # user_preferences.quiet_hours_start / .quiet_hours_end were here until
    # FastAPI_saas-nqk6 (2026-10-18). They were the worst instance of this class
    # ever measured: migration 05 declared sa.Time(timezone=True) against the
    # ORM's String(5), so on a migrated database the ORM could not insert a
    # user_preferences row AT ALL (DatatypeMismatchError, raised identically with
    # both columns bound to NULL, which is what made it a bind-type failure), and
    # a row written any other way read back as datetime.time so
    # notifications/service.py:611's time.fromisoformat raised TypeError - not the
    # ValueError its handler catches. Fixed in FastAPI_saas-nqk6 by correcting layer
    # 05's declaration - and NOT by adding a revision to repair the existing
    # databases, because the schema is not shipped yet and `src/migrations/AGENTS.md`
    # says a schema change extends the relevant layer rather than stacking a new
    # revision "unless the layer is already applied in production". The reversal
    # condition is stated in that file.
    #
    # files.storage_path and audit_logs.user_agent were here until
    # FastAPI_saas-auz2 (2026-10-19), and both were fixed the same way the quiet
    # hours were: the COLUMN widened to what the ORM already declared, and the
    # ORM was left alone. Layer 05 now declares storage_path String(1024) (it was
    # 500, and the column refused the ORM's own width) and layer 07 declares
    # user_agent sa.Text() (it was String(500) behind a model that maps Text, i.e.
    # a model that says "no cap" outright). Narrowing the model to 500 instead was
    # rejected rather than merely declined: a String(500) model still accepts a
    # 501-character value and still gets refused by the database, which is the same
    # untranslated DBAPIError - it silences this gate without fixing the failure.
    # Measured evidence for both: perf-results/RESULT-20261018-9cmn/.
    # Oracle: tests/unit/migrations/test_width_parity_storage_and_audit.py.
    (
        "files",
        "mime_type",
    ): (
        "benign: asserted by FastAPI_saas-d201 and MEASURED by FastAPI_saas-9cmn - the "
        "column enforces 100, rejects 101, and rejects the ORM's 128. An IANA media type is "
        "a short token sequence and 100 characters is above the longest registered type, so "
        "no legitimate value can reach the boundary the column enforces."
    ),
    (
        "passkey_credentials",
        "device_type",
    ): (
        "benign: asserted by FastAPI_saas-d201 and MEASURED by FastAPI_saas-9cmn - the "
        "column enforces 50, rejects 51, and rejects the ORM's 100. WebAuthn device types "
        "are the four single-token values in the specification, so 50 characters cannot "
        "truncate one."
    ),
}


def _normalise(declared: str) -> str:
    """Strip the module prefix a declaration is spelled with.

    `sa.Text()`, `postgresql.JSONB(...)` and `sqlalchemy.dialects.postgresql.UUID()`
    name the same types as `Text()`, `JSONB(...)` and `UUID(...)`; comparing the
    spellings would report all three as different families.
    """
    text = " ".join(declared.strip().split())
    while True:
        head, dot, tail = text.partition(".")
        if not dot or "(" in head:
            return text
        text = tail


def _base_name(declared: str) -> str:
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", _normalise(declared))
    return match.group(1) if match else _normalise(declared)


def _declared_width(declared: str) -> int | None:
    """The length a String/Text/Enum column is capped at, in either spelling.

    The two sides do not spell a width the same way: the models write
    ``String(255)`` and the migrations write ``sa.String(length=255)``. Reading
    only the positional form made every migration column look unbounded, which
    silently downgraded every narrowed column to "wider" — i.e. to benign.
    """
    text = _normalise(declared)
    if _base_name(text) not in {"String", "Text", "Enum"}:
        return None
    positional = re.match(r"^(?:String|Text)\((\d+)\)", text)
    if positional:
        return int(positional.group(1))
    keyword = re.search(r"\blength\s*=\s*(\d+)", text)
    return int(keyword.group(1)) if keyword else None


def _states_unbounded(declared: str) -> bool:
    """True for `Text`, which is an explicit claim of no cap.

    `String` with no length is SQLAlchemy's "unbounded" too, but it is the
    ABSENCE of a width rather than a claim of one — the model never said 500 was
    enough. `Text` does say it, so a capped column contradicts it. That
    difference is the whole reason the two land in different buckets.
    """
    return _base_name(declared) == "Text"


def _column_family(declared: str) -> str | None:
    """The SQL family of a declared type, or None when it cannot be read."""
    return FAMILY_BY_DECLARED.get(_base_name(declared))


def _classify_type(orm_type: str, migration_type: str) -> str:
    """Which of the four buckets a pair of declarations falls into.

    ``equivalent_naming`` is not drift, and the split between
    ``wider_in_migration`` and ``narrower_in_migration`` is directional on
    purpose: only the tighter column can reject a value the ORM accepted.
    """
    orm_family = _column_family(orm_type)
    migration_family = _column_family(migration_type)
    if orm_family is None or migration_family is None:
        return "unreadable"
    if orm_family != migration_family:
        return "family_mismatch"

    orm_width = _declared_width(orm_type)
    migration_width = _declared_width(migration_type)
    if orm_width == migration_width:
        return "equivalent_naming"
    if migration_width is None:
        # ORM Text or String(n) against an unbounded column: nothing can be cut.
        return "wider_in_migration"
    if orm_width is None:
        # `Text` states "no cap" outright, so a capped column contradicts it.
        if _states_unbounded(orm_type):
            return "narrower_in_migration"
        # `String` with no length is SQLAlchemy's "unbounded", i.e. the model
        # states NO width. The column capping it is a schema decision the ORM
        # never contradicted, so this is reported rather than failed: making it
        # drift would need fifteen allowlist entries on day one, which is the
        # dumping ground this gate's allowlist exists to avoid.
        return "unbounded_orm_width"
    if migration_width < orm_width:
        return "narrower_in_migration"
    if migration_width > orm_width:
        return "wider_in_migration"
    return "equivalent_naming"


def _split_args(text: str) -> list[str]:
    """Top-level comma-separated arguments, ignoring commas inside brackets."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    for char in text:
        if char == '"':
            in_string = not in_string
        if not in_string:
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append("".join(current))
                current = []
                continue
        current.append(char)
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


def _closing_paren(text: str, open_at: int) -> int:
    depth = 0
    for index in range(open_at, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _annotation_declared(node: ast.AnnAssign) -> str | None:
    """`Mapped[int]`, `Mapped[str | None]`, `Mapped[uuid.UUID]` -> a declared type.

    The annotation is the only source for ``mapped_column()`` with no positional
    type argument, which is how ``id: Mapped[int] = mapped_column(primary_key=True)``
    is declared in this repo. Reading it is what stops those columns from being
    invisible to the type half.

    The ``Mapped`` wrapper is stripped rather than unwrapped by descending to the
    subscript's base, because the base of ``Mapped[int]`` is ``Mapped`` itself.
    """
    annotation = node.annotation
    if isinstance(annotation, ast.Subscript) and _base_name(ast.unparse(annotation.value)) == "Mapped":
        annotation = annotation.slice
    # `str | None`, `Optional[str]` and `list[str]` all wrap the real type.
    while True:
        if isinstance(annotation, ast.Subscript):
            annotation = annotation.slice
            continue
        if isinstance(annotation, ast.BinOp):
            annotation = annotation.left
            continue
        break
    name: str | None = None
    if isinstance(annotation, ast.Name):
        name = annotation.id
    elif isinstance(annotation, ast.Attribute):
        name = annotation.attr
    elif isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        name = annotation.value
    if name is None or name == "Mapped":
        return None
    return PYTHON_TO_DECLARED.get(name, name)


def _model_column_types() -> dict[str, dict[str, str]]:
    """table name -> column name -> declared type, from every module with models.

    Resolution order per column: the positional type argument of
    ``mapped_column(...)``, then — when that argument is a legacy column NAME or
    a ``ForeignKey`` call rather than a type — the ``Mapped[...]`` annotation,
    and for a foreign key the referenced column's own family.
    """
    tables: dict[str, dict[str, str]] = {}
    references: list[tuple[str, str, str, str]] = []

    for path in SRC.rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            tablename: str | None = None
            columns: dict[str, str] = {}
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                    target = stmt.targets[0]
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "__tablename__"
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str)
                    ):
                        tablename = stmt.value.value
                if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                    continue
                value = stmt.value
                if value is None or "mapped_column" not in ast.dump(value):
                    continue
                fallback = _annotation_declared(stmt)
                declared = ""
                if isinstance(value, ast.Call):
                    for argument in value.args:
                        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                            # Legacy SQLAlchemy form: the positional argument is
                            # the column NAME, not a type.
                            continue
                        if (
                            isinstance(argument, ast.Call)
                            and _base_name(getattr(argument.func, "id", getattr(argument.func, "attr", "")))
                            == "ForeignKey"
                        ):
                            target_table, target_column = _foreign_key_target(argument)
                            if target_table:
                                references.append((tablename or "", stmt.target.id, target_table, target_column))
                            continue
                        segment = ast.get_source_segment(source, argument)
                        if segment:
                            declared = " ".join(segment.split())
                        break
                columns[stmt.target.id] = declared or fallback or "?"
            if tablename and columns:
                tables.setdefault(tablename, {}).update(columns)

    # Second pass: a foreign key must resolve like the column it points at, or
    # every `*.user_id` compares as the text of a call against the migration's
    # integer and the family check means nothing.
    for table, column, target_table, target_column in references:
        target_family = _column_family(tables.get(target_table, {}).get(target_column, ""))
        if target_family is not None and _column_family(tables.get(table, {}).get(column, "")) is None:
            # Only the family is known here, and the family is all the
            # comparison uses, so name it with the family's canonical spelling.
            tables[table][column] = CANONICAL_BY_FAMILY[target_family]
    return tables


# One declared spelling per family, used when a column's type is known only
# through the column it references. Nothing downstream compares spellings.
CANONICAL_BY_FAMILY: dict[str, str] = {
    "int": "Integer",
    "string": "String",
    "bool": "Boolean",
    "decimal": "Numeric",
    "float": "Float",
    "uuid": "UUID",
    "json": "JSON",
    "datetime": "DateTime",
    "date": "Date",
    "time": "Time",
    "bytes": "LargeBinary",
}


def _foreign_key_target(argument: ast.Call) -> tuple[str, str]:
    """`ForeignKey("users.id")` -> ("users", "id"); a bare table gives ("t", "")."""
    for argument_node in argument.args:
        if isinstance(argument_node, ast.Constant) and isinstance(argument_node.value, str):
            table, _, column = argument_node.value.partition(".")
            return table, column
    return "", ""


def _migration_column_types() -> dict[str, dict[str, str]]:
    """table name -> column name -> declared type, from create_table/add_column.

    Parsed with ``ast`` rather than by splitting text on ``op.create_table(``:
    a migration may put a comment between two column declarations, and one
    comment containing an unbalanced bracket is enough to make a text scanner
    lose the rest of the table. That is not hypothetical — the comments
    recording ``FastAPI_saas-vthy`` sit inside layer 03's ``create_table`` call
    and did exactly that.
    """
    tables: dict[str, dict[str, str]] = {}
    for path in sorted(MIGRATIONS.glob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = getattr(callee, "attr", None) or getattr(callee, "id", None)
            if name == "create_table" and node.args:
                table_argument = node.args[0]
                if not (isinstance(table_argument, ast.Constant) and isinstance(table_argument.value, str)):
                    continue
                table = table_argument.value
                columns: dict[str, str] = {}
                for argument in node.args[1:]:
                    parsed = _column_from_source(argument, source)
                    if parsed:
                        columns[parsed[0]] = parsed[1]
                if columns:
                    tables.setdefault(table, {}).update(columns)
            elif name == "add_column" and len(node.args) >= 2:
                table_argument = node.args[0]
                if not (isinstance(table_argument, ast.Constant) and isinstance(table_argument.value, str)):
                    continue
                parsed = _column_from_source(node.args[1], source)
                if parsed:
                    tables.setdefault(table_argument.value, {})[parsed[0]] = parsed[1]
    return tables


def _column_from_source(node: ast.AST, source: str) -> tuple[str, str] | None:
    """(column name, declared type spelling) for one `Column(...)` argument."""
    if not isinstance(node, ast.Call):
        return None
    callee = node.func
    if (getattr(callee, "attr", None) or getattr(callee, "id", None)) != "Column" or not node.args:
        return None
    first = node.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return None
    if len(node.args) < 2:
        return first.value, ""
    segment = ast.get_source_segment(source, node.args[1])
    return first.value, " ".join(segment.split()) if segment else ""


def _model_tables() -> dict[str, set[str]]:
    """table name -> mapped column names.

    Derived from the type map so the two halves of this gate can never disagree
    about which columns exist: one parse, two questions asked of it.
    """
    return {table: set(columns) for table, columns in _model_column_types().items()}


def _migration_columns() -> dict[str, set[str]]:
    """table name -> columns, from op.create_table and op.add_column."""
    return {table: set(columns) for table, columns in _migration_column_types().items()}


def _type_drift(models: dict[str, dict[str, str]], migrations: dict[str, dict[str, str]]) -> list[str]:
    """Observable ORM/migration type differences, one line each.

    The key is ``table.column`` and everything after the first colon is
    explanation, so a caller can match a line against ``TYPE_ALLOWLIST``.
    """
    drift: list[str] = []
    for table in sorted(models):
        migration_columns = migrations.get(table, {})
        for column, orm_type in sorted(models[table].items()):
            migration_type = migration_columns.get(column)
            if migration_type is None:
                continue
            kind = _classify_type(orm_type, migration_type)
            if kind in DRIFT_KINDS:
                drift.append(f"{table}.{column}: orm={orm_type} migration={migration_type} ({kind})")
    return drift


def _unbounded_widths(models: dict[str, dict[str, str]], migrations: dict[str, dict[str, str]]) -> list[str]:
    """Columns the ORM leaves unbounded and the schema caps. Reported, not failed."""
    rows: list[str] = []
    for table in sorted(models):
        migration_columns = migrations.get(table, {})
        for column, orm_type in sorted(models[table].items()):
            migration_type = migration_columns.get(column)
            if migration_type is None:
                continue
            if _classify_type(orm_type, migration_type) == "unbounded_orm_width":
                rows.append(f"{table}.{column}: orm={orm_type} migration={migration_type}")
    return rows


def _key(line: str) -> tuple[str, str]:
    """``"table.column: orm=… "`` -> ("table", "column"), for TYPE_ALLOWLIST lookup."""
    table, _, column = line.split(":")[0].partition(".")
    return table, column


def main() -> int:
    if not MIGRATIONS.is_dir():
        print(f"check_schema_parity: {MIGRATIONS} not found", file=sys.stderr)
        return 2

    model_types = _model_column_types()
    migration_types = _migration_column_types()
    models = {table: set(columns) for table, columns in model_types.items()}
    migrations = {table: set(columns) for table, columns in migration_types.items()}

    if not models:
        print("check_schema_parity: no models parsed — refusing to pass vacuously", file=sys.stderr)
        return 2

    missing_drift: list[str] = []
    for table in sorted(models):
        if table not in migrations:
            missing_drift.append(f"{table}: model has no create_table/add_column in any migration")
            continue
        missing = sorted(models[table] - migrations[table])
        if missing:
            missing_drift.append(f"{table}: columns on the ORM but not in any migration: {missing}")

    type_drift = _type_drift(model_types, migration_types)
    unaccounted = [line for line in type_drift if _key(line) not in TYPE_ALLOWLIST]
    suppressed = [line for line in type_drift if _key(line) in TYPE_ALLOWLIST]
    unbounded = _unbounded_widths(model_types, migration_types)
    compared = sum(
        1 for table, columns in model_types.items() for column in columns if column in migration_types.get(table, {})
    )

    if missing_drift or unaccounted:
        print("check_schema_parity: ORM/migration drift detected", file=sys.stderr)
        for line in [*missing_drift, *unaccounted]:
            print(f"  {line}", file=sys.stderr)
        if missing_drift:
            print(
                "\nA migrated database (alembic upgrade head) will raise UndefinedColumnError for "
                "these columns, while a create_all database will not — so tests pass and production "
                "breaks. Add them to the owning migration layer "
                "(see src/migrations/AGENTS.md: extend the layer, do not stack a new revision).",
                file=sys.stderr,
            )
        if unaccounted:
            print(
                "\nA type difference is drift only where it is OBSERVABLE: a different SQL family "
                "(the driver hands back a different Python object than the ORM promised — the "
                "api_keys.scopes class, which 500'd every X-API-Key request on a migrated "
                "database), or a migration narrower than the ORM (the column rejects a value the "
                "ORM accepted). A wider migration, and a mere difference of spelling, are not "
                "drift. If this row is a known-benign finding, record it in TYPE_ALLOWLIST with "
                "its reason and the bead that owns it — an entry that no longer matches real drift "
                "is itself a failure.",
                file=sys.stderr,
            )
        return 1

    print(
        f"check_schema_parity: {len(models)} ORM tables match the migrations "
        f"({sum(len(c) for c in models.values())} columns)"
    )
    print(f"check_schema_parity: types compared on {compared} columns, no observable drift")
    if unbounded:
        print(
            f"check_schema_parity: {len(unbounded)} columns the ORM leaves unbounded and the "
            "schema caps. Not drift - `String` with no length states no width, so the column "
            "contradicts nothing - and listed so the caps stay deliberate:"
        )
        for line in unbounded:
            print(f"  {line}")
    for line in suppressed:
        print(f"  suppressed: {line}\n    {TYPE_ALLOWLIST[_key(line)]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
