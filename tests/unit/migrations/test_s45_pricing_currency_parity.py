"""S-45 — every ORM-mapped column must exist in the schema migrations build.

`Pricing.currency` is declared on the ORM (`billing/models.py`, `String(3)`,
`nullable=False`, default `"usd"`) and is READ by `billing/service.py:189`, but
migration 04 never created it. On any database built from migrations that made
every `Pricing` read raise `UndefinedColumnError` — a hard failure, not a
silent default. Reproduced against the live schema before the fix.

The test pins the invariant that would have caught it: every column the ORM maps
must be created by the migration. Pure source-level parity — no database needed.

That invariant is the argument for `alembic check` in CI: `create_all` derives
the schema FROM the ORM, so it can never detect this class; only a comparison
against the migrations can.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

MODEL = Path("src/app/modules/billing/models.py")
MIGRATION = Path("src/migrations/versions/04_billing.py")

COLUMN_RE = re.compile(r'sa\.Column\(\s*"([a-z_]+)"(.*)')
TABLE_NAME_RE = re.compile(r'"([a-z_]+)"')


def _migration_tables() -> dict[str, dict[str, str]]:
    """Every create_table block in the migration, as table -> {column: decl}.

    Split on `op.create_table(` rather than matching nested parens, so it does
    not depend on the file's exact indentation.
    """
    src = MIGRATION.read_text(encoding="utf-8")
    tables: dict[str, dict[str, str]] = {}
    for chunk in src.split("op.create_table(")[1:]:
        name_match = TABLE_NAME_RE.search(chunk)
        if not name_match:
            continue
        # Capture the WHOLE sa.Column(...) declaration (the migration writes
        # one per line), so nullable / server_default are assertable.
        tables[name_match.group(1)] = {c.group(1): c.group(2) for c in COLUMN_RE.finditer(chunk)}
    return tables


def _orm_columns(cls_name: str) -> set[str]:
    """Names assigned via ``mapped_column(...)`` only.

    A naive read of every annotated assignment also collects RELATIONSHIPS
    (``tenant``, ``user``, ``product``, ``user_subscription``), which are not
    columns and would produce false "missing from migration" reports.
    """
    tree = ast.parse(MODEL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            cols: set[str] = set()
            for stmt in node.body:
                if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                    continue
                value = stmt.value
                is_mapped = isinstance(value, ast.Call) and "mapped_column" in ast.dump(value.func)
                # A plain annotation with no value is a relationship
                # (`tenant: Mapped[Tenant] = relationship()`).
                if is_mapped or value is None and "relationship" in ast.dump(stmt):
                    if is_mapped:
                        cols.add(stmt.target.id)
            return cols
    raise AssertionError(f"class {cls_name} not found in {MODEL}")


@pytest.mark.parametrize(
    ("cls", "table"),
    [
        ("Pricing", "pricing"),
        ("SubscriptionPlan", "subscription_plans"),
        ("BillingRecord", "billing_records"),
    ],
)
def test_orm_columns_exist_in_migration(cls: str, table: str) -> None:
    """A column on the ORM but not in the migration is a hard read failure."""
    missing = sorted(_orm_columns(cls) - set(_migration_tables()[table]))
    assert not missing, (
        f"{cls} maps columns absent from migration {table}: {missing}. "
        "On a migration-built database every read raises UndefinedColumnError."
    )


def test_pricing_currency_is_read_by_the_service() -> None:
    """Why it matters: the column is not decorative — it is SELECTed."""
    svc = Path("src/app/modules/billing/service.py").read_text(encoding="utf-8")
    assert "pricing.currency" in svc


def test_currency_is_not_nullable_and_has_a_server_default() -> None:
    """nullable=False + a server default, so existing rows migrate cleanly."""
    decl = _migration_tables()["pricing"].get("currency")
    assert decl is not None, "pricing.currency is missing from the migration"
    assert "String(length=3)" in decl, decl
    assert "nullable=False" in decl, decl
    assert "server_default" in decl, "existing pricing rows need a backfilled default"


def test_all_three_billing_tables_carry_currency() -> None:
    """The asymmetry that hid this: two of three tables had it, one did not."""
    tables = _migration_tables()
    for table in ("pricing", "subscription_plans", "billing_records"):
        assert "currency" in tables[table], f"{table} must carry currency"
