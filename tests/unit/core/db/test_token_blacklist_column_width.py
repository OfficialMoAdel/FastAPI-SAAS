"""`token_blacklist.token` must be wide enough to hold a real token.

This file exists because of bead FastAPI_saas-hz4d. Layer 2 originally declared
the column as ``VARCHAR(500)`` while the ORM model declared an unbounded
``String``. Only the migration was enforced, so the two disagreed and the schema
was the one that bound. A production-shaped refresh token measures 540-564
characters, so ``/api/v1/account/logout`` failed for every user with
``StringDataRightTruncationError`` -> a ``sqlalchemy.exc.DataError``, which is a
sibling of ``IntegrityError`` and so was not caught by the ``except
IntegrityError`` at ``jwt_service.py:575``.

The fix was folded INTO layer 2 (it now declares ``sa.Text()``) rather than
stacked as a later revision, so this test points at the origin layer. A separate
``09_widen_token_blacklist_token`` revision existed briefly and was folded in once
the schema was rebuilt from scratch.

WHY THIS FILE CHECKS THE MIGRATIONS AND NOT A LIVE TABLE
The regression cannot be caught by inserting a long token into a test database.
``tests/conftest.py::real_postgres_engine`` builds its schema with
``Base.metadata.create_all``, i.e. from the ORM models — and the model was ALWAYS
unbounded. Such a test would have passed with ``VARCHAR(500)`` in place and would
still pass if the two drift apart again. The defect lived exclusively in the
migration, so the assertion has to read the migration.

``tests/performance/test_rollback_verification.py`` cannot cover this either: it
runs on SQLite (``DIALECTS = ("sqlite",)``) and SQLite does not enforce VARCHAR
length at all.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from src.app.core.db.token_blacklist import TokenBlacklist

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

VERSIONS_DIR = Path(__file__).resolve().parents[4] / "src" / "migrations" / "versions"

#: The layer that CREATES ``token_blacklist``, and therefore the only place its
#: width can be declared. The fix was folded into this origin layer rather than
#: stacked as a later revision: there is no already-migrated database to lie
#: about once the schema is rebuilt from scratch, and ``src/migrations/AGENTS.md``
#: asks for exactly this — "extend the relevant layer's ``upgrade()``" unless the
#: layer is already applied in production.
ORIGIN_LAYER = "02_authentication"


def _type_length(col_call: ast.Call) -> int | None:
    """The ``length=`` the TYPE CONSTRUCTOR of this column declares, if any.

    This is the spelling that matters and the one the original helper missed.
    ``sa.Column("token", sa.String(length=500), nullable=False)`` carries the
    length on the INNER ``sa.String(...)`` call -- the second positional
    argument -- and not as a keyword of ``Column``. The first draft read only
    ``col_call.keywords``, so it saw no ``length`` anywhere, reported ``[None]``,
    and PASSED with the cap restored. That is what a regression guard looks like
    when it is quietly asserting nothing, so the type constructor is scanned
    explicitly: it can be the second positional argument, or a keyword such as
    ``type_=``.
    """
    candidates: list[ast.expr] = []
    if len(col_call.args) >= 2:
        candidates.append(col_call.args[1])
    for kw in col_call.keywords:
        if kw.arg in {"type_", "existing_type"}:
            candidates.append(kw.value)

    for expr in candidates:
        if not isinstance(expr, ast.Call):
            continue
        for kw in expr.keywords:
            if kw.arg != "length" or not isinstance(kw.value, ast.Constant):
                continue
            if isinstance(kw.value.value, int):
                return kw.value.value
    return None


def _inside_create_table(upgrade: ast.FunctionDef, column_call: ast.Call, table: str) -> bool:
    """Is ``column_call`` an argument of ``create_table(table, ...)``?"""
    for call in ast.walk(upgrade):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "create_table":
            continue
        if not call.args or not isinstance(call.args[0], ast.Constant):
            continue
        if call.args[0].value != table:
            continue
        if column_call in call.args:
            return True
    return False


def _declared_lengths(path: Path) -> list[int | None]:
    """Every explicit length ``upgrade()`` declares for ``token_blacklist.token``.

    Returns ``None`` for a declaration with no ``length=`` at all, which is how
    an unbounded type is spelled. Walks both spellings that can declare the
    column: ``create_table("token_blacklist", sa.Column("token", ...))`` and
    ``alter_column("token_blacklist", "token", ...)``.

    ONLY ``upgrade()`` IS SCANNED, and that is the whole point. ``downgrade()`` in
    this revision restores ``sa.String(length=500)``, which is exactly what a
    downgrade is supposed to do — it puts the old schema back. Reading it would
    report a cap on a column the head never creates, and would fire on every
    correct rollback. ``existing_type=`` is skipped for the same reason: it
    documents what the column WAS, not what the migration leaves it as.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[int | None] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "upgrade":
            continue

        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue

            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)

            if name == "Column":
                # Only a column declared INSIDE create_table("token_blacklist").
                # Layer 2 creates four tables that each have a column literally
                # named "token" (magic_links, password_reset_tokens,
                # email_verification_tokens, sso_settings), so matching on the
                # column name alone reports four results and asserts nothing.
                args = call.args
                if not args or not isinstance(args[0], ast.Constant) or args[0].value != "token":
                    continue
                if not _inside_create_table(node, call, "token_blacklist"):
                    continue
                found.append(_type_length(call))

            elif name == "alter_column":
                args = call.args
                if len(args) < 2:
                    continue
                table, column = args[0], args[1]
                if not (isinstance(table, ast.Constant) and table.value == "token_blacklist"):
                    continue
                if not (isinstance(column, ast.Constant) and column.value == "token"):
                    continue
                # type_=... is the target type; existing_type=... is what it was.
                for kw in call.keywords:
                    if kw.arg != "type_":
                        continue
                    length = None
                    if isinstance(kw.value, ast.Call):
                        for kw2 in kw.value.keywords:
                            if kw2.arg != "length" or not isinstance(kw2.value, ast.Constant):
                                continue
                            if isinstance(kw2.value.value, int):
                                length = kw2.value.value
                    found.append(length)

    return found


