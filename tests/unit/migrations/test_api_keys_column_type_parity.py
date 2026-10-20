"""api_keys column TYPES must agree between the ORM and the migrations (FastAPI_saas-vthy).

`api_keys.scopes` is `Text` on the ORM (`api_keys/models.py:55`) and
`api_keys.key_prefix` is `String(8)`, but migration layer 03 created
`scopes` as `postgresql.JSONB` and `key_prefix` as `String(10)`. On any
database built by `alembic upgrade head` the schema therefore disagreed with the
model — and nothing noticed, because the repository's blocking gate
(`scripts/checks/schema_parity.py`) compares column NAMES only, and the CI
`alembic check` that does see types is `continue-on-error: true`.

MEASURED, not assumed (perf-local, a scratch database built by
`alembic upgrade 03_access_control`, against the `create_all` database as a
control, four arms: before/after this fix x migrated/control):

  * the drift is real - the migrated database had `scopes` jsonb and
    `key_prefix` varchar(10) against the model's Text and String(8);
  * the INSERT is accepted either way: asyncpg sends untyped parameters and the
    server coerces the literal, which is why the bead's "every
    POST /api/v1/api-keys 500s" claim does NOT reproduce;
  * the READ is where it breaks. SQLAlchemy's asyncpg dialect decodes a jsonb
    column to a Python `list`, so `APIKey.scopes` arrives as `["read"]` instead
    of `'["read"]'`, and `json_loads(key.scopes)` (`service.py:652`, unguarded)
    raises `JSONDecodeError: Input must be bytes, bytearray, memoryview, or str`
    - so on a migrated database EVERY `X-API-Key` request fails with 500, not
    just minting. The raw asyncpg read is not the application path and hides
    this, which is why the measurement has to go through the ORM;
  * `key_prefix` is cosmetic: an 8-character value reads back 8 characters
    either way.

So the fix is not cosmetic tidiness: it repairs API-key authentication on any
database built by `alembic upgrade head`. Nothing in the application queries
scopes as JSON, so jsonb bought nothing.

This test pins the invariant that would have caught it: for these two columns the
migration must declare the same type (and the same width) the ORM declares. Pure
source-level parity, in the shape of `test_s45_pricing_currency_parity.py`; no
database is needed to run it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

MODEL = Path("src/app/modules/api_keys/models.py")
SERVICE = Path("src/app/modules/api_keys/service.py")
MIGRATION = Path("src/migrations/versions/03_access_control.py")

COLUMN_START_RE = re.compile(r'sa\.Column\(\s*"([a-z_]+)"')
LENGTH_RE = re.compile(r"length\s*=\s*(\d+)")
KEY_PREFIX_LENGTH_RE = re.compile(r"KEY_PREFIX_LENGTH\s*(?::[^=]+)?=\s*(\d+)")


def _api_keys_block() -> str:
    """The `op.create_table("api_keys", ...)` chunk of migration 03.

    Split on `op.create_table(` rather than matching nested parens, so it does
    not depend on the file's indentation, then stop at the next table so a
    neighbouring create_table cannot be mistaken for part of this one.
    """
    chunks = MIGRATION.read_text(encoding="utf-8").split("op.create_table(")[1:]
    for chunk in chunks:
        if chunk.lstrip().startswith('"api_keys"'):
            return chunk
    raise AssertionError("api_keys create_table not found in 03_access_control.py")


def _declaration(column: str) -> str:
    """The whole `sa.Column("<column>", ...)` text, parens balanced.

    Balanced rather than regex-to-comma because the declared types contain their
    own parentheses (`sa.String(length=8)`, `postgresql.JSONB(astext_type=...)`).
    """
    block = _api_keys_block()
    match = re.search(rf'sa\.Column\(\s*"{column}"', block)
    if match is None:
        raise AssertionError(f"api_keys.{column} is not created by 03_access_control.py")
    depth = 0
    for index in range(match.start(), len(block)):
        char = block[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return block[match.start() : index + 1]
    raise AssertionError(f"unbalanced sa.Column(...) for api_keys.{column}")


def _orm_declaration(column: str) -> str | None:
    """The ORM `mapped_column(...)` text for one column, or None."""
    tree = ast.parse(MODEL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            if stmt.target.id == column and stmt.value is not None:
                return ast.unparse(stmt.value)
    return None


def _migration_length(column: str) -> int | None:
    match = LENGTH_RE.search(_declaration(column))
    return int(match.group(1)) if match else None


def test_the_api_keys_table_is_in_the_migration_layer_that_creates_it() -> None:
    """The premise: if api_keys is not in 03 at all, every other test here is vacuous."""
    assert '"api_keys"' in _api_keys_block()


def test_scopes_is_text_in_the_migration_and_text_on_the_model() -> None:
    """The drift: JSONB here, Text on the ORM."""
    assert "Text" in (_orm_declaration("scopes") or ""), _orm_declaration("scopes")
    declaration = _declaration("scopes")
    assert "JSONB" not in declaration, f"api_keys.scopes is still declared as JSONB: {declaration}"
    assert "sa.Text()" in declaration, declaration


def test_key_prefix_width_is_identical_on_both_sides() -> None:
    """String(8) on the ORM, String(length=10) in migration 03."""
    assert "String(8)" in (_orm_declaration("key_prefix") or ""), _orm_declaration("key_prefix")
    assert _migration_length("key_prefix") == 8, _declaration("key_prefix")


def test_the_migration_prefix_width_follows_the_code() -> None:
    """The real invariant: constant -> model -> migration.

    `KEY_PREFIX_LENGTH` in the service is what actually generates a prefix;
    the model's `String(8)` and the migration's width must both equal it.
    Hard-coding 8 in three places is what let them drift apart silently.
    """
    constant = KEY_PREFIX_LENGTH_RE.search(SERVICE.read_text(encoding="utf-8"))
    assert constant is not None, "KEY_PREFIX_LENGTH disappeared from api_keys/service.py"
    width = int(constant.group(1))
    assert f"String({width})" in (_orm_declaration("key_prefix") or ""), _orm_declaration("key_prefix")
    assert _migration_length("key_prefix") == width, (
        f"migration says key_prefix String(length={_migration_length('key_prefix')}) "
        f"while the code generates KEY_PREFIX_LENGTH={width}"
    )


def test_no_json_column_survives_in_the_api_keys_table() -> None:
    """Pins the mechanism that hid this: a dialect type nothing in the ORM uses."""
    for match in COLUMN_START_RE.finditer(_api_keys_block()):
        declaration = _declaration(match.group(1))
        assert "JSON" not in declaration, f"api_keys.{match.group(1)} declares a JSON type: {declaration}"


def test_scopes_is_written_and_read_as_a_string_so_text_is_the_correct_target() -> None:
    """Why Text and not JSONB is the fix direction rather than a preference.

    If the service ever stops being string-shaped, this test fails and the
    column type becomes a decision again instead of an accident.
    """
    service = SERVICE.read_text(encoding="utf-8")
    assert "json_dumps(api_key_data.scopes, as_str=True)" in service, (
        "create_api_key no longer stores scopes as a string"
    )
    assert '"scopes": json_loads(key.scopes)' in service, "validation no longer parses scopes with json_loads"


def test_the_read_path_only_accepts_the_string_shape_jsonb_would_break() -> None:
    """The measured reason the column type is load-bearing, not cosmetic.

    `json_loads` rejects a list, and SQLAlchemy's asyncpg dialect hands back a
    list for a jsonb column. That pair is the whole failure: a migrated
    database made every `X-API-Key` request raise `JSONDecodeError`. Asserting
    it here means the column-type tests above have a stated consequence rather
    than a preference, and it fails loudly if someone makes json_loads tolerant
    (which would move the decision, not settle it).
    """
    from src.app.core.json_utils import json_loads

    assert json_loads('["read"]') == ["read"]
    # Typed as Any on purpose: passing a list is what the annotation forbids and
    # what a jsonb column would deliver, so the call below must be made anyway.
    jsonb_shaped_value: Any = ["read"]
    with pytest.raises(ValueError):
        json_loads(jsonb_shaped_value)
