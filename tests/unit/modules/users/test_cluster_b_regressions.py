"""Regression tests for Cluster B (Users) fixes.

B1 — email normalisation on the UPDATE path, and a case-insensitive
      duplicate check that matches the case-insensitive login lookup.
B2 — ``profile_image_url`` is nullable and carries NO placeholder default.
B4 — the generator template does not import a dead ``crud`` symbol.

Each test pins behaviour that WAS BROKEN before the fix and MUST stay
fixed. They assert behaviour, not source text, except where a source
property IS the subject (the template import, the ORM default).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

REPO = Path(__file__).resolve().parents[4]
SRC = REPO / "src"


# =============================================================================
# B1 — email normalisation: create and update must not diverge
# =============================================================================


class TestEmailNormalisationParity:
    """``normalize_email`` must exist on BOTH the create and update schemas."""

    def test_user_base_normalizes_email(self):
        """The create path (UserBase) has always normalized. Unchanged."""
        from src.app.modules.users.schemas import UserBase

        assert UserBase.normalize_email("  User@Example.COM ") == "user@example.com"

    def test_user_update_normalizes_email(self):
        """B1: the update path normalizes too — this is the fix."""
        from src.app.modules.users.schemas import UserUpdate

        values = UserUpdate(email="  Victim@X.COM  ")
        assert values.email == "victim@x.com"

    def test_user_update_internal_normalizes_email(self):
        """B1: the internal update schema normalizes too."""
        from src.app.modules.users.schemas import UserUpdateInternal

        values = UserUpdateInternal(email="  Victim@X.COM  ")
        assert values.email == "victim@x.com"

    def test_update_leaves_email_untouched_when_absent(self):
        """None must stay None — the validator is not a default."""
        from src.app.modules.users.schemas import UserUpdate

        assert UserUpdate().email is None
        assert UserUpdate(email=None).email is None

    def test_create_and_update_agree(self):
        """The invariant: the same input yields the same stored value."""
        from src.app.modules.users.schemas import UserCreate, UserUpdate

        raw = "  Mixed.Case@Example.COM "
        assert UserCreate(
            name="Userson", username="userson", email=raw, password="Str0ng!Passw0rd"
        ).email == UserUpdate(email=raw).email


class TestEmailDuplicateCheckIsCaseInsensitive:
    """The duplicate check must be at least as strict as the login lookup.

    The unique index ``uq_user_email_tenant`` is case-SENSITIVE, but the
    login lookup lowercases BOTH sides. An exact-match duplicate check
    therefore allowed a second row that the login lookup then matches
    twice — and ``scalar_one_or_none()`` raises ``MultipleResultsFound``,
    500-ing every login for that address.
    """

    async def _compile_check(self, service, method: str, **kwargs) -> str:
        captured: dict[str, Any] = {}
        result = MagicMock()
        result.scalar.return_value = None

        async def _execute(stmt, **_: object) -> MagicMock:
            captured["stmt"] = stmt
            return result

        with patch("src.app.modules.users.service.execute_scoped", side_effect=_execute):
            taken = await getattr(service, method)(**kwargs)

        assert taken is False
        stmt = captured["stmt"]
        return str(stmt.compile(dialect=postgresql.dialect()))

    @pytest.mark.asyncio
    async def test_email_check_compiles_a_case_insensitive_predicate(self):
        from src.app.modules.users.service import UserService

        sql = await self._compile_check(
            UserService(AsyncMock()), "_email_taken", email="Victim@X.com", tenant_id=uuid4()
        )
        assert "lower(users.email)" in sql, (
            "B1 regression: the email duplicate check is case-sensitive again"
        )

    @pytest.mark.asyncio
    async def test_email_check_excludes_the_row_being_updated(self):
        from src.app.modules.users.service import UserService

        sql = await self._compile_check(
            UserService(AsyncMock()),
            "_email_taken",
            email="victim@x.com",
            tenant_id=uuid4(),
            exclude_username="victim",
        )
        assert "users.username !=" in sql, (
            "B1 regression: the duplicate check no longer excludes the updated row"
        )

    @pytest.mark.asyncio
    async def test_username_check_is_case_insensitive_for_updates(self):
        from src.app.modules.users.service import UserService

        sql = await self._compile_check(
            UserService(AsyncMock()),
            "_username_taken",
            username="Alice",
            tenant_id=uuid4(),
            exclude_username="alice_old",
        )
        assert "lower(users.username)" in sql, (
            "B1 regression: the update-path username check is exact-match again"
        )
        assert "users.username !=" in sql

    @pytest.mark.asyncio
    async def test_login_lookup_is_case_insensitive(self):
        """The lookup the duplicate check must match lowercases both sides."""
        from src.app.modules.users.crud import crud_users

        db = AsyncMock()
        db.execute.return_value = MagicMock()
        await crud_users.get_user_with_tier_eager(
            db, email="Victim@X.com", tenant_id=uuid4()
        )

        stmt = db.execute.await_args.args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "lower(users.email)" in sql


# =============================================================================
# B2 — profile_image_url: nullable, no placeholder default
# =============================================================================


class TestProfileImageUrlIsNullable:
    def test_orm_column_is_nullable(self):
        """B2: the ORM column must be Optional and nullable."""
        from src.app.modules.users.models import User

        column = User.__table__.columns["profile_image_url"]
        assert column.nullable is True, "B2 regression: profile_image_url is NOT NULL again"

    def test_orm_column_has_no_placeholder_default(self):
        """B2: the ORM default must not write the placeholder URL."""
        from src.app.modules.users.models import User

        column = User.__table__.columns["profile_image_url"]
        default = getattr(column.default, "arg", None)
        assert default is None, f"B2 regression: ORM default is {default!r}"

    def test_internal_schema_has_no_placeholder_default(self):
        """B2: the internal User schema must not default to the placeholder."""
        from src.app.modules.users.schemas import User

        assert User.model_fields["profile_image_url"].default is None
        assert "profileimageurl.com" not in str(User.model_fields["profile_image_url"].default)

    def test_read_schema_allows_a_missing_avatar(self):
        """B2: a user with no avatar must validate."""
        from src.app.modules.users.schemas import UserReadPublic

        read = UserReadPublic(
            id=1,
            created_at="2026-01-01T00:00:00Z",
            name="Userson",
            username="userson",
            email="u@x.com",
            tier_id=None,
        )
        assert read.profile_image_url is None

    def test_read_schema_still_accepts_a_real_avatar(self):
        from src.app.modules.users.schemas import UserReadPublic

        read = UserReadPublic(
            id=1,
            created_at="2026-01-01T00:00:00Z",
            name="Userson",
            username="userson",
            email="u@x.com",
            tier_id=None,
            profile_image_url="https://cdn/x.png",
        )
        assert read.profile_image_url == "https://cdn/x.png"

    def test_the_placeholder_url_is_never_written_by_the_orm(self):
        """B2: the placeholder must not be a VALUE anywhere — only in prose.

        A comment or an ``examples=[...]`` doc value is fine (it is never
        stored); what must not exist is a default or a literal assigned to
        the column. So this asserts on the CONSTRUCTED default, not on text.
        """
        from src.app.modules.users.models import User

        column = User.__table__.columns["profile_image_url"]
        assert column.default is None
        assert column.server_default is None
        # The empty-string default would also be a silent placeholder.
        default = getattr(column.default, "arg", None)
        assert default in (None, None), f"B2 regression: default {default!r}"


class TestProfileImageUrlOriginLayer:
    def _users_columns(self) -> dict[str, Any]:
        """Run layer 01 against a mocked Alembic ``op`` and read the DDL."""
        import runpy

        import sqlalchemy as sa

        namespace = runpy.run_path(str(SRC / "migrations" / "versions" / "01_foundation.py"))
        op = MagicMock()
        namespace["upgrade"].__globals__["op"] = op
        namespace["upgrade"]()

        users_call = next(
            call for call in op.create_table.call_args_list if call.args and call.args[0] == "users"
        )
        return {
            column.name: column
            for column in users_call.args[1:]
            if isinstance(column, sa.Column)
        }

    def test_origin_layer_declares_the_column_nullable(self):
        """The layer that creates the table must declare the column nullable."""
        column = self._users_columns()["profile_image_url"]
        assert column.nullable is True, (
            "B2: 01_foundation must declare users.profile_image_url nullable"
        )

    def test_origin_layer_carries_no_placeholder_default(self):
        """No placeholder may survive as a schema default."""
        column = self._users_columns()["profile_image_url"]
        assert column.default is None
        assert column.server_default is None


# =============================================================================
# B4 — the generator template must not import a dead symbol
# =============================================================================


class TestGeneratorTemplateHasNoDeadCrudImport:
    def test_template_does_not_import_the_module_crud(self):
        """B4: the template imported ``crud_<module>`` and never used it."""
        path = SRC / "scripts" / "templates" / "service.py.j2"
        source = path.read_text(encoding="utf-8")
        assert "from src.app.modules.{{ module_name }}.crud import" not in source, (
            "B4 regression: the dead crud import is back in the template"
        )

    def test_template_still_renders_valid_python(self):
        """Removing the import must not break the generator.

        The check is on the rendered IMPORTS (not on the whole text): the
        docstring legitimately explains the removed import, but the module
        must not carry the symbol.
        """
        import sys

        sys.path.insert(0, str(SRC / "scripts"))
        import create_module as cm

        ctx = cm.ModuleContext(
            module_name="widget",
            model_name="Widget",
            table_name="widgets",
            fields=[],
            tenant_aware=True,
        )
        rendered = cm.render_template("service.py", ctx)
        tree = ast.parse(rendered)  # must parse

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.name)
        assert "crud_widget" not in imported, (
            "B4 regression: the rendered service still imports a dead crud symbol"
        )

    def test_base_service_builds_its_own_crud(self):
        """The reason the import was dead: BaseService builds ``self.crud``."""
        from src.app.modules._base.service import BaseService
        from src.app.modules.users.models import User

        service = BaseService(User, AsyncMock())

        assert service.model is User
        assert service.crud is not None