class TestTokenColumnIsNotCapped:
    def test_the_model_declares_no_length(self) -> None:
        """The ORM column must stay unbounded.

        This is the half of the original drift that was already correct and is
        asserted so it cannot be tightened back into a cap.
        """
        assert getattr(TokenBlacklist.__table__.c.token.type, "length", None) is None

    def test_the_origin_layer_leaves_it_unbounded(self) -> None:
        """The creating layer must leave the column unbounded.

        Regression guard for bead FastAPI_saas-hz4d: this is the assertion that
        fails if someone re-adds a cap to the layer. The longest refresh token
        measured on perf-local against ``TokenIssuer.create_token_pair`` was 564
        characters (``sub`` of 20 chars) and the length drifts with the digits in
        ``iat``/``jti``. Nothing here justifies a ceiling — a JWT's length is a
        function of its claims — so the assertion is that there is NO ceiling. A
        future cap would have to be justified in this file, not added silently.
        """
        path = VERSIONS_DIR / f"{ORIGIN_LAYER}.py"
        assert path.is_file(), f"the origin layer {path.name} is missing"
        assert _declared_lengths(path) == [None], (
            "the origin layer must leave token_blacklist.token unbounded; "
            f"declared lengths: {_declared_lengths(path)}"
        )


class TestProductionShapedPairRoundTrips:
    """The behavioural half of bead FastAPI_saas-hz4d's acceptance.

    Be explicit about what this covers, because it is NOT the guard that
    catches the original defect. That one lives above: the ``VARCHAR(500)``
    cap existed only in the migration, so the ORM (from which every test
    database is built) never carried it and a behavioural test would have
    passed with the bug in place. What this DOES pin down is the live path
    itself — that ``blacklist_tokens`` stages and flushes a pair shaped like
    production's, including the raw token AND the ``jti:`` row, without a
    ``DataError``. If a future change re-adds a length to the model, or the
    insert path starts truncating, this fails.
    """

    @pytest.mark.asyncio
    async def test_a_production_shaped_pair_blacklists_without_a_data_error(
        self, real_postgres: AsyncSession
    ) -> None:
        # ``token_blacklist.tenant_id`` carries an FK to ``tenants.id``
        # (``02_authentication.py``: the ForeignKeyConstraint on
        # ``["tenant_id"], ["tenants.id"]``, ondelete CASCADE), so the tenant
        # the claims name has to actually exist or the flush raises an
        # IntegrityError -- and ``blacklist_tokens`` swallows ANY IntegrityError
        # as "already blacklisted". Seeded here rather than assumed, because
        # ``real_postgres_engine`` builds its schema with
        # ``Base.metadata.create_all`` and is otherwise empty of tenants. This
        # is the failure this test hit first: got 0 rows, not because the column
        # truncated, but because an FK violation discarded the savepoint.
        from sqlalchemy import text

        from src.app.core.security.jwt_service import (
            TokenType,
            blacklist_tokens,
            create_access_token,
            create_refresh_token,
        )

        await real_postgres.execute(
            text(
                """
                insert into tenants (id, name, slug, is_active, feature_flags, created_at, updated_at)
                values (:id, :name, :slug, true, '{}'::jsonb, now(), now())
                on conflict (id) do nothing
                """
            ),
            {"id": "00000000-0000-0000-0000-000000000000", "name": "token-width-test", "slug": "token-width-test"},
        )

        claims = {
            "sub": "s4x0000",
            "tenant_id": "00000000-0000-0000-0000-000000000000",
            "type": TokenType.ACCESS,
        }
        refresh_claims = {**claims, "type": TokenType.REFRESH}
        access_token = await create_access_token(claims)
        refresh_token = await create_refresh_token(refresh_claims)

        # The measured lengths that motivated the bead. Asserted here so the
        # test fails loudly if token shape ever stops being the shape that
        # broke logout, rather than silently testing a shorter token.
        assert len(access_token) > 400
        assert len(refresh_token) > 500, (
            f"the refresh token is only {len(refresh_token)} chars; this test no "
            "longer exercises the length that broke logout"
        )

        await blacklist_tokens(access_token, refresh_token, real_postgres)
        await real_postgres.commit()

        from sqlalchemy import select

        rows = (
            await real_postgres.execute(
                select(TokenBlacklist.token).where(
                    TokenBlacklist.token.in_([access_token, refresh_token])
                )
            )
        ).scalars().all()
        assert len(rows) == 2, f"both raw tokens should be stored, got {len(rows)}"


