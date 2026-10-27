"""Verify the indexes the hot query paths depend on actually exist.

Run against a live database with ``uv run python -m src.scripts.verify_indexes``.

Two groups are checked:

* RBAC lookups (``roles`` / ``role_permissions`` / ``user_roles``).
* PAGINATION seeks — the keyset composites and the tenant list indexes. These
  are the indexes whose absence is INVISIBLE in tests (a missing index still
  returns the right rows, just by scanning the whole table), so an operator
  needs a way to assert them on a deployed database.

The comparison lives in a pure function so it can be unit-tested without a
database (``tests/unit/scripts/test_verify_indexes.py``).
"""

import asyncio
from collections.abc import Mapping

from sqlalchemy import text

from src.app.core.config import settings

#: (table, index name, columns the index must lead with, in order)
EXPECTED_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # -- RBAC lookups -------------------------------------------------------
    ("roles", "ix_roles_tenant_id", ("tenant_id",)),
    ("role_permissions", "ix_role_permissions_permission_id", ("permission_id",)),
    ("user_roles", "ix_user_roles_tenant_id", ("tenant_id",)),
    ("user_roles", "ix_user_roles_role_id", ("role_id",)),
    # -- Keyset / list pagination ------------------------------------------
    # audit_logs: order matches query_logs / query_logs_with_total /
    # query_logs_keyset (ORDER BY created_at DESC, id DESC) — the id is the
    # tie-breaker that makes equal timestamps pageable.
    ("audit_logs", "idx_audit_logs_tenant_created_id", ("tenant_id", "created_at", "id")),
    ("audit_logs", "idx_audit_tenant_action", ("tenant_id", "action")),
    # posts: the composite behind get_posts_keyset. NOT idx_post_tenant_active
    # (tenant_id, created_at): that one cannot serve the keyset seek, because
    # get_posts_keyset filters (tenant_id, created_by_user_id, is_deleted) and
    # orders (created_at DESC, id DESC). Asserting the wrong index meant an
    # operator got a green SUCCESS while the seek was unindexed.
    (
        "post",
        "idx_posts_tenant_user_created_id",
        ("tenant_id", "created_by_user_id", "is_deleted", "created_at", "id"),
    ),
    # files: the unfiltered tenant list is `WHERE tenant_id = ? ORDER BY
    # created_at DESC` (modules/storage/service.py::list_files), served by the
    # ORM-declared idx_files_tenant_created. NOT idx_files_tenant_active, which
    # migration 08 creates as `(tenant_id, created_at DESC) WHERE is_deleted =
    # false`: the query never filters is_deleted, and `is_deleted` exists in the
    # DB but not on the ORM model, so that partial index cannot serve it.
    ("files", "idx_files_tenant_created", ("tenant_id", "created_at")),
)


def indexed_columns(indexdef: str) -> list[str]:
    """Column names in an ``indexdef``'s parenthesised list, sort order stripped.

    ``CREATE INDEX x ON public.t USING btree (tenant_id, created_at DESC, id
    DESC)`` yields ``["tenant_id", "created_at", "id"]``. ``DESC``/``ASC`` are
    dropped on purpose: PostgreSQL can scan an index backwards, so the sort
    order is not what makes a keyset seek possible — the COLUMN LIST is.
    """
    _, _, remainder = indexdef.partition("(")
    body, _, _ = remainder.rpartition(")")
    columns: list[str] = []
    for part in body.split(","):
        name = part.strip().split(" ")[0].strip('"').strip()
        if name:
            columns.append(name)
    return columns


def check_indexes(indexdefs: Mapping[tuple[str, str], str]) -> list[str]:
    """Return one problem string per missing or wrong index (empty == all good).

    A wrong LEADING column list is reported as a problem, not a pass: an index
    on ``(created_at, tenant_id)`` cannot serve a ``tenant_id = ?`` keyset seek,
    and looking it up by name alone would call that present.
    """
    problems: list[str] = []
    for table, index, columns in EXPECTED_INDEXES:
        definition = indexdefs.get((table, index))
        if definition is None:
            problems.append(f"missing: {index} on {table}")
            continue
        actual = indexed_columns(definition)
        if actual[: len(columns)] != list(columns):
            problems.append(f"wrong columns: {index} on {table} is {actual}, expected to lead with {list(columns)}")
    return problems


async def verify() -> int:
    """Return 0 when every expected index is present, 1 otherwise."""
    from sqlalchemy.ext.asyncio import create_async_engine

    # POSTGRES_URI is the ONE DSN builder. Do NOT interpolate the discrete
    # fields here: POSTGRES_PASSWORD is a SecretStr, whose __str__ is
    # "**********", so an f-string DSN connects with a literal mask and every
    # run fails authentication while printing a redacted URL — maximally
    # confusing to diagnose. POSTGRES_URI also quote_plus-escapes the password.
    # It returns credentials@location only, so the scheme prefix is still needed
    # — same composition as core/db/database.py:54.
    url = f"{settings.postgres.POSTGRES_ASYNC_PREFIX}{settings.postgres.POSTGRES_URI}"
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT tablename, indexname, indexdef FROM pg_indexes"))
            found = {(row[0], row[1]): row[2] for row in result.fetchall()}

            if not found:
                print("ERROR: no indexes visible — wrong database or empty schema")
                return 1

            problems = check_indexes(found)
            for problem in problems:
                print(f"ERROR: {problem}")
            if problems:
                return 1
            print(f"SUCCESS: all {len(EXPECTED_INDEXES)} expected indexes found.")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(verify()))


__all__ = ("EXPECTED_INDEXES", "check_indexes", "indexed_columns", "verify")