import sqlite3

from sqlalchemy.exc import IntegrityError

from src.app.core.security.jwt_service import _is_duplicate_blacklist_violation


class TestTheDuplicateDiscriminatorIsDialectBlind:
    """The duplicate-vs-real-fault predicate must work on every dialect we run.

    This predicate exists because catching ``IntegrityError`` wholesale cannot
    tell a repeat logout from a real constraint fault. Its first version read
    ``orig.sqlstate`` and nothing else, which is a PostgreSQL-only signal:
    asyncpg sets it and 23505 is the unique-violation code, but ``sqlite3`` has
    no SQLSTATE at all. On SQLite a genuine duplicate therefore classified as a
    real fault, the handler rolled back and RAISED, and a REPEAT LOGOUT STOPPED
    BEING IDEMPOTENT. That regression shipped.

    It survived the tests written alongside it because those ran on Postgres,
    while ``tests/performance/test_rollback_verification.py`` runs on SQLite --
    so a guard that only ever ran against one dialect had no evidence about the
    other, which is precisely the gap these cases close.

    Both dialects are asserted HERE, against the predicate directly, so neither
    can regress without one of these going red. The shapes are constructed
    rather than driven, because driving them needs a live database on each
    dialect and a test that only runs where a database happens to exist is how
    this hole opened.
    """

    @staticmethod
    def _exc(orig: BaseException | None) -> IntegrityError:
        return IntegrityError("statement failed", params=None, orig=orig)  # type: ignore[arg-type]

    class _AsyncpgUnique(Exception):
        """What asyncpg hands back: a driver error carrying a SQLSTATE."""

        sqlstate = "23505"

    class _AsyncpgForeignKey(Exception):
        sqlstate = "23503"

    def test_a_postgres_unique_violation_is_the_duplicate(self) -> None:
        assert _is_duplicate_blacklist_violation(self._exc(self._AsyncpgUnique()))

    def test_a_postgres_foreign_key_violation_is_not(self) -> None:
        assert not _is_duplicate_blacklist_violation(self._exc(self._AsyncpgForeignKey()))

    def test_a_sqlite_unique_violation_is_the_duplicate(self) -> None:
        """The regression itself: sqlite3 exposes no sqlstate at all."""
        assert _is_duplicate_blacklist_violation(
            self._exc(sqlite3.IntegrityError("UNIQUE constraint failed: token_blacklist.token"))
        )

    def test_a_sqlite_foreign_key_violation_is_not(self) -> None:
        assert not _is_duplicate_blacklist_violation(
            self._exc(sqlite3.IntegrityError("FOREIGN KEY constraint failed"))
        )

    def test_a_sqlite_not_null_violation_is_not(self) -> None:
        assert not _is_duplicate_blacklist_violation(
            self._exc(sqlite3.IntegrityError("NOT NULL constraint failed: token_blacklist.tenant_id"))
        )

    def test_a_driver_error_with_no_usable_detail_is_not_the_duplicate(self) -> None:
        """Fails SAFE: an unrecognisable failure must be treated as real."""
        assert not _is_duplicate_blacklist_violation(self._exc(sqlite3.OperationalError("locked")))

    def test_no_orig_at_all_is_not_the_duplicate(self) -> None:
        assert not _is_duplicate_blacklist_violation(self._exc(None))


class TestANonDuplicateConstraintFailureIsNotSwallowed:
    """A constraint failure that is NOT the duplicate-token index must surface.

    ``IntegrityError`` is the SQLAlchemy base for every constraint violation,
    so ``except IntegrityError`` at the flush could not tell an idempotent
    repeat logout from an FK violation. Measured (bead FastAPI_saas-68yg): a
    production-shaped pair whose claims named a tenant absent from the
    database produced ``got 0 rows`` and the log "Token already blacklisted in
    DB", because the FK rolled back the savepoint and the handler called it a
    duplicate. The Redis write still happened, so revocation worked and
    nothing else noticed — which is exactly how this survived.
    """

    @pytest.fixture(autouse=True)
    def _no_redis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Isolate the DB path; the Redis write is not what is under test."""
        import src.app.core.security.jwt_service as service

        async def _absorb(_tokens: object) -> None:
            return None

        monkeypatch.setattr(service, "add_tokens_to_blacklist", _absorb)

    @staticmethod
    async def _drop_tenant(real_postgres: AsyncSession, tenant_id: str) -> None:
        """Guarantee the FK dangles, so the violation cannot be avoided.

        ``token_blacklist.tenant_id`` carries ``ondelete="CASCADE"``, so
        deleting the tenant also clears its rows and leaves nothing that could
        satisfy the constraint.
        """
        from sqlalchemy import text

        await real_postgres.execute(text("delete from tenants where id = :t"), {"t": tenant_id})
        await real_postgres.commit()

    @pytest.mark.asyncio
    async def test_a_dangling_tenant_fk_raises_instead_of_being_swallowed(
        self, real_postgres: AsyncSession
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        from src.app.core.security.jwt_service import (
            TokenType,
            blacklist_tokens,
            create_access_token,
            create_refresh_token,
        )

        tenant_id = "00000000-0000-0000-0000-000000000000"
        await self._drop_tenant(real_postgres, tenant_id)

        claims = {
            "sub": "s4x0000",
            "tenant_id": tenant_id,
            "type": TokenType.ACCESS,
        }
        refresh_claims = {**claims, "type": TokenType.REFRESH}
        access_token = await create_access_token(claims)
        refresh_token = await create_refresh_token(refresh_claims)

        # The tokens are production-shaped, so if this ever stops raising it
        # means the swallow came back, not that the fixture changed.
        assert len(access_token) > 400
        assert len(refresh_token) > 500
        with pytest.raises(IntegrityError):
            await blacklist_tokens(access_token, refresh_token, real_postgres)

    @pytest.mark.asyncio
    async def test_a_repeat_logout_is_still_idempotent(self, real_postgres: AsyncSession) -> None:
        """The case the handler legitimately exists for must still be quiet.

        Narrowing the except must not have turned a repeat logout into a
        failure: the second call hits the unique index on
        ``token_blacklist.token`` (SQLSTATE 23505) and must return cleanly.
        """
        from sqlalchemy import text

        from src.app.core.security.jwt_service import (
            TokenType,
            blacklist_tokens,
            create_access_token,
            create_refresh_token,
        )

        tenant_id = "00000000-0000-0000-0000-000000000000"
        await real_postgres.execute(
            text(
                """
                insert into tenants (id, name, slug, is_active, feature_flags, created_at, updated_at)
                values (:id, :name, :slug, true, '{}'::jsonb, now(), now())
                on conflict (id) do nothing
                """
            ),
            {"id": tenant_id, "name": "token-width-test", "slug": "token-width-test"},
        )
        await real_postgres.commit()

        claims = {
            "sub": "s4x0000",
            "tenant_id": tenant_id,
            "type": TokenType.ACCESS,
        }
        refresh_claims = {**claims, "type": TokenType.REFRESH}
        access_token = await create_access_token(claims)
        refresh_token = await create_refresh_token(refresh_claims)

        await blacklist_tokens(access_token, refresh_token, real_postgres)
        # A second logout with the same pair must NOT raise.
        await blacklist_tokens(access_token, refresh_token, real_postgres)

        # Scope the count to THIS pair, not the whole tenant: the fixture does
        # not truncate between tests and both a raw row and a ``jti:<jti>`` row
        # are staged per token, so a tenant-wide count also collects rows
        # written by other tests in this file.
        rows = (
            await real_postgres.execute(
                text("select count(*) from token_blacklist where token in (:a, :r)"),
                {"a": access_token, "r": refresh_token},
            )
        ).scalar_one()
        assert rows == 2, f"the pair should be stored exactly once each, got {rows}"
